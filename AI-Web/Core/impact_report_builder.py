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

    def _executive_summary(self, findings, coverage):

        confirmed = [f for f in findings if f.get("classification") == "CONFIRMED IMPACT"]
        potential = [f for f in findings if f.get("classification") == "POTENTIAL IMPACT"]
        undocumented = [f for f in findings if f.get("classification") == "NO DOCUMENTED IMPACT"]

        total = len(findings)

        if total == 0:
            summary = (
                "No source changes could be assessed against the currently "
                "indexed target knowledge."
            )
        else:
            summary_parts = [
                f"{total} source change{'s' if total != 1 else ''} assessed against "
                f"the target knowledge: {len(confirmed)} confirmed impact"
                f"{'s' if len(confirmed) != 1 else ''}, {len(potential)} potential "
                f"impact{'s' if len(potential) != 1 else ''}."
            ]

            if undocumented:
                summary_parts.append(
                    f"{len(undocumented)} source change"
                    f"{'s' if len(undocumented) != 1 else ''} had no documented "
                    "dependency in the currently indexed target knowledge."
                )

            summary = " ".join(summary_parts)

        warnings = coverage.get("warnings") or []

        if warnings:
            summary += "\n\nCoverage warnings:\n" + "\n".join(f"- {w}" for w in warnings)

        return summary

    # --------------------------------------------------
    # Findings Table
    # --------------------------------------------------

    def _findings_table(self, findings):

        material = [f for f in findings if f.get("classification") != "NO DOCUMENTED IMPACT"]

        if not material:
            return "(No CONFIRMED or POTENTIAL impact findings.)"

        rows = [
            "| # | Classification | Impact Area | Source Change | Target Component |",
            "|---|----------------|-------------|----------------|-------------------|",
        ]

        for finding in material:
            rows.append(
                "| {finding_id} | {classification} | {impact_area} | {source_change} | {target_component} |".format(
                    finding_id=finding.get("finding_id", ""),
                    classification=finding.get("classification", ""),
                    impact_area=finding.get("impact_area", ""),
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

            lines = [
                f"Finding:\n{finding.get('finding_id', '')}",
                f"\nClassification:\n{finding.get('classification', '')}"
                + (f" ({finding.get('evidence_strength')})" if finding.get("evidence_strength") else ""),
                f"\nImpact Area:\n{finding.get('impact_area', '')}",
                f"\nSource Change:\n{source_change.get('name', '')} -- {source_change.get('change', '')}",
                f"\nTarget:\n{self._target_line(target)}"
                + (f" > {finding.get('target_component')}" if finding.get("target_component") else ""),
                f"\nWhy it is impacted:\n{finding.get('reasoning_summary', '')}",
            ]

            if finding.get("classification") == "POTENTIAL IMPACT":
                lines.append("\nRequires verification.")

            source_evidence = finding.get("source_evidence") or []
            if source_evidence:
                lines.append(
                    "\nSource Evidence:\n" + "\n".join(f"- {e}" for e in source_evidence)
                )

            target_evidence = finding.get("target_evidence") or []
            if target_evidence:
                lines.append(
                    "\nTarget Evidence:\n"
                    + "\n".join(f"- {self._evidence_ref(e)}" for e in target_evidence)
                )

            recommended = finding.get("recommended_verification") or []
            if recommended:
                lines.append(
                    "\nRecommended Verification:\n" + "\n".join(f"- {r}" for r in recommended)
                )

            test_coverage = finding.get("test_case_coverage") or {}
            if test_coverage.get("classification"):
                tc_line = f"\nTest Coverage:\n{test_coverage['classification']}"
                if test_coverage.get("test_cases"):
                    tc_line += " (" + ", ".join(test_coverage["test_cases"]) + ")"
                lines.append(tc_line)

            if finding.get("conflicting_evidence"):
                lines.append(
                    "\nCONFLICTING EVIDENCE:\nMultiple target documents provide "
                    "conflicting information for this finding -- see target evidence "
                    "above; neither version was arbitrarily preferred."
                )

            blocks.append("\n".join(lines))

        return ("\n\n" + "-" * 48 + "\n\n").join(blocks)

    # --------------------------------------------------
    # No Documented Impact Section
    # --------------------------------------------------

    def _no_documented_impact(self, findings):

        undocumented = [f for f in findings if f.get("classification") == "NO DOCUMENTED IMPACT"]

        if not undocumented:
            return "(All assessed source changes have documented target findings above.)"

        lines = []

        for finding in undocumented:

            source_change = finding.get("source_change") or {}

            lines.append(
                f"- {source_change.get('name', finding.get('impact_area', 'Unnamed change'))}: "
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
- Target chunks analyzed: {coverage.get('target_chunks_analyzed', 0)}
- Target documents matched: {coverage.get('target_documents_matched', 0)}

------------------------------------------------
EXECUTIVE SUMMARY
------------------------------------------------

{self._executive_summary(findings, coverage)}

------------------------------------------------
IMPACT FINDINGS
------------------------------------------------

{self._findings_table(findings)}

------------------------------------------------
DETAILED FINDING
------------------------------------------------

{self._detailed_findings(findings, target)}

------------------------------------------------
NO DOCUMENTED IMPACT / INSUFFICIENT EVIDENCE
------------------------------------------------

{self._no_documented_impact(findings)}
"""

            return report.strip()

        except Exception:

            self.logger.exception("Impact Report Builder failed.")

            return "Impact Analysis report could not be formatted."
