"""Read-only, database-backed data source for the web dashboard.

Requirement 9 (QA-AUTOMATION-REPOSITORY-ENV-REPORTING-UIUX-V1) — this
repository was previously a thin, mostly-placeholder aggregator (a
handful of COUNT(*) queries plus a "recent runs" query that only ever
looked at test_cases.last_run_date). It is now the real data source
for all five required Dashboard sections (Overview, Test Execution,
Knowledge Coverage, Recent Runs, Recent Activity) plus the filter
option lists Reports & Analytics needs for its cascading filters.

Every number here comes from a real query against this application's
existing tables — knowledge_items/domains/modules (the authoritative
hierarchy tables — see Core/metadata_manager.py), test_cases (the
authoritative "current state" of each test case — status/last_result/
automation_type/execution_type), automation_runs (the ONE persisted
execution-history table — see Core/automation_execution_repository.py,
reused here rather than re-queried ad hoc), and audit_logs (the
existing activity log). Nothing is fabricated; a metric that can't
currently be derived reliably from this schema is either omitted or
returned with an explicit note of what would be needed, never
invented. Every non-trivial calculation (pass rate, knowledge
coverage %) documents its exact formula in the code, not just in a
comment, so a report reader can audit what a percentage actually
means.
"""

import sqlite3

from Config.settings import AI_MODE, LLM_MODEL
from Database.db_manager import DatabaseManager
from Core.automation_execution_repository import AutomationExecutionRepository

# The three automation types this application supports today — the
# same fixed vocabulary as Web/routers/automation_router.py's
# AUTO_TYPES / Core/test_case_repository.py's qa_execution_tools seed
# rows (Playwright/API/SQL). Reused here instead of re-deriving it
# from a query, since it's a fixed, small, already-established set.
AUTOMATION_TYPES = ("Playwright", "API", "SQL")


