"""Read-only canonical coverage/result projection; no invented execution totals."""
import sqlite3
from pathlib import Path


def report(session, db_path):
    result=dict(session)
    bindings=[binding for output in session.get("outputs",[]) if output["kind"]=="test_cases" and output["state"]=="completed"
              for binding in output["result"].get("bindings",[])]
    transferred={b["test_case_id"] for output in session.get("outputs",[]) if output["kind"]=="automation" and output["state"]=="completed"
                 for b in output["result"].get("bindings",[])}
    result["coverage"]={"scope":"Discovered APIs only; not total application coverage",
                        "discovered_endpoints":len(session["endpoints"]),
                        "endpoints_with_saved_test_cases":len({b["endpoint_key"] for b in bindings}),
                        "saved_test_cases":len({b["test_case_id"] for b in bindings}),
                        "transferred_test_cases":len(transferred)}
    result["canonical_test_cases"]=[];result["execution_history"]=[]
    connection=sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True)
    connection.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "test_cases" in tables:
            columns={r[1] for r in connection.execute("PRAGMA table_info(test_cases)")}
            fields=[f for f in ("id","tc_number","test_case_version_id","execution","review_state","last_result","automation_type","status") if f in columns]
            for identity in sorted({b["test_case_id"] for b in bindings}):
                row=connection.execute("SELECT "+",".join(fields)+" FROM test_cases WHERE id=?",(identity,)).fetchone()
                result["canonical_test_cases"].append(dict(row) if row else {"id":identity,"availability":"No longer present in canonical repository"})
        if "automation_runs" in tables:
            columns={r[1] for r in connection.execute("PRAGMA table_info(automation_runs)")}
            fields=[f for f in ("id","run_id","test_case_id","automation_type","status","started_at","finished_at") if f in columns]
            if "test_case_id" in columns:
                for identity in sorted(transferred):
                    result["execution_history"] += [dict(r) for r in connection.execute("SELECT "+",".join(fields)+" FROM automation_runs WHERE test_case_id=? ORDER BY rowid DESC LIMIT 20",(identity,))]
    finally:
        connection.close()
    result["coverage"]["test_cases_with_recorded_execution"]=len({r["test_case_id"] for r in result["execution_history"]})
    return result
