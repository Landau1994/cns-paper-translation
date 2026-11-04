#!/usr/bin/env python3
"""Read-only PDF checks for a translated journal article (Python 3.10+, pypdf).

Example:
  python audit_pdf.py final.pdf --forbidden-text "Downloaded from" \
      --expected-ref-count 79 --ref-prefix ref: --tex-log final.log \
      --output audit.json

Only an explicitly requested JSON report is written. PDF input is never saved,
rewritten, cropped, or sanitized. Forbidden strings are literal, not regexes;
matching uses NFKC, case folding, and collapsed whitespace. No default watermark
pattern is supplied. In particular, scientific decimal values must not be
mistaken for IP addresses by a broad default regular expression.

This is not OCR, translation verification, visual QA, or proof that an unknown
watermark is absent. Image payload inspection cannot read rendered image text.
Findings deliberately exclude matched text and full personal download details.
Exit codes: 0 = selected checks pass, 1 = findings, 2 = incomplete/error.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import unicodedata
import warnings

try:
    from pypdf import PdfReader
    from pypdf.generic import (
        ArrayObject, ByteStringObject, ContentStream, DictionaryObject,
        IndirectObject, NameObject, NumberObject, StreamObject, TextStringObject,
    )
except ImportError:
    PdfReader = None


LIMITATIONS = [
    "Only the explicitly supplied literal forbidden strings are checked; unknown watermarks may remain.",
    "This tool does not perform OCR and cannot establish that text inside all figures is translated or watermark-free.",
    "Page extraction and decoded content/string inspection may miss text with unusual glyph encodings or obfuscation.",
    "Resource scanning deliberately includes nested and unused XObjects and ignores clipping/crop visibility.",
    "Decoded image payloads are inspected as bytes, not visually interpreted as pixels.",
    "Internal PDF destinations are checked; external URLs and remote-document destinations are not fetched or verified.",
    "Match counts describe individual evidence views, not a count of unique visible watermarks.",
]


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class CountLogs(logging.Handler):
    """Count library diagnostics without formatting their potentially private text."""
    def __init__(self):
        super().__init__(logging.WARNING)
        self.counts = Counter()

    def emit(self, record):
        self.counts[record.levelname] += 1


def object_value(value):
    return value.get_object() if isinstance(value, IndirectObject) else value


def object_key(value):
    if isinstance(value, IndirectObject):
        return ("indirect", value.idnum, value.generation)
    reference = getattr(value, "indirect_reference", None)
    if reference is not None:
        return ("indirect", reference.idnum, reference.generation)
    return ("direct", id(value))


def byte_views(data: bytes):
    """Different PDF string encodings; none of these interpretations is OCR."""
    yield data.decode("utf-8", errors="replace")
    yield data.decode("latin-1")
    if data.startswith((b"\xfe\xff", b"\xff\xfe")):
        yield data.decode("utf-16", errors="replace")


class Audit:
    def __init__(self, reader, rules):
        self.reader = reader
        self.rules = [normalized(rule) for rule in rules]
        self.matches = {}
        self.errors = []
        self.counts = Counter()
        self.seen_xobjects = set()
        self.named = {}
        self.destination_failures = []
        self.page_keys = {object_key(page): index for index, page in enumerate(reader.pages)}

    def error(self, location, operation, exception):
        # Exception text can itself contain PDF text, URLs, names or raw bytes.
        self.errors.append({"location": location, "operation": operation,
                            "error_type": type(exception).__name__})

    def inspect_text(self, text, location):
        value = normalized(text)
        for index, rule in enumerate(self.rules, 1):
            count = value.count(rule)
            if count:
                key = (index, location)
                # Multiple encoding views of one stream must not inflate counts.
                self.matches[key] = max(self.matches.get(key, 0), count)

    def inspect_bytes(self, data, location):
        for view in byte_views(data):
            self.inspect_text(view, location)

    def strings(self, value, seen=None):
        """Strings in operands/metadata, with recursion and cycle protection."""
        if seen is None:
            seen = set()
        key = object_key(value)
        if key in seen:
            return
        seen.add(key)
        value = object_value(value)
        if isinstance(value, (TextStringObject, str)):
            yield str(value)
        elif isinstance(value, (ByteStringObject, bytes)):
            yield from byte_views(bytes(value))
        elif isinstance(value, (ArrayObject, list)):
            for child in value:
                yield from self.strings(child, seen)
        elif isinstance(value, DictionaryObject):
            # Streams and resources are handled separately, never via this tree.
            for key, child in value.items():
                if key not in ("/Resources", "/Parent", "/P", "/Length"):
                    yield from self.strings(child, seen)

    def content(self, stream, location):
        try:
            parsed = ContentStream(stream, self.reader)
            self.inspect_bytes(parsed.get_data(), location + "/decoded-content")
            fragments = []
            for operands, operator in parsed.operations:
                if operator in (b"Tj", b"TJ", b"'", b'"'):
                    fragments.extend(self.strings(operands))
                elif operator in (b"BDC", b"DP"):
                    for text in self.strings(operands):
                        self.inspect_text(text, location + "/marked-content")
            # Detect escaped, hex-encoded and split PDF text-showing operands.
            self.inspect_text("".join(fragments), location + "/text-operands")
            self.inspect_text(" ".join(fragments), location + "/text-operands")
            self.counts["content_streams"] += 1
        except Exception as exc:
            self.error(location, "parse_content_stream", exc)

    def resources(self, resources, location):
        if resources is None:
            return
        try:
            resources = object_value(resources)
            xobjects = object_value(resources.get("/XObject", {}))
            for index, child in enumerate(xobjects.values(), 1):
                # Numbered positions avoid copying resource names into reports.
                self.xobject(child, f"{location}/xobject[{index}]")
        except Exception as exc:
            self.error(location, "read_resources", exc)

    def xobject(self, reference, location):
        key = object_key(reference)
        if key in self.seen_xobjects:
            self.counts["reused_xobject_references"] += 1
            return
        self.seen_xobjects.add(key)
        try:
            value = object_value(reference)
            subtype = value.get("/Subtype")
            if subtype == "/Form":
                self.counts["form_xobjects"] += 1
                self.content(value, location)
            elif subtype == "/Image":
                self.counts["image_xobjects"] += 1
                self.inspect_bytes(value.get_data(), location + "/image-payload")
            elif isinstance(value, StreamObject):
                self.counts["other_xobjects"] += 1
                self.inspect_bytes(value.get_data(), location + "/decoded-stream")
            for field in ("/ActualText", "/Alt"):
                if field in value:
                    for text in self.strings(value[field]):
                        self.inspect_text(text, location + "/accessibility-text")
            metadata = value.get("/Metadata")
            if metadata is not None:
                metadata = object_value(metadata)
                if isinstance(metadata, StreamObject):
                    self.inspect_bytes(metadata.get_data(), location + "/metadata")
            self.resources(value.get("/Resources"), location)
            for field in ("/SMask", "/Mask"):
                mask = value.get(field)
                if mask is not None and isinstance(object_value(mask), StreamObject):
                    self.xobject(mask, location + field.lower())
        except Exception as exc:
            self.error(location, "read_xobject", exc)

    def destination_valid(self, value, seen=None):
        if seen is None:
            seen = set()
        key = object_key(value)
        if key in seen:
            return False
        seen.add(key)
        value = object_value(value)
        if isinstance(value, (TextStringObject, NameObject, str)):
            name = str(value)
            destination = self.named.get(name, self.named.get(name.lstrip("/")))
            return destination is not None and self.destination_valid(destination, seen)
        if isinstance(value, DictionaryObject):
            if "/Page" in value:
                page = value.raw_get("/Page")
            elif "/D" in value:
                return self.destination_valid(value.raw_get("/D"), seen)
            else:
                return False
        elif isinstance(value, (ArrayObject, list)) and value:
            page = value[0]
        else:
            return False
        if object_key(page) in self.page_keys:
            return True
        page = object_value(page)
        if isinstance(page, (NumberObject, int)):
            return 0 <= page < len(self.reader.pages)
        return object_key(page) in self.page_keys

    def check_destination(self, value, location):
        self.counts["internal_destinations_checked"] += 1
        try:
            valid = self.destination_valid(value)
        except Exception as exc:
            self.error(location, "resolve_destination", exc)
            valid = False
        if not valid:
            self.destination_failures.append({"location": location,
                                               "reason": "missing_or_invalid_destination"})

    def action(self, reference, location, seen=None):
        if seen is None:
            seen = set()
        key = object_key(reference)
        if key in seen:
            return
        seen.add(key)
        value = object_value(reference)
        if not isinstance(value, DictionaryObject):
            self.error(location, "read_action_dictionary", TypeError())
            return
        if value.get("/S") == "/GoTo":
            self.counts["internal_goto_actions"] += 1
            self.check_destination(value.get("/D"), location)
        elif value.get("/S") in ("/URI", "/GoToR", "/GoToE", "/Launch"):
            self.counts["external_actions_not_verified"] += 1
        following = value.get("/Next")
        if following is not None:
            following = object_value(following)
            values = following if isinstance(following, ArrayObject) else [following]
            for index, child in enumerate(values, 1):
                self.action(child, f"{location}/next[{index}]", seen)

    def additional_actions(self, owner, location):
        additional = owner.get("/AA")
        if additional is not None:
            for index, action in enumerate(object_value(additional).values(), 1):
                self.action(action, f"{location}/additional-action[{index}]")

    def outline(self, items, location="outline"):
        for index, item in enumerate(items, 1):
            path = f"{location}/item[{index}]"
            if isinstance(item, list):
                self.outline(item, path)
            elif isinstance(item, DictionaryObject):
                self.check_destination(item, path)

    def run(self):
        try:
            self.named = self.reader.named_destinations
            for index, destination in enumerate(self.named.values(), 1):
                self.check_destination(destination, f"named-destination[{index}]")
        except Exception as exc:
            self.error("document", "read_named_destinations", exc)
        for index, page in enumerate(self.reader.pages, 1):
            location = f"page[{index}]"
            try:
                self.inspect_text(page.extract_text() or "", location + "/extracted-text")
                self.counts["pages_text_extracted"] += 1
            except Exception as exc:
                self.error(location, "extract_page_text", exc)
            contents = page.get("/Contents")
            if contents is not None:
                self.content(contents, location)
            self.resources(page.get("/Resources"), location)
            try:
                for position, annotation in enumerate(page.get("/Annots", []), 1):
                    annotation = object_value(annotation)
                    path = f"{location}/annotation[{position}]"
                    if "/Dest" in annotation:
                        self.check_destination(annotation.raw_get("/Dest"), path)
                    if "/A" in annotation:
                        self.action(annotation.raw_get("/A"), path)
                    self.additional_actions(annotation, path)
                self.additional_actions(page, location)
            except Exception as exc:
                self.error(location, "read_annotations_or_actions", exc)
        try:
            self.outline(self.reader.outline)
            catalog = self.reader.root_object
            if "/OpenAction" in catalog:
                opening = object_value(catalog["/OpenAction"])
                if isinstance(opening, DictionaryObject) and "/S" in opening:
                    self.action(opening, "document/open-action")
                else:
                    self.check_destination(opening, "document/open-destination")
            self.additional_actions(catalog, "document")
        except Exception as exc:
            self.error("document", "read_outline_or_open_actions", exc)


def audit_tex_log(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    patterns = [
        ("missing_glyph", r"Missing character:"),
        ("overfull_box", r"Overfull \\[hv]box"),
        ("undefined_control_sequence", r"Undefined control sequence"),
        ("undefined_reference_or_citation",
         r"(?:Warning:[^\n]*(?:Reference|Citation)[^\n]*(?:\n[^\n]*){0,2}?\bundefined\b|There were undefined (?:references|citations))"),
    ]
    findings = []
    for rule, pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append({"rule": rule, "line": text.count("\n", 0, match.start()) + 1,
                             "count": 1})
    return {"checked": True, "findings": findings}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--forbidden-text", action="append", default=[], metavar="LITERAL",
                        help="Explicit literal to detect; repeatable. Never copied into the report.")
    parser.add_argument("--expected-ref-count", type=int, metavar="N",
                        help="Require exactly the numbered targets prefix+1 through prefix+N.")
    parser.add_argument("--ref-prefix", default="ref:")
    parser.add_argument("--tex-log", type=Path)
    parser.add_argument("--output", type=Path, help="JSON report path; stdout is used when omitted.")
    args = parser.parse_args(argv)
    if any(not normalized(rule) for rule in args.forbidden_text):
        parser.error("--forbidden-text values must contain non-whitespace text")
    if args.expected_ref_count is not None and args.expected_ref_count < 0:
        parser.error("--expected-ref-count must be nonnegative")
    if args.output:
        protected = {args.pdf.resolve()}
        if args.tex_log:
            protected.add(args.tex_log.resolve())
        hard_link = args.output.exists() and any(
            path.exists() and args.output.samefile(path) for path in protected)
        if hard_link or args.output.resolve() in protected or args.output.suffix.lower() != ".json":
            parser.error("--output must be a .json path distinct from all input files")
    return args


def main(argv=None):
    args = parse_args(argv)
    # pypdf warnings may quote source content; expose only warning counts below.
    logger = logging.getLogger("pypdf")
    log_counts = CountLogs()
    logger.handlers[:] = [log_counts]
    logger.propagate = False
    report = {"schema_version": 1, "status": "error", "read_only": True,
              "pdf": str(args.pdf.resolve()), "forbidden_rule_count": len(args.forbidden_text),
              "limitations": LIMITATIONS, "errors": []}
    exit_code = 2
    try:
        if PdfReader is None:
            raise ModuleNotFoundError("pypdf")
        before = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            reader = PdfReader(str(args.pdf), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise PermissionError("encrypted_pdf_requires_password")
            audit = Audit(reader, args.forbidden_text)
            audit.run()
            report["python_warning_counts"] = dict(Counter(type(w.message).__name__ for w in captured))
            report["pypdf_log_counts"] = dict(log_counts.counts)
        report.update(pdf_sha256=before, page_count=len(reader.pages),
                      scan_counts=dict(audit.counts), errors=audit.errors,
                      forbidden_matches=[{"rule": rule, "location": location, "count": count}
                                         for (rule, location), count in sorted(audit.matches.items())],
                      internal_link_failures=audit.destination_failures,
                      named_destination_count=len(audit.named))
        if args.expected_ref_count is not None:
            expected = set(range(1, args.expected_ref_count + 1))
            numbered = [(name, int(match.group(1))) for name in audit.named
                        if (match := re.fullmatch(re.escape(args.ref_prefix) + r"([0-9]+)", name))]
            actual = {number for _, number in numbered}
            missing = sorted(number for number in expected
                             if args.ref_prefix + str(number) not in audit.named)
            extra = sorted(actual - expected)
            noncanonical = sorted(number for name, number in numbered
                                  if name != args.ref_prefix + str(number))
            report["references"] = {"checked": True, "expected_count": len(expected),
                                    "actual_numbered_target_count": len(numbered),
                                    "missing_numbers": missing, "unexpected_numbers": extra,
                                    "noncanonical_numbers": noncanonical}
        else:
            report["references"] = {"checked": False}
        report["tex_log"] = audit_tex_log(args.tex_log) if args.tex_log else {"checked": False}
        after = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
        report["pdf_unchanged_during_audit"] = before == after
        if before != after:
            report["errors"].append({"location": "document", "operation": "verify_stable_input",
                                     "error_type": "InputChangedDuringAudit"})
        failed = bool(audit.matches or audit.destination_failures or
                      report["references"].get("missing_numbers") or
                      report["references"].get("unexpected_numbers") or
                      report["references"].get("noncanonical_numbers") or
                      report["tex_log"].get("findings"))
        exit_code = 2 if report["errors"] else 1 if failed else 0
        report["status"] = ("pass", "fail", "error")[exit_code]
    except Exception as exc:
        report["errors"].append({"location": "document", "operation": "audit",
                                 "error_type": type(exc).__name__})
    report["exit_code"] = exit_code
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            # Do not quote paths from exception messages or print the source file.
            print(json.dumps({"status": "error", "operation": "write_json_report",
                              "error_type": type(exc).__name__, "exit_code": 2}), file=sys.stderr)
            return 2
    else:
        sys.stdout.write(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
