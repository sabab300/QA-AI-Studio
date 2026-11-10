#!/usr/bin/env python3
"""Run AI test-case generation on a document with the REAL model and write an evaluation pack.

It uses the same engine as QA Engineering (Core.test_case_engine.generate_cases) and does not write to the
application database or the Central Repository. Output (in --out):

  result.json   rows + the full quality report
  cases.xlsx    the delivered cases in the QA AI Studio workbook layout (plus Quality Status / Basis columns)
  calls.json    one entry per model call: duration, prompt/answer size, tokens reported by Ollama
  summary.md    the quality summary, coverage per requirement unit, rejected/duplicate decisions

USAGE (from the project folder, with Ollama running)
    python tools\\tc_generation_eval.py --text "tests\\generation\\fixtures\\psw_crf_pra_34.txt" --count 5 ^
        --types Functional,Positive,Negative,Boundary --out "reports\\tc_eval\\A_psw_5"
    python tools\\tc_generation_eval.py --text "tests\\generation\\fixtures\\weboc_crf_pra_89.txt" --count 20 --out "reports\\tc_eval\\D_weboc_20"

  --llm gateway   (default) the configured AI gateway of the application (Settings -> LLM), exactly as QA Engineering
  --llm ollama    direct Ollama /api/chat call (--ollama-url, --model), same options as the gateway uses
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "AI-Web"))

from Core import test_case_engine as engine  # noqa: E402

DEFAULT_OPTIONS = {"temperature": 0.2, "num_predict": 3000, "num_ctx": 5120}


def ollama_llm(url: str, model: str, calls: list, options: dict = None):
    import requests

    def call(prompt, system):
        started = time.time()
        payload = {"model": model, "stream": False, "keep_alive": "30m", "options": dict(options or DEFAULT_OPTIONS),
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        response = requests.post(url.rstrip("/") + "/api/chat", json=payload, timeout=1800)
        response.raise_for_status()
        data = response.json()
        text = (data.get("message") or {}).get("content", "")
        calls.append({"seconds": round(time.time() - started, 1), "prompt_chars": len(prompt), "answer_chars": len(text),
                      "prompt_tokens": data.get("prompt_eval_count"), "answer_tokens": data.get("eval_count")})
        return text
    return call


def gateway_llm(calls: list):
    from Core.test_case_generator import TestCaseGenerator
    state = {"provider": None, "model": None, "error": None}
    inner = TestCaseGenerator.__new__(TestCaseGenerator)
    from Core.llm_engine import LLMEngine
    inner.llm = LLMEngine()
    from Core.logger import Logger
    inner.logger = Logger.get_logger()
    base = inner._llm_call(state)

    def call(prompt, system):
        started = time.time()
        text = base(prompt, system)
        calls.append({"seconds": round(time.time() - started, 1), "prompt_chars": len(prompt), "answer_chars": len(text or ""),
                      "provider": state["provider"], "model": state["model"]})
        return text
    return call


COLUMNS = ["Test Case ID", "Requirement/Scenario", "Importance", "Test Types", "Test Case", "Preconditions", "Test Steps",
           "Expected Result", "Execution Type", "Quality Status", "Basis", "Comments"]


def write_workbook(rows: list, quality: list, path: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    book = Workbook()
    sheet = book.active
    sheet.title = "Test Cases"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for number, (row, q) in enumerate(zip(rows, quality), start=1):
        sheet.append([f"EVAL_{number:04d}", row.get("scenario"), row.get("importance"), row.get("test_type"),
                      row.get("test_case"), row.get("pre_conditions"), row.get("steps"), row.get("expected_result"),
                      row.get("execution"), q.get("status"), q.get("basis"), row.get("comments")])
    for column, width in zip("ABCDEFGHIJKL", (12, 40, 10, 14, 48, 40, 60, 60, 12, 14, 12, 70)):
        sheet.column_dimensions[column].width = width
    for line in sheet.iter_rows(min_row=2):
        for cell in line:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def summary_markdown(title: str, outcome: dict, calls: list, seconds: float) -> str:
    report = outcome["report"]
    cov = report.get("unit_coverage") or {}
    model_seconds = sum(c.get("seconds") or 0 for c in calls)
    tokens = sum(c.get("answer_tokens") or 0 for c in calls)
    lines = [f"# {title}", "", f"Model calls: {len(calls)}; model time {model_seconds / 60:.1f} min"
             + (f" ({tokens} answer tokens)" if tokens else "") + f"; engine wall time {seconds / 60:.1f} min.", ""]
    lines += ["## Summary", ""] + [f"- {line}" for line in report.get("summary", [])] + [""]
    lines += ["## Delivered cases", "", "| # | Status | Basis | Type | Execution | Test case | Covers |", "|---|---|---|---|---|---|---|"]
    for number, (row, q) in enumerate(zip(outcome["rows"], report.get("case_quality", [])), start=1):
        lines.append(f"| {number} | {q['status']} | {q['basis']} | {row['test_types'][0]} | {row['execution']} | "
                     f"{row['test_case'][:110].replace('|', '/')} | {', '.join(q['covers'])} |")
    lines += ["", "## Requirement units", "", f"{cov.get('denominator', '')}", "", "| Unit | Priority | Kind | Status | Text |",
              "|---|---|---|---|---|"]
    for unit in cov.get("units", []):
        lines.append(f"| {unit['id']} | {unit['priority']} | {unit['kind']} | {unit['status']} | {unit['text'][:120].replace('|', '/')} |")
    if report.get("contradiction_cases"):
        lines += ["", "## Rejected: business-rule contradictions", ""]
        lines += [f"- {c['title']} — {c['reasons'][0][:300]}" for c in report["contradiction_cases"]]
    if report.get("duplicate_decisions"):
        lines += ["", "## Duplicate decisions", ""]
        lines += [f"- {d['decision']}: \"{d['case'][:90]}\" vs kept \"{d['kept_case'][:90]}\"" for d in report["duplicate_decisions"]]
    if report.get("rejected_cases"):
        lines += ["", "## All rejected cases", ""]
        lines += [f"- [{r['requirement']}] {r['title'][:100]} — {r['reason']}" for r in report["rejected_cases"]]
    return "\n".join(lines) + "\n"


def run(text: str, types: list, count, llm, out: Path, title: str, calls: list, label: str = "Selected document"):
    started = time.time()
    outcome = engine.generate_cases([{"text": text}], types, count, llm, label=label,
                                    progress=lambda stage, message: print(f"[{time.strftime('%H:%M:%S')}] {stage}: {message}", flush=True))
    seconds = time.time() - started
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(outcome, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "calls.json").write_text(json.dumps(calls, indent=1), encoding="utf-8")
    if outcome.get("rows"):
        write_workbook(outcome["rows"], outcome["report"].get("case_quality", []), out / "cases.xlsx")
    (out / "summary.md").write_text(summary_markdown(title, outcome, calls, seconds), encoding="utf-8")
    return outcome, seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", required=True, help="plain-text file of the document (the indexed text)")
    parser.add_argument("--count", default="all")
    parser.add_argument("--types", default="Functional,Positive,Negative,Boundary")
    parser.add_argument("--out", required=True)
    parser.add_argument("--llm", choices=("gateway", "ollama"), default="gateway")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen2.5:7b")
    args = parser.parse_args()
    calls: list = []
    llm = gateway_llm(calls) if args.llm == "gateway" else ollama_llm(args.ollama_url, args.model, calls)
    text = Path(args.text).read_text(encoding="utf-8")
    outcome, seconds = run(text, [t.strip() for t in args.types.split(",") if t.strip()], args.count, llm, Path(args.out),
                           f"Generation evaluation - {Path(args.text).name} - {args.count} case(s)", calls)
    print("\n".join(outcome["report"].get("summary", [])))
    print(f"\nWritten to {args.out} ({seconds / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
