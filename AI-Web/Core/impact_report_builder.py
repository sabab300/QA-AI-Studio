"""
QA AI Studio
Impact Report Builder

Version: 1.0

QA-AI-STUDIO-IMPACT-ANALYSIS-V1: Stage 8 of the Impact Analysis
pipeline. Pure formatting, no I/O -- mirrors the single-responsibility
style of Core/context_builder.py / Core/citation_engine.py. Takes the
structured result Core/impact_analysis_service.py assembles and renders
the human-readable report text in the exact shape requested (Executive
Summary / Impact Findings table / Detailed Finding blocks / No
Documented Impact section). Never invents a page number or any other
detail not present in the structured findings it is given.
"""

from Core.logger import Logger
from Core import impact_management


class ImpactReportBuilder:

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Source / Target Identity Lines
    # --------------------------------------------------

    def _source_line(self, source):

        parts = [
            source.get("domain") or "",
            source.get("module") or "",
            source.get("knowledge_name") or "",
            source.get("source_document") or "",
        ]

        return " > ".join(p for p in parts if p)

    def _target_line(self, target):

        parts = [
            target.get("domain") or "",
            target.get("module") or "All Modules",
        ]

        return " > ".join(p for p in parts if p)

    # --------------------------------------------------
    # Evidence Reference Block
    # --------------------------------------------------

    def _evidence_ref(self, evidence_item):

        if not isinstance(evidence_item, dict):
            return str(evidence_item)

        parts = [evidence_item.get("file_name") or evidence_item.get("source_document") or "Unknown"]

        for label, key in (
            ("Domain", "domain"), ("Module", "module"),
            ("Knowledge", "knowledge_name"), ("Version", "version"),
            ("Type", "document_type"),
        ):
            value = evidence_item.get(key)
            if value:
                parts.append(f"{label}={value}")

        # Never invent a page number -- only include it when the
        # retrieved chunk metadata actually carried one.
        page = evidence_item.get("page") or evidence_item.get("page_number")
        if page:
            parts.append(f"Page={page}")

        return " | ".join(parts)

    # --------------------------------------------------
    # Executive Summary
    # --------------------------------------------------

    CLASS_LABELS = (
        ("CONFIRMED IMPACT", "confirmed impact"),
        ("POTENTIAL IMPACT", "potential impact"),
        ("ALREADY COVERED", "already covered (no change required)"),
        ("INSUFFICIENT EVIDENCE", "insufficient evidence"),
        ("NO DOCUMENTED IMPACT", "no documented impact"),
    )

    def _executive_summary(self, findings, coverage, summary=None):

        total = len(findings)

        if total == 0:
            text = (
                "No source changes could be assessed against the currently "
                "indexed target knowledge."
            )
        else:
            counts = {name: sum(1 for f in findings if f.get("classification") == name) for name, _ in self.CLASS_LABELS}
            parts = ["%d %s" % (counts[name], label) for name, label in self.CLASS_LABELS if counts[name]]
            text = "%d source requirement finding%s reviewed: %s." % (total, "s" if total != 1 else "", ", ".join(parts) or "none classified")
            if (summary or {}).get("source_supported_potential") is not None:
                text += " Source-supported candidate functionality: %d finding(s); this count does not confirm target impact." % summary["source_supported_potential"]
            changes = (summary or {}).get("document_changes") or {}
            if changes.get("prior_version") is not None:
                text += (" Document changes since version %s: %d confirmed, %d wording-only, %d new, %d unchanged. A document change is not by itself a system impact; "
                         "the classification of each finding is its impact on the target." % (changes["prior_version"], changes.get("confirmed_changes", 0), changes.get("wording_only", 0),
                                                                                              changes.get("new_requirements", 0), changes.get("unchanged", 0)))
            if (summary or {}).get("headline") and counts.get("INSUFFICIENT EVIDENCE") and not coverage.get("target_chunks_analyzed"):
                text += " The target scope holds no independent evidence for these changes (no target document, other change request or earlier version matched), so neither an impact nor the absence of one can be concluded."

        warnings = coverage.get("warnings") or []

        if warnings:
            text += "\n\nCoverage warnings (%d):\n" % len(warnings) + "\n".join(f"- {w}" for w in warnings)

        return text

    # --------------------------------------------------
    # Findings Table
    # --------------------------------------------------

    def _findings_table(self, findings):

        material = [f for f in findings if f.get("classification") != "NO DOCUMENTED IMPACT"]

        if not material:
            return "(No findings with an impact classification.)"

        rows = [
            "| # | Classification | Analysis Type | Source Change | Target Component |",
            "|---|----------------|---------------|----------------|-------------------|",
        ]

        for finding in material:
            rows.append(
                "| {finding_id} | {classification} | {impact_area} | {source_change} | {target_component} |".format(
                    finding_id=finding.get("finding_id", ""),
                    classification=finding.get("classification", ""),
                    impact_area=finding.get("analysis_type") or finding.get("impact_area", ""),
                    source_change=(finding.get("source_change") or {}).get("name", ""),
                    target_component=finding.get("target_component", "") or "-",
                )
            )

        return "\n".join(rows)

    # --------------------------------------------------
    # Detailed Findings
    # --------------------------------------------------

    def _detailed_findings(self, findings, target):

        material = [f for f in findings if f.get("classification") != "NO DOCUMENTED IMPACT"]

        if not material:
            return ""

        blocks = []

        for finding in material:

            source_change = finding.get("source_change") or {}
            classification = finding.get("classification", "")

            lines = [
                f"Finding: {finding.get('finding_id', '')}",
                f"\nExecutive Summary:\n{finding.get('executive_summary') or finding.get('reasoning_summary', '')}",
                f"\nSource Change:\n{source_change.get('name', '')} -- {source_change.get('change', '')}",
            ]

            if finding.get("analysis_type"):
                lines.append(f"\nAnalysis Type:\n{finding['analysis_type']}")
            potential = finding.get("potential_affected_functionality")
            if potential:
                lines.append("\nPotential Affected Functionality (source-supported, not confirmed target impact):\n" + potential.get("description", "") + "\n" + potential.get("note", ""))
            if finding.get("target_assessment"):
                lines.append("\nTarget Assessment:\n" + finding["target_assessment"])

            lines.append("\nExisting Target Behaviour:\n" + (finding.get("existing_target_behaviour") or "Not stated by the evidence."))
            lines.append("\nRequired Target Change:\n" + (finding.get("required_target_change") or "Not stated by the evidence."))

            change = finding.get("document_change") or {}
            if change.get("label"):
                doc_line = f"\nDocument Change (what the documents say):\n{change['label']}"
                if change.get("prior_version") is not None and change.get("status") not in ("NO_PRIOR_VERSION", "UNKNOWN"):
                    doc_line += f" (compared with version {change['prior_version']})"
                for flip in change.get("polarity") or []:
                    doc_line += f"\n- {flip.get('label')}: {flip.get('before')} to {flip.get('after')}"
                if change.get("polarity_note"):
                    doc_line += "\n" + change["polarity_note"]
                lines.append(doc_line)

            lines.append(
                f"\nImpact Classification (impact on the target system):\n{classification}"
                + (f" (evidence: {finding.get('evidence_strength')})" if finding.get("evidence_strength") else "")
                + f"\nTarget: {self._target_line(target)}"
                + (f" > {finding.get('target_component')}" if finding.get("target_component") else "")
            )

            basis = finding.get("classification_basis") or []
            if basis:
                lines.append("Basis:\n" + "\n".join(f"- {b}" for b in basis))

            if classification == "POTENTIAL IMPACT":
                lines.append("Requires verification.")

            evidence_lines = []
            quote = finding.get("supporting_quote") or {}
            if quote.get("text"):
                evidence_lines.append(f"- Quotation from {quote.get('evidence_kind') or 'the target'} ({self._evidence_ref(quote)}): \"{quote['text']}\"")
            for e in finding.get("source_evidence") or []:
                evidence_lines.append(f"- Source: {e}")
            for e in finding.get("target_evidence") or []:
                evidence_lines.append(f"- Target: {self._evidence_ref(e)}")
            for e in finding.get("excluded_evidence") or []:
                evidence_lines.append(f"- Not used as target behaviour ({e.get('role_label', e.get('role', ''))}): {self._evidence_ref(e)}")
            for h in finding.get("source_history") or []:
                evidence_lines.append(f"- The same requirement text also appears in {h.get('file_name')} (version {h.get('version')})")
            if evidence_lines:
                lines.append("\nSupporting Evidence:\n" + "\n".join(evidence_lines))

            if finding.get("conflicting_evidence"):
                lines.append(
                    "\nCONFLICTING EVIDENCE:\nDifferent versions of the same target passage disagree -- see the target evidence "
                    "above; neither version was arbitrarily preferred."
                )

            lines.append("\nRecommended Action:\n" + (finding.get("recommended_action") or "Review this finding with the target owner."))

            recommended = finding.get("recommended_verification") or []
            if recommended:
                lines.append("Recommended Verification:\n" + "\n".join(f"- {r}" for r in recommended))

            test_coverage = finding.get("test_case_coverage") or {}
            if test_coverage.get("classification"):
                tc_line = f"\nTest Coverage:\n{test_coverage['classification']}"
                if test_coverage.get("test_cases"):
                    tc_line += " (" + ", ".join(test_coverage["test_cases"]) + ")"
                if test_coverage.get("note"):
                    tc_line += "\n" + test_coverage["note"]
                for m in test_coverage.get("matches") or []:
                    where = "workbook version %s of source %s" % (m.get("workbook_version") if m.get("workbook_version") is not None else "unknown",
                                                                 ", ".join(str(i) for i in m.get("source_knowledge_ids") or []) or "unknown")
                    flag = "" if m.get("scope_status") in (None, "CURRENT") else " [%s]" % str(m.get("scope_status")).lower()
                    tc_line += "\n- %s - %s (%s)%s" % (m.get("test_case_id"), m.get("scenario") or "", where, flag)
                for m in test_coverage.get("superseded_matches") or []:
                    tc_line += "\n- Not counted, older workbook version %s (current is %s): %s - %s" % (m.get("workbook_version"), m.get("latest_workbook_version"), m.get("test_case_id"), m.get("scenario") or "")
                lines.append(tc_line)

            automation = finding.get("automation_impact") or {}
            if automation.get("status") and automation["status"] not in ("NOT_APPLICABLE", "NO_MATCHED_TESTS"):
                au_line = "\nAutomation Impact (read-only):\n%s - %s" % (automation["status"].replace("_", " "), automation.get("note") or "")
                for entry in automation.get("tests") or []:
                    head = "%s%s%s" % (entry.get("test"), (" (test case version %s)" % entry["test_case_version_id"]) if entry.get("test_case_version_id") else "", (" - " + entry["scenario"]) if entry.get("scenario") else "")
                    if not entry.get("assets"):
                        au_line += "\n- %s: no automation of its own" % head
                    for asset in entry.get("assets") or []:
                        terms = (" [changed terms: %s]" % ", ".join(asset["matched_terms"][:4])) if asset.get("matched_terms") else ""
                        state = " (declared only, no asset record)" if asset.get("state") == "DECLARED" else "" if asset.get("text_checked") else " (text not checked)"
                        au_line += "\n- %s: %s%s%s" % (head, asset.get("label"), terms, state)
                    for asset in entry.get("version_assets") or []:
                        if asset.get("matched_terms"):
                            au_line += "\n- %s: version-level, not this scenario's own - %s [changed terms: %s]" % (entry.get("test"), asset.get("label"), ", ".join(asset["matched_terms"][:4]))
                lines.append(au_line)

            blocks.append("\n".join(lines))

        return ("\n\n" + "-" * 48 + "\n\n").join(blocks)

    # --------------------------------------------------
    # Version Comparison Section
    # --------------------------------------------------

    def _version_comparison(self, result):

        diff = result.get("version_difference")

        if not diff:
            return "(No earlier version of the source document was available to compare with.)"

        source_version = (result.get("source") or {}).get("version") or "?"
        counts = diff.get("counts") or {}
        lines = [
            f"Version {diff.get('prior_version')} compared with version {source_version}: "
            f"{counts.get('changed', 0)} changed, {counts.get('added', 0)} added, {counts.get('removed', 0)} removed requirement statement(s); "
            f"{counts.get('other_changed', 0) + counts.get('other_added', 0) + counts.get('other_removed', 0)} other text difference(s) (not requirements)."
        ]
        if diff.get("complete") is False:
            lines.append("WARNING: the comparison is incomplete (one of the documents could not be read in full); differences, especially removals, may be missing.")

        for d in diff.get("changed") or []:
            lines.append(f"- CHANGED{'' if d.get('material') else ' (wording only, no demonstrated change of meaning)'}:\n    before: {d.get('prior')}\n    now:    {d.get('current')}")
            for flip in d.get("polarity") or []:
                lines.append(f"    setting: {flip.get('label')}: {flip.get('before')} to {flip.get('after')}")
            if d.get("polarity_note"):
                lines.append(f"    note: {d['polarity_note']}")
        for d in diff.get("added") or []:
            lines.append(f"- ADDED: {d.get('current')}")
        for d in diff.get("removed") or []:
            lines.append(f"- REMOVED: {d.get('prior')}")

        removed = result.get("removed_requirements") or []
        if removed:
            lines.append("Existing tests and automation that matched a removed requirement (to be reviewed for retirement or update; nothing is changed automatically):")
            for r in removed:
                cov = r.get("test_case_coverage") or {}
                lines.append(f"- {r.get('statement')}")
                if not cov.get("matches"):
                    lines.append("    no current test matched" + (f" ({cov['note']})" if cov.get("note") else "") if cov.get("lookup_ok", True) else "    the test repository could not be read; coverage is unknown")
                for m in cov.get("matches") or []:
                    lines.append(f"    test {m.get('test_case_id')} - {m.get('scenario') or ''} (workbook version {m.get('workbook_version') if m.get('workbook_version') is not None else 'unknown'} of source {', '.join(str(i) for i in m.get('source_knowledge_ids') or []) or 'unknown'})")
                for e in (r.get("automation_impact") or {}).get("tests") or []:
                    for a in e.get("assets") or []:
                        lines.append(f"    automation: {a.get('label')} for {e.get('test')}" + (" [mentions removed terms: %s]" % ", ".join(a["matched_terms"][:4]) if a.get("matched_terms") else ""))

        other = diff.get("other") or {}
        if any(other.get(k) for k in ("changed", "added", "removed")):
            lines.append("Other text differences (not requirements; listed, not judged):")
            for d in other.get("changed") or []:
                lines.append(f"- changed: {d.get('prior')}  ->  {d.get('current')}")
            for d in other.get("added") or []:
                lines.append(f"- added: {d.get('current')}")
            for d in other.get("removed") or []:
                lines.append(f"- removed: {d.get('prior')}")

        if diff.get("truncated"):
            lines.append(f"Only the first {diff.get('shown_limit')} of each list are shown; the counts above are complete.")

        if diff.get("unassessed_count"):
            lines.append(f"\nUNASSESSED CHANGES: {diff['unassessed_count']} functional difference(s) are not covered by any finding and were not assessed against the target:")
            for d in diff.get("unassessed") or []:
                if d.get("kind") == "REMOVED":
                    lines.append(f"- removed from version {source_version}: {d.get('prior')}")
                elif d.get("kind") == "ADDED":
                    lines.append(f"- new in version {source_version}: {d.get('current')}")
                else:
                    lines.append(f"- changed: {d.get('prior')}  ->  {d.get('current')}")

        return "\n".join(lines)

    # --------------------------------------------------
    # Management Summary (shared with Change Impact)
    # --------------------------------------------------

    def _management_section(self, result):

        try:
            block = result.get("management") or impact_management.management_block(result, "impact_analysis")
            return "\n".join(impact_management.management_lines(block))
        except Exception:
            self.logger.exception("Impact management summary failed (non-fatal).")
            return "Management summary could not be produced for this result."

    # --------------------------------------------------
    # Source / Test Coverage Section
    # --------------------------------------------------

    def _coverage_section(self, result):

        coverage = result.get("coverage") or {}
        source = coverage.get("source") or {}
        requirements = coverage.get("source_requirements") or {}
        tests = coverage.get("test_cases") or {}
        lines = []
        processing = coverage.get("target_processing") or {}
        if processing:
            lines.append("Target assessment processing: %s of %s findings processed; %s of %s batches; %s incomplete; status %s." %
                         (processing.get("completed"), processing.get("total"), processing.get("batches_completed"), processing.get("batches_total"), processing.get("failed"), processing.get("status")))

        if source:
            lines.append(f"Source document: {source.get('chunks_read', 0)} of {source.get('chunks_total', 0)} chunk(s) read ({source.get('read_mode')}); "
                         f"{source.get('sections_analysed', 0)} of {source.get('sections_total', 0)} section(s) analysed"
                         + (f"; {source.get('chunks_not_examined')} chunk(s) in sections that were not analysed" if source.get("chunks_not_examined") else "") + ".")
        if requirements:
            why = requirements.get("by_reason") or {}
            lines.append(f"Requirement statements in the source: {requirements.get('detected', 0)} detected, {requirements.get('assessed', 0)} assessed by a finding, "
                         f"{requirements.get('not_assessed_count', 0)} not assessed (not read: {why.get('NOT_EXAMINED', 0)}; left out by the size limit: {why.get('NOT_ASSESSED_LIMIT', 0)}; "
                         f"not extracted: {why.get('NOT_EXTRACTED', 0)}).")
            reasons = {"NOT_EXAMINED": "not read", "NOT_ASSESSED_LIMIT": "left out by the size limit", "NOT_EXTRACTED": "not extracted"}
            for item in requirements.get("not_assessed") or []:
                lines.append(f"- not assessed ({reasons.get(item.get('reason'), item.get('reason'))}, chunk {item.get('chunk')}): {item.get('text')}")
            if requirements.get("not_assessed_count", 0) > len(requirements.get("not_assessed") or []):
                lines.append(f"  ... {requirements['not_assessed_count'] - len(requirements['not_assessed'])} more not shown.")
            if requirements.get("assessment_note"):
                lines.append(requirements["assessment_note"])
            inventory = requirements.get("inventory") or {}
            if inventory:
                lines.append("Source statement inventory: " + ", ".join("%s: %s" % (k.lower(), v) for k, v in inventory.get("counts", {}).items()) + ".")
                for item in inventory.get("items", []) + inventory.get("duplicates", []):
                    lines.append("- %s %s (chunk %s; %s; %s; findings %s; versions %s): %s" %
                                 (item.get("statement_id", item.get("duplicate_of", "")), item.get("kind"), item.get("chunk"), item.get("assessment", "duplicate"), item.get("target_scope_status", "not applicable"),
                                  ", ".join(item.get("finding_ids") or []) or "none", ", ".join(item.get("version_statuses") or []) or "not compared", item.get("text")))
        for item in coverage.get("changes_not_assessed") or []:
            lines.append(f"- change not individually assessed (size limit): {item.get('name')}")
        for item in coverage.get("target_statements_not_assessed") or []:
            lines.append("- source statement retained; independent target search not executed (size limit): " + item)
        if tests:
            if tests.get("status_note"):
                lines.append("Test search status: " + tests["status_note"])
            if tests.get("total") is None and tests.get("examined", 0) == 0 and not tests.get("findings_searched"):
                not_needed = tests.get("status") == "NOT_EXECUTED" or (not tests.get("status") and tests.get("findings_not_assessed") and not tests.get("findings_search_attempted"))
                lines.append("Test cases: not searched (no changed requirement needed a test-case search)." if not_needed else "Test cases: not searched.")
            else:
                lines.append(f"Test cases: {tests.get('examined', 0)} of {tests.get('total') if tests.get('total') is not None else 'unknown'} searched for {tests.get('findings_searched', 0)} finding(s); "
                             f"{tests.get('findings_not_assessed', 0)} unchanged finding(s) not searched" + ("." if tests.get("complete", True) else "; the search is INCOMPLETE."))
        issues = (coverage.get("consistency") or {}).get("issues") or []
        lines.append("Result consistency check: " + ("passed." if not issues else "%d problem(s): %s" % (len(issues), " ".join(issues))))

        return "\n".join(lines)

    # --------------------------------------------------
    # No Documented Impact Section
    # --------------------------------------------------

    def _no_documented_impact(self, findings):

        undocumented = [f for f in findings if f.get("classification") == "NO DOCUMENTED IMPACT"]

        if not undocumented:
            return "(Every assessed source change has a classification above.)"

        lines = []

        for finding in undocumented:

            source_change = finding.get("source_change") or {}

            lines.append(
                f"- {finding.get('finding_id', '')} {source_change.get('name', finding.get('impact_area', 'Unnamed change'))}: "
                "No documented impact was identified from currently indexed target knowledge."
            )

        return "\n".join(lines)

    # --------------------------------------------------
    # Build
    # --------------------------------------------------

    def build(self, result):

        try:

            source = result.get("source") or {}
            target = result.get("target") or {}
            findings = result.get("findings") or []
            coverage = result.get("coverage") or {}

            report = f"""================================================
IMPACT ANALYSIS
================================================

Source Change:
{self._source_line(source)}

Target:
{self._target_line(target)}

Analysis Coverage:
- Source chunks analyzed: {coverage.get('source_chunks_analyzed', 0)}
- Target chunks used as evidence (target documents and other change requests): {coverage.get('target_chunks_analyzed', 0)}
- Target documents used as evidence: {coverage.get('target_documents_matched', 0)}
- Target chunks excluded (the source itself, its other versions, sibling documents of the same change request): {coverage.get('target_chunks_excluded', 0)}
- Earlier version compared: {(result.get('analysis_context') or {}).get('prior_version_compared') or 'none'}

------------------------------------------------
EXECUTIVE SUMMARY
------------------------------------------------

{self._executive_summary(findings, coverage, result.get('summary'))}

------------------------------------------------
IMPACT FINDINGS
------------------------------------------------

{self._findings_table(findings)}

------------------------------------------------
DETAILED FINDING
------------------------------------------------

{self._detailed_findings(findings, target)}

------------------------------------------------
VERSION COMPARISON
------------------------------------------------

{self._version_comparison(result)}

------------------------------------------------
NO DOCUMENTED IMPACT
------------------------------------------------

{self._no_documented_impact(findings)}

------------------------------------------------
SOURCE AND TEST COVERAGE
------------------------------------------------

{self._coverage_section(result)}

------------------------------------------------
MANAGEMENT SUMMARY AND EFFORT
------------------------------------------------

{self._management_section(result)}
"""

            return report.strip()

        except Exception:

            self.logger.exception("Impact Report Builder failed.")

            return "Impact Analysis report could not be formatted."