class DashboardRepository:
    """Keeps dashboard queries small, resilient, and independent of UI data."""

    def __init__(self):
        self.db = DatabaseManager()
        self.runs = AutomationExecutionRepository()

    def snapshot(self, recent_limit=20, activity_limit=20, trend_days=14):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        try:
            overview = self._overview(conn)
            hierarchy = self._workbook_hierarchy(conn)
            return {
                # "metrics" kept as an alias of "overview" for any
                # existing caller that hasn't moved to the new key yet
                # — same dict, not a second computation.
                "metrics": overview,
                "overview": overview,
                "hierarchy": hierarchy,
                "test_execution": self._test_execution(conn, trend_days),
                "knowledge_coverage": self._knowledge_coverage(conn),
                "recent_runs": self._recent_runs(recent_limit),
                "recent_activity": self._recent_activity(conn, activity_limit),
            }
        finally:
            conn.close()

    def filters(self):
        """
        Requirement 9 — Reports & Analytics' cascading filter option
        lists (Domain/Module/Knowledge Name/Version/Automation Type/
        Result). Domain/Module/Knowledge Name/Version are read from
        the SAME authoritative knowledge_items-backed hierarchy every
        other screen uses (via QaEngineeringWeb().list_scope(), the
        one shared hierarchy service — see Requirement 1) rather than
        a second, dashboard-only hierarchy query. Automation Type and
        Result are small fixed vocabularies already used elsewhere in
        this app (qa_execution_tools / test_cases.last_result), listed
        directly rather than "discovered" from data that might have
        gaps.
        """
        from Core.qa_engineering_web_repository import QaEngineeringWeb

        sources = QaEngineeringWeb().list_scope()
        return {
            # Flat unique lists for a simple "show me every value that
            # exists" control...
            "domains": sorted({s.get("domain") for s in sources if s.get("domain")}),
            "modules": sorted({s.get("module") for s in sources if s.get("module")}),
            "knowledge_names": sorted({s.get("knowledge_name") for s in sources if s.get("knowledge_name")}),
            "versions": sorted({s.get("version") for s in sources if s.get("version")}),
            "automation_types": list(AUTOMATION_TYPES),
            "results": ["Pass", "Fail", "Blocked", "Not Run"],
            # ...and the raw domain/module/knowledge_name/version combos
            # so the frontend can wire a REAL cascading Domain -> Module
            # -> Knowledge Name -> Version filter (Module options
            # depending on the selected Domain, etc.) using the exact
            # same fillSelect()-based cascade pattern the QA Automation
            # panels already use, instead of a second cascade
            # implementation.
            "sources": sources,
        }

    @staticmethod
    def _dict_factory(cursor, row):
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    @staticmethod
    def _has_table(conn, table_name):
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone() is not None

    def _scalar(self, conn, query, params=(), default=0):
        if not query:
            return default
        row = conn.execute(query, params).fetchone()
        if row is None:
            return default
        return next(iter(row.values())) if isinstance(row, dict) else row[0]

    # --------------------------------------------------
    # Overview
    # --------------------------------------------------

    def _overview(self, conn):
        has_knowledge = self._has_table(conn, "knowledge_items")
        has_domains = self._has_table(conn, "domains")
        has_modules = self._has_table(conn, "modules")
        has_cases = self._has_table(conn, "test_cases")

        results = self._result_breakdown(conn) if has_cases else {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0}
        total_cases = sum(results.values())
        executed = results["Pass"] + results["Fail"] + results["Blocked"]

        automated = self._scalar(
            conn, "SELECT COUNT(*) FROM test_cases WHERE execution IN ('Playwright','API','SQL')"
        ) if has_cases else 0
        manual = (total_cases - automated) if has_cases else 0

        # Requirement 9 — "provide useful QA management summary
        # metrics ... do NOT fabricate metrics." Pass rate is defined
        # EXPLICITLY as Passed / (Passed + Failed + Blocked) — i.e.
        # the pass rate among test cases that have actually been run
        # at least once. Test cases still "Not Run" are deliberately
        # excluded from the denominator (an un-run test case is
        # neither a pass nor a fail yet) rather than silently counted
        # against the rate. Returned as a rounded percentage AND as
        # the raw passed/executed counts, so the percentage is never a
        # bare, unauditable number.
        pass_rate_pct = round((results["Pass"] / executed) * 100, 1) if executed else None

        # Requirement 9 — "automation assets ... where supported."
        # Defined explicitly (and narrowly, so it's honest about what
        # it counts) as: test cases that have an actual automation
        # artifact attached — a generated/recorded script, or a bound
        # API endpoint — as opposed to merely being MARKED
        # Automatable with nothing built yet. This is a real,
        # queryable signal (automation_script/recorded_script/
        # bound_api_endpoint_id are all real columns — see
        # Core/test_case_repository.py ensure_schema()), not an
        # invented number.
        automation_assets = self._scalar(
            conn,
            """
            SELECT COUNT(*) FROM test_cases
            WHERE (automation_script IS NOT NULL AND automation_script <> '')
               OR (recorded_script IS NOT NULL AND recorded_script <> '')
               OR bound_api_endpoint_id IS NOT NULL
            """,
        ) if has_cases else 0

        return {
            "knowledge_items": self._scalar(conn, "SELECT COUNT(*) FROM knowledge_items") if has_knowledge else 0,
            "domains": self._scalar(conn, "SELECT COUNT(*) FROM domains") if has_domains else 0,
            "modules": self._scalar(conn, "SELECT COUNT(*) FROM modules") if has_modules else 0,
            "versions": self._scalar(conn, "SELECT COUNT(DISTINCT version) FROM knowledge_items WHERE COALESCE(version, '') <> ''") if has_knowledge else 0,
            "test_cases": total_cases,
            "automated_test_cases": automated,
            "manual_test_cases": manual,
            "executed_test_cases": executed,
            "last_results": results,
            "pass_rate_pct": pass_rate_pct,
            "pass_rate_definition": "Passed / (Passed + Failed + Blocked); test cases Not Run are excluded from the denominator.",
            "automation_assets": automation_assets,
            "automation_assets_definition": "Test cases with a generated/recorded automation script or a bound API endpoint attached.",
            "ai_mode": AI_MODE,
            "llm_model": LLM_MODEL,
        }

    def _result_breakdown(self, conn):
        counts = {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0}
        for row in conn.execute(
            "SELECT COALESCE(last_result, 'Not Run') AS result, COUNT(*) AS count "
            "FROM test_cases GROUP BY COALESCE(last_result, 'Not Run')"
        ):
            result = row["result"] if isinstance(row, dict) else row[0]
            count = row["count"] if isinstance(row, dict) else row[1]
            if result in counts:
                counts[result] = count
        return counts

    def _workbook_hierarchy(self, conn):
        """Return the persisted Knowledge -> TC workbook hierarchy once.

        All Dashboard trees consume this same flat contract. Relationships
        come from database foreign keys/path columns; filenames are labels,
        never used to infer ownership.
        """
        required = {"knowledge_items", "test_case_versions", "test_case_files"}
        if not all(self._has_table(conn, table) for table in required):
            return []
        rows = conn.execute(
            """
            SELECT ki.id AS knowledge_id, ki.domain, ki.module, ki.knowledge_name,
                   COALESCE(ki.version,'') AS version,
                   COALESCE(ki.document_type,'') AS document_type,
                   COALESCE(ki.source_type,'') AS source_type,
                   COALESCE(ki.file_name,ki.source_file_id,'Source Document') AS source_document,
                   tv.id AS test_case_version_id, tv.version_number,
                   tv.folder_path, tf.id AS test_case_file_id, tf.file_name,
                   tf.workbook_path
            FROM knowledge_items ki
            JOIN test_case_versions tv ON tv.source_knowledge_id=ki.id
            JOIN test_case_files tf ON tf.test_case_version_id=tv.id
            ORDER BY ki.domain,ki.module,ki.knowledge_name,ki.version,
                     ki.document_type,ki.source_type,ki.id,tv.version_number,tf.id
            """
        ).fetchall()
        metrics = {}
        if self._has_table(conn, "test_cases"):
            for row in conn.execute(
                """SELECT test_case_version_id,reviewed_workbook_path,
                          COUNT(*) AS total,
                          SUM(CASE WHEN COALESCE(last_result,'Not Run')='Pass' THEN 1 ELSE 0 END) AS passed,
                          SUM(CASE WHEN COALESCE(last_result,'Not Run')='Fail' THEN 1 ELSE 0 END) AS failed,
                          SUM(CASE WHEN COALESCE(last_result,'Not Run')='Blocked' THEN 1 ELSE 0 END) AS blocked,
                          SUM(CASE WHEN COALESCE(last_result,'Not Run')='Not Run' THEN 1 ELSE 0 END) AS not_run,
                          SUM(CASE WHEN execution IN ('Playwright','API','SQL') THEN 1 ELSE 0 END) AS automated
                   FROM test_cases
                   WHERE test_case_version_id IS NOT NULL
                   GROUP BY test_case_version_id,reviewed_workbook_path"""
            ):
                metrics[(row["test_case_version_id"], row["reviewed_workbook_path"] or "")] = dict(row)
        run_metrics = {}
        if self._has_table(conn, "automation_runs"):
            for row in conn.execute(
                """SELECT tc.test_case_version_id,tc.reviewed_workbook_path,
                          COUNT(ar.id) AS runs_sampled,AVG(ar.duration_seconds) AS avg_duration
                   FROM test_cases tc JOIN automation_runs ar ON ar.test_case_id=tc.id
                   GROUP BY tc.test_case_version_id,tc.reviewed_workbook_path"""
            ):
                run_metrics[(row["test_case_version_id"], row["reviewed_workbook_path"] or "")] = dict(row)
        result = []
        from pathlib import Path
        for row in rows:
            item = dict(row)
            key = (item["test_case_version_id"], item["workbook_path"] or "")
            counts = metrics.get(key, {})
            runs = run_metrics.get(key, {})
            item["test_case_folder"] = Path(item["folder_path"]).name
            item["test_case_count"] = counts.get("total", 0)
            item["pass"] = counts.get("passed", 0) or 0
            item["fail"] = counts.get("failed", 0) or 0
            item["blocked"] = counts.get("blocked", 0) or 0
            item["not_run"] = counts.get("not_run", 0) or 0
            item["automated_count"] = counts.get("automated", 0) or 0
            item["runs_sampled"] = runs.get("runs_sampled", 0) or 0
            duration = runs.get("avg_duration")
            item["avg_duration"] = round(duration, 3) if duration is not None else None
            result.append(item)
        return result

    # --------------------------------------------------
    # Test Execution
    # --------------------------------------------------

    def _test_execution(self, conn, trend_days=14):
        has_cases = self._has_table(conn, "test_cases")

        by_type = {}
        if has_cases:
            for automation_type in AUTOMATION_TYPES:
                counts = {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0}
                for row in conn.execute(
                    "SELECT COALESCE(last_result, 'Not Run') AS result, COUNT(*) AS count "
                    "FROM test_cases WHERE automation_type=? "
                    "GROUP BY COALESCE(last_result, 'Not Run')",
                    (automation_type,),
                ):
                    result = row["result"]
                    if result in counts:
                        counts[result] = row["count"]
                by_type[automation_type] = counts

        overall = self._result_breakdown(conn) if has_cases else {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0}

        # Real execution history — Core/automation_execution_repository.py's
        # automation_runs table, the ONE persisted run log in this app
        # (see that file's module docstring). Trend gaps (days with no
        # runs) are filled with 0 here rather than left absent, so the
        # frontend can plot a continuous line without guessing.
        trend_rows = self.runs.execution_trend(days=trend_days)
        trend_by_date = {row["run_date"]: row for row in trend_rows}
        from datetime import date, timedelta
        today = date.today()
        trend = []
        for offset in range(trend_days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            row = trend_by_date.get(day)
            trend.append({
                "date": day,
                "total": row["total"] if row else 0,
                "passed": row["passed"] if row else 0,
                "failed": row["failed"] if row else 0,
            })

        duration = {row["automation_type"] or "Unknown": {
            "sample_count": row["sample_count"],
            "avg_seconds": round(row["avg_seconds"], 1) if row["avg_seconds"] is not None else None,
            "min_seconds": row["min_seconds"],
            "max_seconds": row["max_seconds"],
        } for row in self.runs.duration_stats()}

        recent_summary = self.runs.list_runs(limit=10)["runs"]

        return {
            "overall": overall,
            "by_automation_type": by_type,
            "execution_trend": trend,
            "execution_trend_days": trend_days,
            "duration_by_automation_type": duration,
            "recent_execution_summary": recent_summary,
            # QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4:
            # Dashboard's Test Execution tab must be "hierarchy-aware" —
            # by_automation_type above answers "how is Playwright/API/SQL
            # doing overall", but not "how is Domain X / Module Y doing".
            # Added here, additively, alongside (never replacing)
            # by_automation_type.
            "hierarchy": self._execution_hierarchy(conn) if has_cases else {
                "by_domain": [], "by_domain_module": [], "by_domain_module_knowledge": [],
            },
        }

    def _execution_hierarchy(self, conn):
        """
        QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4 —
        the Dashboard's Test Execution tab needs a genuine Domain ->
        Module -> Knowledge Name breakdown, not just the flat
        by-automation-type table _test_execution() already returns.
        Mirrors _knowledge_coverage()'s exact 3-tier grouping pattern
        (by_domain / by_domain_module / by_domain_module_knowledge)
        and reuses the SAME result vocabulary (test_cases.last_result,
        via _result_breakdown()'s Pass/Fail/Blocked/Not Run counts) —
        no new table, no invented metric, no second source of truth.
        """
        rows = conn.execute(
            "SELECT domain, module, knowledge_name, "
            "COALESCE(last_result, 'Not Run') AS result, COUNT(*) AS cnt "
            "FROM test_cases "
            "WHERE COALESCE(domain,'') <> '' AND COALESCE(module,'') <> '' "
            "GROUP BY domain, module, knowledge_name, COALESCE(last_result, 'Not Run')"
        ).fetchall()

        def _empty_counts():
            return {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0}

        domain_buckets, module_buckets, knowledge_buckets = {}, {}, {}
        for row in rows:
            domain = row["domain"]
            module = row["module"]
            knowledge_name = row["knowledge_name"] or "Unspecified"
            result = row["result"]
            cnt = row["cnt"]
            for buckets, key in (
                (domain_buckets, domain),
                (module_buckets, (domain, module)),
                (knowledge_buckets, (domain, module, knowledge_name)),
            ):
                bucket = buckets.setdefault(key, _empty_counts())
                if result in bucket:
                    bucket[result] += cnt

        def _summarize(counts):
            total = sum(counts.values())
            executed = counts["Pass"] + counts["Fail"] + counts["Blocked"]
            return {
                "test_case_count": total,
                "last_results": dict(counts),
                "pass_rate_pct": round((counts["Pass"] / executed) * 100, 1) if executed else None,
            }

        by_domain = [
            dict(domain=domain, **_summarize(counts))
            for domain, counts in sorted(domain_buckets.items())
        ]
        by_domain_module = [
            dict(domain=d, module=m, **_summarize(counts))
            for (d, m), counts in sorted(module_buckets.items())
        ]
        by_domain_module_knowledge = [
            dict(domain=d, module=m, knowledge_name=k, **_summarize(counts))
            for (d, m, k), counts in sorted(knowledge_buckets.items())
        ]

        return {
            "by_domain": by_domain,
            "by_domain_module": by_domain_module,
            "by_domain_module_knowledge": by_domain_module_knowledge,
        }

    # --------------------------------------------------
    # Knowledge Coverage
    # --------------------------------------------------

    def _knowledge_coverage(self, conn):
        """
        Requirement 9 — "do not manufacture coverage percentages —
        define the calculation explicitly before displaying one."

        Coverage is computed at the (domain, module, knowledge_name)
        grouping level, correlating Knowledge -> Test Cases exactly as
        specified:

            coverage_pct(domain) =
                COUNT(DISTINCT (module, knowledge_name) groups in
                      knowledge_items for this domain THAT HAVE
                      >= 1 matching row in test_cases with the same
                      domain/module/knowledge_name)
                /
                COUNT(DISTINCT (module, knowledge_name) groups in
                      knowledge_items for this domain)
                * 100

        A second figure, automation_coverage_pct, narrows the
        numerator further to groups that have >= 1 matching test case
        that is also execution_type='Automatable' — i.e. "of what's
        covered by test cases at all, how much of that is actually
        automated." Both the raw covered/total counts and the
        percentages are returned so neither figure is a bare,
        unexplained number; the frontend/report can show "12 / 18
        knowledge groups (67%)" rather than just "67%".
        """
        has_knowledge = self._has_table(conn, "knowledge_items")
        has_cases = self._has_table(conn, "test_cases")

        if not has_knowledge:
            return {"by_domain": [], "definition": "No knowledge_items table found."}

        knowledge_groups = conn.execute(
            "SELECT DISTINCT domain, module, knowledge_name FROM knowledge_items "
            "WHERE COALESCE(domain,'') <> '' AND COALESCE(module,'') <> '' AND COALESCE(knowledge_name,'') <> ''"
        ).fetchall()

        # Counted (not just DISTINCT-membership) so the per-knowledge-item
        # traceability drill-down below can show a real test case count per
        # group, not just a covered/not-covered flag.
        case_counts = {}
        automated_counts = {}
        if has_cases:
            for row in conn.execute(
                "SELECT domain, module, knowledge_name, COUNT(*) AS cnt FROM test_cases "
                "GROUP BY domain, module, knowledge_name"
            ):
                case_counts[(row["domain"], row["module"], row["knowledge_name"])] = row["cnt"]
            for row in conn.execute(
                "SELECT domain, module, knowledge_name, COUNT(*) AS cnt FROM test_cases "
                "WHERE execution IN ('Playwright','API','SQL') GROUP BY domain, module, knowledge_name"
            ):
                automated_counts[(row["domain"], row["module"], row["knowledge_name"])] = row["cnt"]
        case_groups = set(case_counts.keys())
        automated_groups = set(automated_counts.keys())

        by_domain = {}
        by_domain_module = {}
        for row in knowledge_groups:
            key = (row["domain"], row["module"], row["knowledge_name"])
            bucket = by_domain.setdefault(row["domain"], {"total": 0, "covered": 0, "automated": 0})
            module_bucket = by_domain_module.setdefault((row["domain"], row["module"]), {"total": 0, "covered": 0, "automated": 0})
            for b in (bucket, module_bucket):
                b["total"] += 1
                if key in case_groups:
                    b["covered"] += 1
                if key in automated_groups:
                    b["automated"] += 1

        def _summarize(total, covered, automated):
            return {
                "knowledge_groups_total": total,
                "knowledge_groups_covered": covered,
                "coverage_pct": round((covered / total) * 100, 1) if total else 0,
                "knowledge_groups_automated": automated,
                "automation_coverage_pct": round((automated / total) * 100, 1) if total else 0,
            }

        result = [
            dict(domain=domain, **_summarize(b["total"], b["covered"], b["automated"]))
            for domain, b in sorted(by_domain.items())
        ]

        # Requirement 9 — Reports & Analytics wants "deeper filtering/
        # drill-down than Dashboard": the same coverage calculation,
        # one level more granular (Domain + Module), computed in the
        # same pass over the same data rather than a second query.
        module_result = [
            dict(domain=domain, module=module, **_summarize(b["total"], b["covered"], b["automated"]))
            for (domain, module), b in sorted(by_domain_module.items())
        ]

        # Requirement 9 / Reports & Analytics "Traceability" tab — the
        # same Knowledge -> Test Case correlation, taken one level
        # further to the actual (domain, module, knowledge_name) group
        # itself (not just aggregated into a domain/module count), so
        # the report can show exactly WHICH knowledge items have zero
        # linked test cases (the real traceability gap), each with its
        # real test-case count — not a second/duplicate coverage
        # formula, just the same case_counts/automated_counts already
        # computed above, exposed per-group instead of only summed.
        knowledge_level = sorted(
            (
                {
                    "domain": row["domain"],
                    "module": row["module"],
                    "knowledge_name": row["knowledge_name"],
                    "test_case_count": case_counts.get(
                        (row["domain"], row["module"], row["knowledge_name"]), 0
                    ),
                    "covered": (row["domain"], row["module"], row["knowledge_name"]) in case_groups,
                    "automated_test_case_count": automated_counts.get(
                        (row["domain"], row["module"], row["knowledge_name"]), 0
                    ),
                    "automated": (row["domain"], row["module"], row["knowledge_name"]) in automated_groups,
                }
                for row in knowledge_groups
            ),
            key=lambda r: (r["domain"] or "", r["module"] or "", r["knowledge_name"] or ""),
        )

        return {
            "by_domain": result,
            "by_domain_module": module_result,
            "by_domain_module_knowledge": knowledge_level,
            "definition": (
                "coverage_pct = knowledge (domain, module, knowledge_name) groups with >= 1 matching "
                "test case / total such groups for that domain (or domain+module), x100. "
                "automation_coverage_pct narrows the numerator to groups with >= 1 Automatable test case."
            ),
        }

    # --------------------------------------------------
    # Recent Runs
    # --------------------------------------------------

    def _recent_runs(self, limit):
        # Requirement 9 — "do not duplicate execution history into a
        # second independent storage model." The previous version of
        # this method queried test_cases.last_run_date directly (a
        # second, thinner, ad hoc view of "recent runs" that had no
        # automation_type/duration/run-by and only ever showed the
        # single latest run per test case). It now simply calls the
        # ONE real execution-history repository/table instead.
        return self.runs.list_runs(limit=limit)["runs"]

    # --------------------------------------------------
    # Recent Activity
    # --------------------------------------------------

    def _recent_activity(self, conn, limit):
        if not self._has_table(conn, "audit_logs"):
            return []
        return conn.execute(
            """
            SELECT id, username, action, resource, detail, created_date
            FROM audit_logs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
