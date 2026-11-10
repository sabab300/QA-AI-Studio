#!/usr/bin/env python3
"""Check the quality of a Test Cases workbook (.xlsx) exported from QA AI Studio.

It does not need the application or an AI model. It reports, in plain language:
  * rows with missing/placeholder fields,
  * rows whose Test Type is not one of the types you selected,
  * type labels that contradict the wording (for example a "Negative" case that expects success),
  * duplicate or near-duplicate cases,
  * cases that are not supported by the source document (when you pass --source).

USAGE
    python tools\\check_test_case_quality.py "TC-V1-TC1.xlsx" --types Functional,Positive,Negative
    python tools\\check_test_case_quality.py "TC-V1-TC1.xlsx" --types Positive,Negative --source "CRF-PSW-PRA-34-27092024.docx"
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "AI-Web"))
sys.path.insert(0, str(PROJECT))

from openpyxl import load_workbook  # noqa: E402

from Core import test_case_engine as engine  # noqa: E402

HEADERS = {
    "scenario": "scenario", "name / scenario / requirement": "scenario",
    "test case": "test_case", "test type": "test_type", "test types": "test_type", "type": "test_type",
    "importance": "importance", "importance (high/medium/low)": "importance",
    "pre-conditions": "pre_conditions", "preconditions": "pre_conditions", "pre conditions": "pre_conditions",
    "steps": "steps", "test steps": "steps", "expected result": "expected_result",
    "execution": "execution", "execution type": "execution", "tc id": "tc_id", "test case id": "tc_id",
    "requirement/scenario": "scenario", "tc number": "tc_id", "comments": "comments",
}


def read_rows(path: Path):
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book[book.sheetnames[0]]
    rows = list(sheet.iter_rows(values_only=True))
    book.close()
    if not rows:
        return []
    mapping = {i: HEADERS.get(str(v or "").strip().lower()) for i, v in enumerate(rows[0])}
    out = []
    for cells in rows[1:]:
        if not any(cells):
            continue
        out.append({field: str(cells[i] or "").strip() for i, field in mapping.items() if field})
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook")
    parser.add_argument("--types", default="", help="comma list of the test types that were selected")
    parser.add_argument("--source", help="source document (.docx/.pdf/.txt) to check grounding against")
    args = parser.parse_args()

    rows = read_rows(Path(args.workbook))
    everything = list(engine.TYPE_DEFINITIONS)
    selected = [t.strip() for t in args.types.split(",") if t.strip()] or everything
    source_text = ""
    if args.source:
        from Core.text_extractor import TextExtractor
        source_text = TextExtractor().extract_text(args.source)

    problems = Counter()
    notes = []
    kept = []
    by_type = Counter()
    for number, row in enumerate(rows, start=2):
        label = row.get("tc_id") or f"row {number}"
        for field in ("scenario", "test_case", "steps", "expected_result", "pre_conditions"):
            if engine._is_placeholder(row.get(field)):
                problems["missing field"] += 1
                notes.append(f"{label}: '{field}' is empty or a placeholder")
        types = engine.canonical_types(row.get("test_type", ""), everything)
        if not types:
            problems["unknown test type"] += 1
            notes.append(f"{label}: test type '{row.get('test_type')}' is not a supported type")
            continue
        by_type[types[0]] += 1
        if types[0] not in selected:
            problems["type not selected"] += 1
            notes.append(f"{label}: type {types[0]} was not selected")
        fixed = engine._polarity_fix(types[0], row, everything)
        if fixed != types[0]:
            problems["label contradicts wording"] += 1
            notes.append(f"{label}: labelled {types[0]} but the wording fits {fixed}")
        if source_text:
            score = engine.grounding_score(row, source_text)
            if score < 0.30:
                problems["not in source document"] += 1
                notes.append(f"{label}: only {int(score * 100)}% of its key words appear in the source")
        row["steps"] = engine._numbered_steps(row.get("steps", "")) or row.get("steps", "")
        row["test_types"] = types
        import re as _re
        evidence = _re.search(r'Evidence \(\w+\): "(.*?)"\s*(?:\n|$)', row.get("comments", ""), _re.S)
        source_ref = _re.search(r"^Source: (.+?)(?: \(internal reference\))?(?: - |$)", row.get("comments", ""), _re.M)
        row["source_evidence"] = evidence.group(1) if evidence else ""
        row["requirement_ref"] = source_ref.group(1).strip() if source_ref else ""
        if row["steps"]:
            kept.append(row)

    if source_text:  # V3: business-rule consistency and evidence basis against the source document
        from Core import business_rules as rules_module
        from Core import test_case_quality as quality
        from Core.requirement_extractor import extract_requirements
        requirements = extract_requirements(source_text)["units"] or [{"ref": "document", "text": source_text, "statements": []}]
        all_rules = [rule for requirement in requirements for rule in rules_module.extract_rules(requirement)]
        whole = {"ref": "document", "text": source_text,
                 "statements": [s for r in requirements for s in (r.get("statements") or [])]}
        for number, row in enumerate(kept, start=2):
            label = row.get("tc_id") or f"row {number}"
            findings = rules_module.check_case(row, all_rules)
            verdict = rules_module.verdict_of(findings)
            if verdict == "contradiction":
                problems["contradicts a documented business rule"] += 1
                notes.append(f"{label}: " + next(f["message"] for f in findings if f["verdict"] == "contradiction")[:300])
            basis = quality.basis(row, whole, "", verdict)
            if basis["category"] == "assumption":
                problems["assumption / needs confirmation"] += 1
                notes.append(f"{label}: " + (basis["reasons"] or ["behaviour not stated by the document"])[0][:300])
            expected = quality.expected_quality(row, "")
            if expected["blocking"]:
                problems["weak expected result"] += 1
                notes.append(f"{label}: " + expected["issues"][0])
        for note in rules_module.mapping_ambiguities(all_rules):
            notes.append("Document ambiguity: " + note)

    probe = engine.Report(selected, "all")
    dedupe_requirements = []
    if source_text:
        dedupe_requirements = [engine._attach_rules_and_units(r) for r in requirements]
    engine.dedupe(kept, probe, requirements=dedupe_requirements)
    for decision in probe.duplicate_decisions:
        notes.append(f"{decision['decision']}: \"{decision['case'][:80]}\" vs \"{decision['kept_case'][:80]}\"")
    repeats = probe.drops.get("duplicate", 0)
    if repeats:
        problems["duplicate"] += repeats
        notes.append(f"{repeats} case(s) repeat another case")

    print(f"Workbook: {args.workbook}")
    print(f"Test cases: {len(rows)}")
    print("By type: " + (", ".join(f"{k} {v}" for k, v in by_type.most_common()) or "none"))
    missing_types = [t for t in selected if not by_type.get(t)]
    if args.types and missing_types:
        print("Selected types with NO test cases: " + ", ".join(missing_types))
    print()
    if not problems:
        print("No problems found.")
        return 0
    print("Problems:")
    for name, count in problems.most_common():
        print(f"  {count:>4}  {name}")
    print("\nFirst findings:")
    for note in notes[:40]:
        print("  - " + note)
    return 1


if __name__ == "__main__":
    sys.exit(main())
