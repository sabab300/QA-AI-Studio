"""
QA AI Studio
Impact Analysis Service

Version: 1.0

QA-AI-STUDIO-IMPACT-ANALYSIS-V1: orchestrates the 8-stage cross-domain
Impact Analysis pipeline (source retrieval -> change footprint
extraction -> target query generation -> target retrieval -> dependency
matching -> classification -> test-case traceability -> report
building). Reuses the existing RAG/vector/test-case stack throughout;
see the Phase A discovery report for the full "what already exists"
inventory. The only genuinely new pieces are this orchestrator, the
report builder, the two new prompts, and one new small history table.

Calls Core/ollama_provider.py's OllamaProvider.generate() DIRECTLY
rather than through Core/llm_engine.py's LLMEngine.generate() for its
two structured-JSON stages -- LLMEngine's prompt-type auto-detection
and Core/output_formatter.py's duplicate-line stripping are built for
prose Q&A and are unsafe for structured JSON output (see the Phase A
report for the specific failure modes). This is additive: zero lines
changed in llm_engine.py, response_validator.py, ai_guard.py, or
output_formatter.py.
"""

import json
import re
import time

from Core.logger import Logger
from Core.metadata_manager import MetadataManager
from Core.vector_store import VectorStore
from Core.retrieval_engine import RetrievalEngine
from Core.context_builder import ContextBuilder
from Core.ollama_provider import OllamaProvider
from Core.prompt_builder import PromptBuilder
from Core.test_case_repository import TestCaseRepository
from Core import test_case_scope
from Core.impact_report_builder import ImpactReportBuilder
from Core.impact_analysis_repository import ImpactAnalysisRepository
from Core import impact_analysis_logic as L
from Core import impact_management


class ImpactAnalysisError(Exception):
    """Carries one of the controlled failure codes Requirement 34 asks
    for -- never collapsed into a generic 'Unable to answer.'"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ImpactAnalysisService:

    # Sequential batches bound concurrency; per-call context/token limits
    # bound model usage without dropping later source requirements.
    ASSESSMENT_BATCH_SIZE = 10
    MAX_QUERIES_PER_CHANGE = 3

    ENGINE_VERSION = "2.3-complete-target-assessment"

    SOURCE_TOP_K = 50
    SOURCE_RERANK_TOP = 15
    SOURCE_MAX_CHARACTERS = 8000
    SOURCE_MAX_CHUNKS = 400

    # A larger candidate pool is retrieved so that the source's own text cannot crowd real baseline documents out of the
    # top results; only baseline text is then kept for the model.
    TARGET_TOP_K_PER_QUERY = 24
    TARGET_RERANK_TOP_PER_QUERY = 12
    TARGET_MAX_CHARACTERS_PER_FINDING = 5000
    MAX_BASELINE_CHUNKS = 6

    FOOTPRINT_MAX_TOKENS = 1024
    MATCH_MAX_TOKENS = 900

    TEXT_FIELDS_FOR_MATCH = ("scenario", "test_case", "steps", "expected_result", "pre_conditions")

    VALID_CLASSIFICATIONS = set(L.CLASSIFICATIONS)
    VALID_EVIDENCE_STRENGTHS = {"DIRECT", "INDIRECT", "INSUFFICIENT"}

    def __init__(self):

        self.logger = Logger.get_logger()

        self.metadata = MetadataManager()
        self.vector_store = VectorStore()
        self.retrieval = RetrievalEngine()
        self.context_builder = ContextBuilder()
        # QA-AI-STUDIO-TASK4: the extraction / matching LLM goes through the gateway (task impact_analysis); same generate() dict contract.
        try:
            from Core.ai_platform.compat import GatewayLLMProvider
            self.llm = GatewayLLMProvider(task="impact_analysis")
        except Exception:
            self.llm = OllamaProvider()
        self.prompts = PromptBuilder()
        self.test_cases = TestCaseRepository()
        self.report_builder = ImpactReportBuilder()
        self.history = ImpactAnalysisRepository()

    # --------------------------------------------------
    # Entry Point
    # --------------------------------------------------

    def analyze(self, session_id, source, target, question=None, progress_callback=None, cancel_check=None):

        start = time.perf_counter()
        self.progress_callback = progress_callback
        self.cancel_check = cancel_check

        try:

            self._validate_source(source)
            resolved_target = self._validate_target(target)

        except ImpactAnalysisError as error:

            self.logger.info(
                "[IMPACT-DIAG] analysis blocked code=%s message=%s"
                % (error.code, error.message)
            )

            return {"success": False, "error_code": error.code, "error": error.message}

        try:

            return self._run(session_id, source, resolved_target, question, start)

        except ImpactAnalysisError as error:

            # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: a controlled failure raised
            # mid-pipeline (e.g. the change-footprint extraction returning
            # unparseable JSON) must surface its own named error_code, not
            # get swallowed into the generic INTERNAL_ERROR branch below.
            self.logger.info(
                "[IMPACT-DIAG] analysis blocked code=%s message=%s"
                % (error.code, error.message)
            )

            return {
                "success": False,
                "error_code": error.code,
                "error": error.message,
                "execution_time": round(time.perf_counter() - start, 2),
            }

        except Exception as error:

            self.logger.exception("Impact Analysis failed with an unexpected error.")

            return {
                "success": False,
                "error_code": "INTERNAL_ERROR",
                "error": str(error),
                "execution_time": round(time.perf_counter() - start, 2),
            }

    # --------------------------------------------------
    # Validation
    # --------------------------------------------------

    def _validate_source(self, source):

        domain = str(source.get("domain") or "").strip()
        module = str(source.get("module") or "").strip()
        knowledge_name = str(source.get("knowledge_name") or "").strip()
        version = str(source.get("version") or "").strip()
        document_type = str(source.get('document_type') or '').strip()
        source_document = str(source.get("source_document") or "").strip()

        if not all([domain, module, knowledge_name, document_type, version, source_document]):
            raise ImpactAnalysisError(
                "ANALYSIS_VALIDATION_FAILED",
                "Source Domain, Module, Knowledge, Document Type, Version, and Source Document are all required.",
            )

        items = self.metadata.list_items_for_knowledge(domain, module, knowledge_name)

        matches = [
                item for item in items
                if str(item.get("version") or "") == version
                and item.get('document_type') == document_type
                and item.get("file_name") == source_document
        ]
        if len(matches)>1:
            raise ImpactAnalysisError("SOURCE_AMBIGUOUS", "Multiple sources have this name in the selected document version. Select a unique source.")
        match = matches[0] if matches else None

        if match is None:
            raise ImpactAnalysisError(
                "SOURCE_NOT_FOUND",
                "The selected source document was not found under the selected Domain/Module/Knowledge/Version.",
            )

        # Fill in document_type from the resolved record rather than
        # trusting whatever the client happened to send.
        source['document_type'] = match.get('document_type') or ''
        source['document_version_id'] = match.get('document_version_id')

        # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: attach the stable knowledge_items
        # row id alongside the canonical hierarchy strings above. The match
        # above already proves source_document belongs to exactly this
        # Domain/Module/Knowledge/Version (list_items_for_knowledge() is
        # itself scoped to that triple, then filtered by version+file_name),
        # so this is not a second validation path -- it's an identity
        # handle for history/evidence display, not a trust decision.
        source["knowledge_item_id"] = match.get("id")

        if str(match.get("status") or "").upper() == "FAILED_NO_TEXT":
            raise ImpactAnalysisError(
                "SOURCE_NOT_INDEXED",
                "Impact analysis cannot start because the selected source document has no "
                "indexed text. Re-upload a text-readable PDF/DOCX or resolve document "
                "ingestion first.",
            )

        has_vectors = self.vector_store.has_source_vectors(
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version, file_name=source_document,
            document_type=document_type, document_version_id=match.get('document_version_id'),
        )

        if not has_vectors:
            raise ImpactAnalysisError(
                "SOURCE_NOT_INDEXED",
                "The selected source document has no retrievable indexed vectors. "
                "Re-index this source before running Impact Analysis.",
            )

    def _validate_target(self, target):

        domain = str(target.get("domain") or "").strip()
        module = str(target.get("module") or "").strip() or None

        if not domain:
            raise ImpactAnalysisError("ANALYSIS_VALIDATION_FAILED", "Target Domain is required.")

        if domain not in self.metadata.list_domains():
            raise ImpactAnalysisError("TARGET_NOT_FOUND", f"Target Domain '{domain}' was not found.")

        if module and module not in self.metadata.list_modules(domain):
            raise ImpactAnalysisError(
                "TARGET_NOT_FOUND",
                f"Target Module '{module}' was not found under Domain '{domain}'.",
            )

        has_vectors = self.vector_store.has_domain_vectors(domain=domain, module=module)

        if not has_vectors:
            raise ImpactAnalysisError(
                "TARGET_NOT_INDEXED",
                "Target system has no indexed knowledge available for impact analysis.",
            )

        return {"domain": domain, "module": module}

    # --------------------------------------------------
    # Pipeline
    # --------------------------------------------------

    def _run(self, session_id, source, target, question, start):

        self.logger.info(
            "[IMPACT-DIAG] analysis start source_domain=%s source_module=%s "
            "source_knowledge=%s source_version=%s source_document=%s "
            "target_domain=%s target_module=%s" % (
                source.get("domain"), source.get("module"), source.get("knowledge_name"),
                source.get("version"), source.get("source_document"),
                target.get("domain"), target.get("module"),
            )
        )

        warnings = []          # [{code, finding_id, message}] -- the visible "Coverage Warnings" are exactly these

        def warn(code, message, finding_id=""):
            warnings.append({"code": code, "finding_id": finding_id, "message": message})

        # Stage 1 -- source retrieval. The WHOLE selected document is read (in order), not a similarity sample.
        source_documents, source_metadata, source_info = self._load_source(source)

        self.logger.info("[IMPACT-DIAG] source_chunks=%s mode=%s" % (len(source_documents), source_info.get("mode")))

        if not any(str(d or "").strip() for d in source_documents):
            return {
                "success": False,
                "error_code": "NO_CHANGE_FOOTPRINT",
                "error": "No retrievable source content for the selected document.",
                "execution_time": round(time.perf_counter() - start, 2),
            }

        if source_info.get("mode") != "FULL_DOCUMENT":
            warn("SOURCE_PARTIAL", "The whole source document could not be read; only the most relevant passages "
                                   "(%d chunk(s)) were analysed, so some requirements may be missing from this analysis." % len(source_documents))
        elif source_info.get("truncated"):
            warn("SOURCE_PARTIAL", "The source document could not be retrieved completely; only %d chunk(s) were analysed. Additional source requirements may remain unassessed; retry when source retrieval is available." % len(source_documents))

        source_chunks = [{"text": t, "meta": dict(m or {}), "index": i} for i, (t, m) in enumerate(zip(source_documents, source_metadata))]

        # Stage 2 -- structured change footprint extraction, window by window, so every part of the source is seen.
        footprint, windows_used, windows_total, window_detail = self._extract_footprint_windows(source, source_chunks)

        if footprint is None:
            return {
                "success": False,
                "error_code": "OLLAMA_UNAVAILABLE",
                "error": "Change footprint extraction failed -- Ollama did not return a usable response.",
                "execution_time": round(time.perf_counter() - start, 2),
            }

        if windows_used < windows_total:
            warn("SOURCE_PARTIAL", "Only %d of %d source section(s) were analysed because of the analysis size limit." % (windows_used, windows_total))

        for failed in window_detail["failed_windows"]:
            where = (" (chunks %s-%s)" % (failed["first_chunk"], failed["last_chunk"])) if failed.get("first_chunk") is not None else ""
            warn("SOURCE_SECTION_FAILED", "Source section %d%s could not be analysed because the model returned no usable answer; its requirements were not assessed by the model "
                                          "(they are still compared with the earlier version and listed under Source Coverage)." % (failed["window"], where))

        footprint, discarded = self._verify_footprint(footprint, source_chunks)

        if discarded:
            warn("SOURCE_EVIDENCE_UNVERIFIED", "%d extracted change(s) were discarded because their quoted source text does not appear in the source document: %s."
                 % (len(discarded), "; ".join(discarded[:5])))

        # The model is not a completeness oracle. Retain uncited functional
        # source statements verbatim and run them through the same quote gate.
        examined_source = [c for c in source_chunks if c["meta"].get("chunk_number") in window_detail["examined_chunks"]]
        footprint = L.supplement_source_requirements(footprint, examined_source)
        footprint, inventory_discarded = self._verify_footprint(footprint, source_chunks)
        if inventory_discarded:
            warn("SOURCE_INVENTORY_UNVERIFIED", "%d source inventory statement(s) could not be verified; review Source Coverage." % len(inventory_discarded))

        self.logger.info("[IMPACT-DIAG] extracted_changes=%s" % len(footprint))

        if not footprint:
            # A genuinely empty extraction is Requirement 34's named NO_CHANGE_FOOTPRINT failure code, not a silent success.
            return {
                "success": False,
                "error_code": "NO_CHANGE_FOOTPRINT",
                "error": "No supported change footprint could be extracted from the selected source document.",
                "execution_time": round(time.perf_counter() - start, 2),
            }

        source_text = "\n".join(c["text"] for c in source_chunks)
        cited = [q["quote"] for ch in footprint for q in ch.get("source_evidence_detail", []) if q.get("verified")]

        for label in L.uncovered_requirements(source_text, cited):
            warn("SOURCE_REQUIREMENT_UNCOVERED", "%s of the source document is not referenced by any extracted change; it was not assessed." % label)

        # Other versions of the source document come from the Knowledge Hub's records (not from the target search).
        related_versions, related_ok = self._load_related_versions(source)
        counterpart = L.find_earlier_counterpart(source.get("version"), related_versions, source_chunks)
        prior_documents = [counterpart["document"]] if counterpart["document"] else []
        prior_version = counterpart["version"]
        has_earlier = any(L.version_order(d.get("version"), source.get("version")) == -1 for d in related_versions)

        if counterpart["document"] is None and has_earlier:
            warn("VERSION_COUNTERPART_NOT_FOUND", "Earlier versions of this Knowledge exist but none of their documents corresponds to the source document, so nothing was compared with an earlier version.")
        elif counterpart["skipped"] or counterpart["others"]:
            parts = []
            if counterpart["skipped"]:
                parts.append("version(s) %s hold only other documents" % ", ".join(str(k["version"]) for k in counterpart["skipped"]))
            if counterpart["others"]:
                parts.append("version %s also holds %s" % (prior_version, ", ".join(d.get("file_name") or "another document" for d in counterpart["others"][:4])))
            warn("VERSION_OTHER_DOCUMENTS_NOT_COMPARED", "The source was compared with %s (version %s) only; %s, so those other documents were not compared."
                 % (counterpart["document"].get("file_name"), prior_version, " and ".join(parts)))

        if not related_ok:
            warn("VERSION_LOOKUP_FAILED", "Other versions of the source document could not be read, so unchanged requirements could not be told apart from new ones.")
        elif prior_version is None and not has_earlier:
            warn("NO_PRIOR_VERSION", "No earlier version of this source document is available. Version %s is analysed as source requirements, not as confirmed changes from a previous version." % source.get("version"))

        # Requirements that are word for word in the earlier version cost nothing to report, so only the others count against the size limit.
        statuses = [L.prior_version_status(change, prior_documents, prior_version, source_chunks) for change in footprint]
        # Batch size is progress granularity, never an eligibility limit.
        assessed_footprint = list(zip(footprint, statuses))
        changes_not_assessed, target_not_assessed = [], []
        self._target_query_cache, self._classification_cache = {}, {}
        scope_scan = self._target_scope_scan(source, target, source_chunks)
        processing = {"total": len(assessed_footprint), "completed": 0, "failed": 0,
                      "batch_size": self.ASSESSMENT_BATCH_SIZE, "batches_completed": 0,
                      "scope_scan_reused": scope_scan is not None,
                      "scope_scan_chunks": len(scope_scan[0]) if scope_scan is not None else 0,
                      "batches_total": (len(assessed_footprint) + self.ASSESSMENT_BATCH_SIZE - 1) // self.ASSESSMENT_BATCH_SIZE}

        findings = []
        total_target_chunks = 0
        excluded_chunks = 0
        used_target_keys, excluded_target_keys = set(), set()
        matched_docs = set()
        excluded_docs = {}
        total_queries = 0
        test_case_cache = None
        test_case_info = None
        seen_known_names, seen_known_ids = set(), set()          # knowledge names / source ids of the evidence documents examined (for matching removed requirements to tests)
        test_findings_searched = 0
        test_findings_attempted = 0

        version_only = 0

        for index, (change, version_status) in enumerate(assessed_footprint, start=1):

            self._progress("target_assessment", index - 1, len(assessed_footprint))

            finding_id = f"IA-{index:03d}"
            counts = {L.ROLE_BASELINE: 0, L.ROLE_CHANGE_REQUEST: 0, L.ROLE_SAME_REQUEST: 0, L.ROLE_SOURCE_FAMILY: 0, L.ROLE_SOURCE_ITSELF: 0}
            baseline, others, parsed = [], [], None
            assessment_status, retrieval_issue = "VERSION_UNCHANGED", None

            if version_status["status"] == "UNCHANGED":
                # The requirement is word for word in the earlier version: it is not a change introduced by this version,
                # whatever the target documents say. No target search or model call is needed.
                decision = L.decision_from_version(version_status, source.get("version"))
            else:
                self._target_retrieval_status = {"status": "SEARCH_EXECUTED"}
                try:
                    if scope_scan is not None:
                        target_documents, target_metadata = scope_scan
                        assessment_status = "SCOPE_SCAN_COMPLETED"
                    else:
                        queries = self._generate_target_queries(change)
                        total_queries += len(queries)
                        target_documents, target_metadata = self._retrieve_target(queries, target)
                        assessment_status = self._target_retrieval_status["status"]
                except Exception:
                    self.logger.exception("Impact Analysis: target retrieval failed for %s.", finding_id)
                    target_documents, target_metadata = [], []
                    assessment_status = "RETRIEVAL_FAILED"
                    retrieval_issue = "Target retrieval failed; the source statement is retained, but independent target evidence could not be assessed. Retry when retrieval is available."
                tagged = L.tag_chunks(target_documents, target_metadata, source, source_chunks)
                counts = L.role_counts(tagged)
                # Independent evidence: target documents first, then other change requests. Never the source, its other versions or its siblings.
                admissible = [c for c in tagged if c["role"] == L.ROLE_BASELINE] + [c for c in tagged if c["role"] == L.ROLE_CHANGE_REQUEST]
                others = [c for c in tagged if c["role"] not in L.ADMISSIBLE_ROLES]

                baseline, target_context = self._build_target_context(admissible)

                def chunk_key(c):
                    m = c["meta"]
                    return (m.get("domain"), m.get("module"), m.get("knowledge_name"), m.get("file_name"), str(m.get("version")), m.get("chunk_number"), c["text"])
                used_target_keys.update(chunk_key(c) for c in baseline)
                excluded_target_keys.update(chunk_key(c) for c in others)
                total_target_chunks = len(used_target_keys)
                excluded_chunks = len(excluded_target_keys)

                for chunk in baseline:
                    meta = chunk["meta"]
                    matched_docs.add((meta.get("knowledge_id"), meta.get("file_name"), meta.get("knowledge_name"), meta.get("document_type"), meta.get("version")))

                for chunk in others:
                    meta = chunk["meta"]
                    excluded_docs[(meta.get("file_name"), str(meta.get("version")), chunk["role"])] = {
                        "file_name": meta.get("file_name"), "version": meta.get("version"), "document_type": meta.get("document_type"),
                        "knowledge_name": meta.get("knowledge_name"), "role": chunk["role"], "role_label": L.ROLE_LABELS[chunk["role"]],
                    }

                if not baseline:
                    decision = L.no_baseline_decision(counts, version_status.get("versions_checked") or ())
                    decision["classification"] = L.INSUFFICIENT
                    if retrieval_issue:
                        decision["reasons"] = [retrieval_issue]
                else:
                    key = (L.normalize(change.get("name")), L.normalize(change.get("change")), L.normalize(" ".join(change.get("source_evidence") or [])), target_context)
                    if key not in self._classification_cache:
                        try:
                            self._classification_cache[key] = self._classify(change, target, target_context)
                        except Exception:
                            self.logger.exception("Impact Analysis: classification failed for %s.", finding_id)
                            self._classification_cache[key] = None
                    parsed = self._classification_cache[key]
                    decision = L.decide(change, parsed, baseline)
                    if parsed is None:
                        assessment_status = "CLASSIFICATION_FAILED"

                # The comparison with the earlier version stands when no independent evidence settled the change.
                if decision["classification"] in (L.INSUFFICIENT, L.NO_DOCUMENTED):
                    derived = L.decision_from_version(version_status, source.get("version"))
                    if derived is not None:
                        derived["reasons"] = derived["reasons"] + ([decision["reasons"][0]] if decision["reasons"] else [])
                        decision = derived
                        version_only += 1

            finding = self._assemble_finding(finding_id, source, change, target, parsed, decision, baseline, others, counts, related_versions, version_status)
            finding["target_assessment"] = assessment_status
            if assessment_status in ("RETRIEVAL_FAILED", "RETRIEVAL_PARTIAL", "CLASSIFICATION_FAILED"):
                processing["failed"] += 1
                warn("TARGET_ASSESSMENT_PARTIAL", "%s: %s" % (finding_id, retrieval_issue or "Target assessment was incomplete (%s); available evidence is retained." % assessment_status), finding_id)

            if finding["change_status"] == "UNCHANGED":
                finding["test_case_coverage"] = {"classification": "NOT ASSESSED", "test_cases": [], "lookup_ok": True,
                                                 "note": "Unchanged from version %s, so no new test coverage is needed for it; test cases were not searched." % version_status.get("prior_version")}

            else:

                if test_case_cache is None:
                    test_case_cache = self._load_test_cases(target)

                rows, lookup_ok, test_case_info = test_case_cache
                test_findings_attempted += 1
                test_findings_searched += int(lookup_ok)
                known_knowledge = {c["meta"].get("knowledge_name") for c in baseline if c["meta"].get("knowledge_name")} | {source.get("knowledge_name")}
                known_ids = {c["meta"].get("knowledge_id") for c in baseline if c["meta"].get("knowledge_id") is not None} | {source.get("knowledge_item_id")}
                seen_known_names |= known_knowledge
                seen_known_ids |= {i for i in known_ids if i is not None}
                finding["test_case_coverage"] = L.match_test_cases(change, rows, known_knowledge, lookup_ok, target_knowledge_ids=known_ids)
                if test_case_info.get("status") in ("NO_TEST_CASES_EXIST", "NO_TESTS_IN_SCOPE", "SCOPE_EMPTY_REPOSITORY_COUNT_UNKNOWN", "REPOSITORY_UNAVAILABLE"):
                    finding["test_case_coverage"].update(status=test_case_info["status"], note=test_case_info["status_note"])

                coverage_class = finding["test_case_coverage"]["classification"]
                if finding["classification"] in (L.CONFIRMED, L.POTENTIAL):
                    if coverage_class == "NO MATCHING COVERAGE FOUND":
                        warn("NO_TEST_COVERAGE", "%s (%s): no matching test case was found in the examined target scope; test cases outside that scope were not searched."
                             % (finding_id, (change.get("name") or "")[:80]), finding_id)
                    elif coverage_class == "LOOKUP UNAVAILABLE":
                        warn("TEST_LOOKUP_FAILED", "%s: the test-case repository could not be read, so test coverage is unknown." % finding_id, finding_id)

            if finding["classification"] == L.INSUFFICIENT:
                warn("INSUFFICIENT_EVIDENCE", "%s (%s): %s" % (finding_id, (change.get("name") or "")[:80], finding["classification_basis"][0] if finding["classification_basis"] else "Evidence is insufficient."), finding_id)

            if finding.get("conflicting_evidence"):
                warn("VERSIONS_DIFFER", "%s: the target evidence contains different wording for the same passage in more than one document version." % finding_id, finding_id)

            if parsed is None and baseline:
                warn("CLASSIFICATION_FAILED", "%s: the classification model returned no usable answer." % finding_id, finding_id)

            findings.append(finding)
            processing["completed"] = index
            processing["batches_completed"] = index // self.ASSESSMENT_BATCH_SIZE + int(index == len(assessed_footprint) and index % self.ASSESSMENT_BATCH_SIZE != 0)
            self._progress("target_assessment", index, len(assessed_footprint))

        finding_quotes = [q for f in findings for q in f.get("source_evidence", [])] + [f["version_status"].get("current_quote") for f in findings]
        version_diff = L.version_difference(source_chunks, prior_documents, prior_version, finding_quotes)

        comparison_complete = (source_info.get("mode") == "FULL_DOCUMENT" and not source_info.get("truncated")
                               and not any(d.get("truncated") for d in prior_documents or []))

        if version_diff is not None:
            version_diff["compared_with"] = {"file_name": counterpart["document"].get("file_name"), "version": prior_version, "similarity": counterpart["score"]}
            version_diff["files_not_compared"] = [d.get("file_name") for d in counterpart["others"]]
            version_diff["complete"] = comparison_complete
            if not comparison_complete:
                warn("VERSION_COMPARISON_PARTIAL", "The source document or the earlier version (v%s) could not be read completely, so the comparison of the two versions is incomplete: "
                                                   "differences, and especially removed requirements, may be missing." % prior_version)

        if version_diff and version_diff["unassessed_count"]:
            kinds = version_diff["unassessed_by_kind"]
            first = "; ".join('"%s"' % (d.get("current") or d.get("prior") or "")[:110] for d in version_diff["unassessed"][:2])
            warn("VERSION_DIFFERENCE_NOT_ASSESSED",
                 "%d functional difference(s) between version %s and version %s are not covered by any finding (changed %d, new %d, removed %d; for example %s). "
                 "They were found by comparing the documents and are listed under Version Comparison as unassessed changes."
                 % (version_diff["unassessed_count"], prior_version, source.get("version"), kinds["CHANGED"], kinds["ADDED"], kinds["REMOVED"], first))

        if version_diff and version_diff["counts"]["removed"]:
            warn("REQUIREMENTS_REMOVED", "%d requirement(s) of version %s no longer appear in version %s; removals are listed under Version Comparison."
                 % (version_diff["counts"]["removed"], prior_version, source.get("version")))

        # A removed requirement is a real change: the existing tests (and automation) that covered it must be reviewed. They are matched with the same
        # source-identity and workbook-version rules as every other finding; nothing is retired or changed.
        removed_requirements = []
        removed_items = (version_diff or {}).get("removed") or []
        if removed_items:
            if test_case_cache is None:
                test_case_cache = self._load_test_cases(target)
            rows, lookup_ok, loaded_info = test_case_cache
            if test_case_info is None:
                test_case_info = loaded_info
            names = set(seen_known_names) | {source.get("knowledge_name")}
            ids = set(seen_known_ids) | {source.get("knowledge_item_id")}
            for item in removed_items:
                prior_text = item.get("prior") or ""
                subject = " ".join(re.sub(r"^\s*[A-Za-z]+[-_ ]?\d+[:.)]?\s*", "", prior_text).split()[:10])
                removed_requirements.append({"statement": prior_text, "prior_chunk": item.get("prior_chunk"),
                                             "test_case_coverage": L.match_test_cases({"name": subject, "change": prior_text}, rows, names, lookup_ok, target_knowledge_ids=ids)})

        # Source coverage: which requirement statements of the source were assessed, which were not, and why.
        deferred_quotes = [q for c in changes_not_assessed for q in c.get("source_evidence", [])]
        requirement_coverage = L.source_requirement_coverage(source_chunks, finding_quotes, window_detail["examined_chunks"], deferred_quotes=deferred_quotes)
        inventory = L.source_inventory(source_chunks)
        for item in inventory["items"]:
            related = [f for f in findings if any(L._cites(item["text"], q) for q in f.get("source_evidence", []))] if item["kind"] == "FUNCTIONAL" else []
            item["finding_ids"] = [f["finding_id"] for f in related]
            for f in related:
                f.setdefault("requirement_ids", []).append(item["statement_id"])
            item["assessment"] = "SOURCE_REVIEWED" if related else "NOT_ASSESSED" if item["kind"] == "FUNCTIONAL" else "NOT_A_FUNCTIONAL_REQUIREMENT"
            item["version_statuses"] = sorted({f["change_status"] for f in related})
            item["target_scope_status"] = "SOURCE_SCOPE_MATCH" if L.scope_type(source, target) == "SAME_MODULE" else "UNVERIFIED"
        requirement_coverage["inventory"] = inventory
        requirement_coverage["assessment_note"] = "Assessed means the source statement is represented by a finding. It does not mean target impact is confirmed. Administrative information, context and duplicates are listed separately. Outside-target scope is never inferred from missing evidence."

        if requirement_coverage["not_assessed_count"]:
            why = requirement_coverage["by_reason"]
            warn("SOURCE_REQUIREMENTS_NOT_ASSESSED",
                 "%d of %d requirement statement(s) in the source document were not assessed by any finding (%d in sections that were not read, %d belonging to changes left out by the size limit, "
                 "%d that the model did not extract). They are listed under Source Coverage; none of them is reported as impact or as no impact."
                 % (requirement_coverage["not_assessed_count"], requirement_coverage["detected"], why["NOT_EXAMINED"], why["NOT_ASSESSED_LIMIT"], why["NOT_EXTRACTED"]))

        unchanged_count = sum(1 for f in findings if f["change_status"] == "UNCHANGED")
        if test_case_info is None:
            test_case_info = {"total": None, "examined": 0, "complete": True, "status": "NOT_EXECUTED", "status_note": "Test search not executed: every finding is unchanged from the earlier version."}
        test_case_coverage = dict(test_case_info, findings_searched=test_findings_searched, findings_not_assessed=unchanged_count,
                                  findings_search_attempted=test_findings_attempted)
        if test_case_coverage.get("status") == "REPOSITORY_UNAVAILABLE":
            warn("TEST_REPOSITORY_UNAVAILABLE", test_case_coverage["status_note"])
        if (test_case_coverage.get("version_scope") or {}).get("status") == "UNVERIFIED":
            warn("TEST_VERSION_SCOPE_UNVERIFIED", test_case_coverage["version_scope"]["note"])
        if test_case_coverage.get("status") == "SEARCH_EXECUTED":
            test_case_coverage["status"] = "MATCHES_FOUND" if (any((f.get("test_case_coverage") or {}).get("test_cases") for f in findings)
                                                                or any((r.get("test_case_coverage") or {}).get("test_cases") for r in removed_requirements)) else "NO_MATCHING_TESTS"
            test_case_coverage["status_note"] = "Related tests found; this does not verify the changed behaviour." if test_case_coverage["status"] == "MATCHES_FOUND" else "Test search executed; no matching tests found in the examined scope."

        # Automation impact (read-only): only for tests a finding already matched. Never blocks or fails the analysis.
        # Tests matched to a removed requirement are looked up too (an automated script of a removed rule needs review).
        removed_probe = [{"finding_id": "REMOVED-%d" % (i + 1), "change_status": "REMOVED", "classification": "NOT CLASSIFIED",
                          "source_change": {"name": (r["statement"] or "")[:80], "change": r["statement"]}, "test_case_coverage": r["test_case_coverage"]}
                         for i, r in enumerate(removed_requirements)]
        automation_summary = self._automation_impact(findings + removed_probe, (test_case_cache or ([], False, {}))[0])
        for removed, probe in zip(removed_requirements, removed_probe):
            removed["automation_impact"] = probe.get("automation_impact")

        if not test_case_info.get("complete") and test_case_info.get("total") is not None:
            warn("TEST_CASES_PARTIAL", "Only %s of %s test case(s) of the target scope could be searched, so a change reported as having no matching test case may have one among the unsearched test cases."
                 % (test_case_info.get("examined"), test_case_info.get("total") if test_case_info.get("total") is not None else "an unknown number of"))

        comparison_source_complete = source_info.get("mode") == "FULL_DOCUMENT" and not source_info.get("truncated") and windows_used >= windows_total and not window_detail["failed_windows"]

        counts_by_class = {name: sum(1 for f in findings if f["classification"] == name) for name in L.CLASSIFICATIONS}
        document_changes = {
            "prior_version": prior_version if prior_documents else None,
            "confirmed_changes": sum(1 for f in findings if f["document_change"]["status"] == "CHANGED" and f["document_change"]["material"]),
            "wording_only": sum(1 for f in findings if f["document_change"]["status"] == "CHANGED" and not f["document_change"]["material"]),
            "new_requirements": sum(1 for f in findings if f["document_change"]["status"] == "ADDED"),
            "unchanged": unchanged_count,
            "not_compared": sum(1 for f in findings if f["document_change"]["status"] in ("NO_PRIOR_VERSION", "UNKNOWN")),
        }
        testcase_matches = sum(len((f.get("test_case_coverage") or {}).get("test_cases", [])) for f in findings)

        self.logger.info(
            "[IMPACT-DIAG] target_queries=%s target_chunks=%s excluded=%s counts=%s testcase_matches=%s" % (
                total_queries, total_target_chunks, excluded_chunks, counts_by_class, testcase_matches,
            )
        )

        if excluded_docs:
            names = sorted({"%s (v%s)" % (d["file_name"], d["version"]) for d in excluded_docs.values()})
            warn("TARGET_SELF_REFERENCE_EXCLUDED",
                 "%d target document(s) were excluded as evidence because they are the source document itself, other versions of it, or sibling documents of the "
                 "same change request, not independent descriptions of the target: %s." % (len(excluded_docs), ", ".join(names[:6])))

        if version_only:
            warn("VERSION_COMPARISON_ONLY",
                 "%d finding(s) rest on the comparison with the earlier version (v%s) alone: no independent target document or other change request settled them. "
                 "They confirm a change in the document, not an impact on the target: whether the target implements or must change is not verified." % (version_only, prior_version))

        searched = sum(1 for f in findings if f["change_status"] != "UNCHANGED")

        if searched - version_only > 0 and total_target_chunks == 0:
            scope = target.get("domain") + (f" / {target.get('module')}" if target.get("module") else "")
            warn("TARGET_NO_BASELINE",
                 f"Target scope ({scope}) holds no independent evidence (a target document such as an SRS, design, API or integration document, or another change request) "
                 "that matched any extracted change. Findings reflect the available evidence, not a confirmed absence of real-world impact.")

        result = {
            "success": True,
            "engine_version": self.ENGINE_VERSION,
            "source": source,
            "target": target,
            "coverage": {
                "source_chunks_analyzed": len(source_documents),
                "source_chunks_total": source_info.get("total", len(source_documents)),
                "source_read_mode": source_info.get("mode"),
                "target_chunks_analyzed": total_target_chunks,
                "target_chunks_excluded": excluded_chunks,
                "target_documents_matched": len(matched_docs),
                "target_documents_excluded": len(excluded_docs),
                "excluded_documents": sorted(excluded_docs.values(), key=lambda d: (str(d["file_name"]), str(d["version"]))),
                "source": {
                    "chunks_total": source_info.get("total", len(source_documents)),
                    "chunks_read": len(source_documents),
                    "chunks_not_read": max(0, source_info.get("total", len(source_documents)) - len(source_documents)),
                    "read_mode": source_info.get("mode"),
                    "retrieval_pages": source_info.get("pages", 1),
                    "sections_total": windows_total,
                    "sections_analysed": windows_used - len(window_detail["failed_windows"]),
                    "sections_not_analysed": (windows_total - windows_used) + len(window_detail["failed_windows"]),
                    "chunks_not_examined": len(window_detail["skipped_chunks"] | {n for w in window_detail["failed_windows"] for n in range(w["first_chunk"] or 0, (w["last_chunk"] or -1) + 1)}),
                    "complete": bool(comparison_source_complete),
                },
                "source_requirements": requirement_coverage,
                "changes_not_assessed": changes_not_assessed,
                "target_statements_not_assessed": target_not_assessed,
                "target_processing": dict(processing, status="PARTIAL" if processing["failed"] else "COMPLETED",
                    target_queries_requested=total_queries, unique_target_queries=len(self._target_query_cache),
                    classification_calls=len(self._classification_cache), classification_token_limit=self.MATCH_MAX_TOKENS,
                    assessment_methods={s: sum(f["target_assessment"] == s for f in findings) for s in {f["target_assessment"] for f in findings}}),
                "test_cases": test_case_coverage,
                "automation": automation_summary,
                "warnings": [w["message"] for w in warnings],
                "warning_details": warnings,
            },
            "summary": {
                "confirmed": counts_by_class[L.CONFIRMED], "potential": counts_by_class[L.POTENTIAL],
                "already_covered": counts_by_class[L.ALREADY_COVERED], "insufficient_evidence": counts_by_class[L.INSUFFICIENT],
                "no_documented_impact": counts_by_class[L.NO_DOCUMENTED], "coverage_warnings": len(warnings),
                "document_changes": document_changes,
                "source_supported_potential": sum(bool(f.get("potential_affected_functionality")) for f in findings),
                "headline": self._headline(counts_by_class, len(findings), total_target_chunks, document_changes),
            },
            "analysis_context": {
                "scope_type": L.scope_type(source, target),
                "scope_label": L.SCOPE_LABELS[L.scope_type(source, target)],
                "source_document_type": source.get("document_type"),
                "prior_version_compared": prior_version if prior_documents else None,
                "other_versions_found": sorted({str(d["version"]) for d in related_versions}),
                "evidence_types_used": sorted({str(c.get("document_type") or "document") for f in findings for c in f.get("target_evidence", [])}),
                "analysis_types": sorted({f["analysis_type"] for f in findings}),
            },
            "version_difference": version_diff,
            "removed_requirements": removed_requirements,
            "change_footprint": footprint,
            "findings": findings,
            "execution_time": round(time.perf_counter() - start, 2),
        }

        issues = L.validate_result(result)
        result["coverage"]["consistency"] = {"checked": True, "issues": issues}
        for message in issues:
            warn("RESULT_CONSISTENCY", message)
        if issues:
            result["coverage"]["warnings"] = [w["message"] for w in warnings]
            result["coverage"]["warning_details"] = warnings
            result["summary"]["coverage_warnings"] = len(warnings)

        try:
            block = impact_management.management_block(result, "impact_analysis")
            result["management"] = {k: block[k] for k in ("decision", "questions", "effort")}
        except Exception:
            self.logger.exception("Impact Analysis: management summary failed (non-fatal).")
        result["report_text"] = self.report_builder.build(result)

        report_issues = L.validate_report(result)
        if report_issues:
            result["coverage"]["consistency"]["issues"] += report_issues
            for message in report_issues:
                warn("RESULT_CONSISTENCY", message)
            result["coverage"]["warnings"] = [w["message"] for w in warnings]
            result["coverage"]["warning_details"] = warnings
            result["summary"]["coverage_warnings"] = len(warnings)
            result["report_text"] = self.report_builder.build(result)

        self._save_history(session_id, source, target, question, result)

        return result

    def _headline(self, counts, total, baseline_chunks, document_changes=None):

        if total == 0:
            return "No source changes could be assessed."

        parts = []
        for key, label in ((L.CONFIRMED, "confirmed impact"), (L.POTENTIAL, "potential impact"), (L.ALREADY_COVERED, "already covered (no change required)"),
                           (L.INSUFFICIENT, "insufficient evidence"), (L.NO_DOCUMENTED, "no documented impact")):
            if counts.get(key):
                parts.append("%d %s" % (counts[key], label))

        text = "%d source requirement finding%s reviewed: %s." % (total, "" if total == 1 else "s", ", ".join(parts) or "none classified")

        if document_changes and document_changes.get("prior_version") is not None:
            text += (" Document changes since version %s: %d confirmed, %d wording-only, %d new, %d unchanged. A document change is not by itself a system impact."
                     % (document_changes["prior_version"], document_changes["confirmed_changes"], document_changes["wording_only"], document_changes["new_requirements"], document_changes["unchanged"]))

        if counts.get(L.INSUFFICIENT) and not baseline_chunks:
            text += " The target scope holds no independent evidence for these changes (no target document, other change request or earlier version matched), so no impact or absence of impact can be concluded."

        return text

    def _save_history(self, session_id, source, target, question, result):

        try:
            analysis_id = self.history.save(
                session_id, source, target, question, result,
                execution_time=result.get("execution_time", 0),
            )
            if analysis_id is not None:
                result["analysis_id"] = analysis_id
        except Exception:
            self.logger.exception("Failed to persist Impact Analysis history (non-fatal).")

    # --------------------------------------------------
    # Finding assembly
    # --------------------------------------------------

    def _assemble_finding(self, finding_id, source, change, target, parsed, decision, baseline, others, counts, related_versions, version_status):

        parsed = parsed or {}
        classification = decision["classification"]
        basis = decision.get("basis") or "TARGET_EVIDENCE"
        reasons = list(decision.get("reasons") or [])
        component = str(parsed.get("target_component") or "").strip()
        existing = str(parsed.get("existing_target_behaviour") or "").strip()
        required = str(parsed.get("required_target_change") or "").strip()
        index = decision.get("evidence_index")
        status = version_status.get("status")
        prior = version_status.get("prior_version")
        version = source.get("version")

        quote_item = None
        if basis == "TARGET_EVIDENCE" and index is not None and index < len(baseline):
            chunk = baseline[index]
            meta = chunk["meta"]
            quote = parsed.get("target_evidence_quote")
            quote = " ... ".join(str(q) for q in quote) if isinstance(quote, list) else str(quote or "")
            quote_item = {"text": quote.strip(), "exact": bool(decision.get("evidence_exact")), "file_name": meta.get("file_name"),
                          "version": meta.get("version"), "document_type": meta.get("document_type"), "chunk_number": meta.get("chunk_number"),
                          "page": meta.get("page") or meta.get("page_number"), "evidence_id": "E%d" % (index + 1),
                          "evidence_kind": L.EVIDENCE_KIND_LABELS.get(chunk["role"], "target document")}
        elif basis == "VERSION_COMPARISON" and version_status.get("prior_quote"):
            quote_item = {"text": version_status["prior_quote"], "exact": True, "file_name": version_status.get("prior_file"), "version": prior,
                          "document_type": source.get("document_type"), "chunk_number": version_status.get("prior_chunk"), "page": None,
                          "evidence_id": "V%s" % prior, "evidence_kind": "earlier version of the source document"}

        if basis == "VERSION_COMPARISON":
            current = version_status.get("current_quote") or ""
            if status == "UNCHANGED":
                existing = "The same requirement is already stated word for word in version %s: \"%s\"" % (prior, version_status.get("prior_quote", ""))
                required = "None. It is not a change introduced by version %s." % version
            elif status == "CHANGED":
                existing = "Version %s stated: \"%s\"" % (prior, version_status.get("prior_quote", ""))
                required = ("Check whether what was built or documented from version %s matches version %s: \"%s\". The document change is confirmed; whether the target must change is not." % (prior, version, current)
                            if version_status.get("material") else
                            "Confirm whether the rewording in version %s (\"%s\") changes the behaviour; no change is demonstrated." % (version, current))
            else:
                existing = "Version %s contains no corresponding requirement." % prior
                required = "Implement, or confirm the implementation of, the requirement introduced in version %s: \"%s\"" % (version, current)
            component = ""
        else:
            if classification not in (L.CONFIRMED, L.POTENTIAL, L.ALREADY_COVERED):
                existing = ""
                component = ""
            if classification == L.ALREADY_COVERED:
                required = "None. The available evidence already states this requirement."
            elif classification == L.INSUFFICIENT:
                required = "Cannot be determined from the available evidence."
            elif classification == L.NO_DOCUMENTED:
                required = "None identified from the indexed target documentation."
            elif not required:
                required = "Not stated by the evidence; confirm with the target owner."

            if not existing:
                if classification == L.INSUFFICIENT and not baseline:
                    existing = "No independent evidence describing the target's current behaviour was found in the selected target scope."
                elif classification == L.NO_DOCUMENTED:
                    existing = "The available target evidence was reviewed; nothing in it relates to this change."
                else:
                    existing = "The retrieved target text does not describe the current behaviour of this component."

        history = []
        for quote in change.get("source_evidence") or []:
            for hit in L.locate_in_documents(quote, related_versions):
                if hit not in history:
                    history.append(hit)

        comparison = L.compare_versions(baseline)
        kind = baseline[index]["role"] if basis == "TARGET_EVIDENCE" and index is not None and index < len(baseline) else None

        finding = {
            "finding_id": finding_id,
            "classification": classification,
            "system_impact": classification,
            "document_change": L.document_change(version_status),
            "classification_basis_kind": basis,
            "evidence_strength": decision["evidence_strength"],
            "analysis_type": L.analysis_label(source, target, baseline, version_status, basis),
            "change_status": status,
            "impact_area": str(parsed.get("impact_area") or "").strip() or change.get("type", ""),
            "source_change": {"type": change.get("type"), "name": change.get("name"), "change": change.get("change")},
            "source_extraction_method": change.get("extraction_method") or "MODEL_EXTRACTED",
            "source_requirement": {"text": " ".join(change.get("source_evidence") or []), "verified": any(q.get("verified") for q in change.get("source_evidence_detail", []))},
            "potential_affected_functionality": ({"description": " ".join(change.get("source_evidence") or []), "basis": "SOURCE_REQUIREMENT",
                "note": "Source-supported candidate functionality for review; no independent target dependency or implementation change is established by this statement."}
                if status != "UNCHANGED" and L.is_functional(" ".join(change.get("source_evidence") or [])) else None),
            "confirmed_target_impact": classification == L.CONFIRMED,
            "source_evidence": change.get("source_evidence", []),
            "source_evidence_detail": change.get("source_evidence_detail", []),
            "source_history": history,
            "version_status": version_status,
            "target_domain": target.get("domain"),
            "target_module": target.get("module"),
            "target_component": component,
            "target_state": decision["target_state"],
            "existing_target_behaviour": existing,
            "required_target_change": required,
            "supporting_quote": quote_item,
            "target_evidence": self._evidence_items(baseline),
            "excluded_evidence": self._excluded_items(others),
            "evidence_roles": counts,
            "impact_description": str(parsed.get("impact_description") or "").strip() if classification in (L.CONFIRMED, L.POTENTIAL) and basis == "TARGET_EVIDENCE" else "",
            "reasoning_summary": str(parsed.get("reasoning_summary") or "").strip() if classification in (L.CONFIRMED, L.POTENTIAL, L.ALREADY_COVERED) and basis == "TARGET_EVIDENCE" else (reasons[0] if reasons else ""),
            "classification_basis": reasons,
            "downgraded_from_model_claim": bool(decision.get("downgraded")),
            "recommended_verification": [str(v).strip() for v in (parsed.get("recommended_verification") or []) if str(v).strip()][:5] if classification in (L.CONFIRMED, L.POTENTIAL) and basis == "TARGET_EVIDENCE" else [],
            "recommended_action": self._recommended_action(classification, change, target, component, basis, version_status, version),
            "executive_summary": self._finding_summary(classification, change, target, component, bool(history), basis, version_status, version, kind),
            "version_comparison": comparison,
            "conflicting_evidence": comparison["conflict"],
        }

        return finding

    def _recommended_action(self, classification, change, target, component, basis="TARGET_EVIDENCE", version_status=None, version=None):

        name = change.get("name") or "this change"
        scope = target.get("domain") + (" / " + target.get("module") if target.get("module") else "")
        where = component or scope
        prior = (version_status or {}).get("prior_version")

        if basis == "VERSION_COMPARISON":
            if classification == L.ALREADY_COVERED:
                return "No action for \"%s\": it was already part of version %s. Assess it only if it was never assessed for that version." % (name, prior)
            if (version_status or {}).get("status") == "CHANGED" and (version_status or {}).get("material"):
                return ("Confirm with the business analyst and the target owner whether \"%s\" (changed between version %s and version %s) requires a change in %s, and add or update "
                        "regression tests if it does. Index an SRS, design or API document for %s so the effect on the implemented behaviour can be confirmed." % (name, prior, version, scope, scope))
            return "Review \"%s\" with the business analyst to decide whether version %s needs a change in %s. Do not change the target until this is confirmed." % (name, version, scope)

        if classification == L.CONFIRMED:
            return "Update %s to implement \"%s\" and add or update regression tests for it. Re-run this analysis after the target requirement is revised." % (where, name)
        if classification == L.POTENTIAL:
            return "Review %s with the target owner or business analyst to decide whether \"%s\" requires a change. Do not change the target until this is confirmed." % (where, name)
        if classification == L.ALREADY_COVERED:
            return "No target change is required for \"%s\". Include the existing behaviour in regression testing to confirm it still works." % name
        if classification == L.INSUFFICIENT:
            return ("Add independent evidence for %s to the Knowledge Hub - a target document (SRS, design, API or integration specification), another change request "
                    "that covers it, or an earlier version of the source - or widen the target scope, then run the analysis again. "
                    "Do not treat this result as either impact or no impact." % scope)
        return "No dependency on \"%s\" is documented in the indexed target knowledge for %s. Verify manually if you expect a dependency." % (name, scope)

    def _finding_summary(self, classification, change, target, component, in_other_versions, basis="TARGET_EVIDENCE", version_status=None, version=None, kind=None):

        name = change.get("name") or "The change"
        scope = target.get("domain") + (" / " + target.get("module") if target.get("module") else "")
        prior = (version_status or {}).get("prior_version")
        source_of_evidence = "the other change request" if kind == L.ROLE_CHANGE_REQUEST else "the target documentation"

        if basis == "VERSION_COMPARISON":
            if classification == L.ALREADY_COVERED:
                return "%s is unchanged: the same wording is in version %s, so it is not a change introduced by version %s." % (name, prior, version)
            if (version_status or {}).get("status") == "CHANGED" and (version_status or {}).get("material"):
                flips = (version_status or {}).get("polarity") or []
                what = (" (%s)" % "; ".join("%s: %s to %s" % (c["label"], c["before"], c["after"]) for c in flips)) if flips else ""
                return ("%s: document change confirmed between version %s and version %s%s (both passages quoted). System impact is only Potential: no independent target "
                        "document or other change request shows whether the implemented target must change." % (name, prior, version, what))
            if (version_status or {}).get("status") == "ADDED":
                return "%s is new in version %s: version %s has no corresponding requirement. Whether the target implements it is not verified." % (name, version, prior)
            return "%s is worded differently from version %s without a demonstrated change of meaning." % (name, prior)

        if classification == L.CONFIRMED:
            text = "%s requires a change in %s: %s states different behaviour (quoted below)." % (name, component or scope, source_of_evidence)
        elif classification == L.POTENTIAL:
            text = "%s may affect %s, but the evidence does not prove that the target must change." % (name, component or scope)
        elif classification == L.ALREADY_COVERED:
            text = "%s is already described by %s, so no target change is required." % (name, source_of_evidence)
        elif classification == L.INSUFFICIENT:
            text = "Impact of %s on %s cannot be determined: the target scope has no usable independent evidence for it." % (name, scope)
        else:
            text = "No documented dependency on %s was found in %s." % (name, scope)

        if in_other_versions:
            text += " The same requirement text also appears in another version of the source document."

        return text

    def _evidence_items(self, baseline, limit=None):

        items = []
        for number, chunk in enumerate(baseline, start=1):
            meta = chunk["meta"]
            items.append({
                "evidence_id": "E%d" % number,
                "file_name": meta.get("file_name"),
                "domain": meta.get("domain"),
                "module": meta.get("module"),
                "knowledge_name": meta.get("knowledge_name"),
                "version": meta.get("version"),
                "document_type": meta.get("document_type"),
                "document_version_id": meta.get("document_version_id"),
                "chunk_number": meta.get("chunk_number"),
                "page": meta.get("page") or meta.get("page_number"),
                "role": chunk["role"],
                "excerpt": " ".join(str(chunk["text"]).split())[:320],
            })
            if limit is not None and len(items) >= limit:
                break
        return items

    def _excluded_items(self, others, limit=6):

        seen, items = set(), []
        for chunk in others:
            meta = chunk["meta"]
            key = (meta.get("file_name"), str(meta.get("version")), chunk["role"])
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "file_name": meta.get("file_name"), "version": meta.get("version"), "document_type": meta.get("document_type"),
                "knowledge_name": meta.get("knowledge_name"), "chunk_number": meta.get("chunk_number"),
                "role": chunk["role"], "role_label": L.ROLE_LABELS[chunk["role"]],
                "excerpt": " ".join(str(chunk["text"]).split())[:200],
            })
            if len(items) >= limit:
                break
        return items

    # --------------------------------------------------
    # Stage 1 -- Source Retrieval
    # --------------------------------------------------

    def _scope_listing(self, filters, max_chunks):
        """Every chunk of a metadata scope in document order (read-only), or None when the store cannot list it."""

        from Core.ai_platform.retrieval import Retriever

        if RetrievalEngine._ranked is None:
            RetrievalEngine._ranked = Retriever.default()

        return RetrievalEngine._ranked.scope_chunks(filters, max_chunks=max_chunks)

    def _load_source(self, source):
        """Every chunk of the selected source document, in document order. Falls back to a ranked sample if the
        whole document cannot be listed; the caller reports that as a coverage warning."""

        listing = None
        filters = {
            "domain": source.get("domain"), "module": source.get("module"), "knowledge_name": source.get("knowledge_name"),
            "version": source.get("version"), "document_type": source.get("document_type"),
            "document_version_id": source.get("document_version_id"), "source_file_names": [source.get("source_document")],
        }

        try:
            listing = self._scope_listing(filters, self.SOURCE_MAX_CHUNKS)

        except Exception:
            self.logger.exception("Impact Analysis: full source listing failed; using ranked retrieval.")

        if listing and listing.get("chunks"):
            if listing.get("truncated"):
                try:
                    listing = self._source_pages(filters)
                except ImpactAnalysisError:
                    raise
                except Exception:
                    self.logger.exception("Impact Analysis: source pagination failed; retaining explicitly partial source content.")
            chunks = listing["chunks"]
            return [c["text"] for c in chunks], [c["metadata"] for c in chunks], {
                "mode": "FULL_DOCUMENT", "total": listing.get("total", len(chunks)), "truncated": bool(listing.get("truncated")),
                "pages": listing.get("pages", 1),
            }

        documents, metadata = self._retrieve_source(source)
        return documents, metadata, {"mode": "RANKED_SAMPLE", "total": len(documents), "truncated": False}

    def _source_pages(self, filters):
        """Continue a large source in bounded read-only pages, not a larger cap.
        Exact text deduplication must not hide chunks sharing a common prefix.
        A failed later page never masquerades as a complete document.
        """
        from Core.ai_platform.retrieval import Retriever
        where = Retriever.build_where(filters)
        if where is None:
            raise ValueError("A source scope is required")
        rows, seen, offset, pages = [], set(), 0, 0
        failed = False
        while True:
            self._progress("source_retrieval", len(rows), 0)
            try:
                got = self.vector_store.collection.get(where=where, include=["documents", "metadatas"], limit=self.SOURCE_MAX_CHUNKS, offset=offset)
                ids, docs, metas = got.get("ids", []), got.get("documents", []), got.get("metadatas", [])
                if len(ids) != len(docs) or len(ids) != len(metas):
                    raise ValueError("Incomplete source page")
            except Exception:
                if not rows:
                    raise
                self.logger.exception("Impact Analysis: later source page failed; preserving retrieved source content.")
                failed = True
                break
            pages += 1
            for text, meta in zip(docs, metas):
                signature = " ".join(str(text or "").split())
                if signature and signature not in seen:
                    seen.add(signature)
                    rows.append({"text": text, "metadata": meta})
            if len(ids) < self.SOURCE_MAX_CHUNKS:
                break
            offset += len(ids)
        rows.sort(key=lambda c: (str(c["metadata"].get("file_name") or ""), int(c["metadata"].get("chunk_number") or 0)))
        return {"chunks": rows, "total": len(rows), "truncated": failed, "pages": pages}

    def _retrieve_source(self, source):

        query = (
            "business rule validation workflow field API endpoint payload "
            "database mapping status transition change"
        )

        result = self.retrieval.retrieve(
            question=query,
            domain=source.get("domain"),
            module=source.get("module"),
            knowledge_name=source.get("knowledge_name"),
            version=source.get("version"),
            document_type=source.get('document_type'),
            document_version_id=source.get('document_version_id'),
            source_file_names=[source.get("source_document")],
            top_k=self.SOURCE_TOP_K,
            rerank_top=self.SOURCE_RERANK_TOP,
        )

        if not result.get("success"):
            return [], []

        return result.get("documents", []), result.get("metadata", [])

    def _source_windows(self, source_chunks):
        """Consecutive chunks packed into windows that fit the model's context budget."""

        windows, current, size = [], [], 0

        for chunk in source_chunks:
            length = len(chunk["text"]) + 24
            if current and size + length > self.SOURCE_MAX_CHARACTERS:
                windows.append(current)
                current, size = [], 0
            current.append(chunk)
            size += length

        if current:
            windows.append(current)

        return windows

    # --------------------------------------------------
    # Stage 2 -- Change Footprint Extraction
    # --------------------------------------------------

    def _extraction_system_prompt(self):

        return (
            "You are a QA analysis engine at Pakistan Single Window (PSW). "
            "You extract structured facts from supplied evidence only. "
            "Never invent information not present in the evidence. "
            "Always respond with exactly one JSON object and nothing else."
        )

    def _extract_footprint_windows(self, source, source_chunks):
        """(footprint, windows_used, windows_total, detail). ``detail`` says exactly which chunks the model saw: examined_chunks, the chunks of
        sections left out by the size limit (skipped_chunks) and the sections whose extraction returned nothing (failed_windows)."""

        windows = self._source_windows(source_chunks)
        used = windows
        merged = []
        detail = {"examined_chunks": set(), "skipped_chunks": set(), "failed_windows": []}

        def numbers(window):
            return {c["meta"].get("chunk_number") for c in window}

        for number, window in enumerate(used, start=1):

            self._progress("source_extraction", number - 1, len(used))

            context = "\n\n".join(
                "[S%d] chunk %s\n%s" % (i, c["meta"].get("chunk_number", "-"), str(c["text"]).strip()) for i, c in enumerate(window, start=1)
            )
            part = self._extract_change_footprint(source, context)

            if part is None:
                if not merged and number == 1:
                    return None, len(used), len(windows), detail
                ids = [n for n in numbers(window) if isinstance(n, int)]
                detail["failed_windows"].append({"window": number, "first_chunk": min(ids) if ids else None, "last_chunk": max(ids) if ids else None})
                continue

            detail["examined_chunks"] |= numbers(window)
            merged = self._merge_footprints(merged, part)
            self._progress("source_extraction", number, len(used))

        return merged, len(used), len(windows), detail

    def _merge_footprints(self, first, second):

        merged = [dict(item, source_evidence=list(item.get("source_evidence", []))) for item in first]

        for item in second:

            target = None
            for existing in merged:
                same_name = L.normalize(existing["name"]) == L.normalize(item["name"])
                if same_name or (L.jaccard(existing["name"], item["name"]) >= 0.7 and L.normalize(existing["type"]) == L.normalize(item["type"])):
                    target = existing
                    break

            if target is None:
                merged.append(dict(item, source_evidence=list(item.get("source_evidence", []))))
                continue

            for e in item.get("source_evidence", []):
                if e not in target["source_evidence"]:
                    target["source_evidence"].append(e)
            target["source_evidence"] = target["source_evidence"][:3]
            if not target.get("change") and item.get("change"):
                target["change"] = item["change"]

        return merged

    def _verify_footprint(self, footprint, source_chunks):
        """Keep only changes whose quoted source text really is in the source document."""

        kept, discarded, seen = [], [], set()

        for change in footprint:

            detail, verified_any = [], False

            for quote in change.get("source_evidence", []):
                index, exact = L.verify_quote(quote, source_chunks)
                item = {"quote": quote, "verified": index is not None, "exact": exact}
                if index is not None:
                    meta = source_chunks[index]["meta"]
                    item.update({"chunk_number": meta.get("chunk_number"), "file_name": meta.get("file_name"), "version": meta.get("version"),
                                 "page": meta.get("page") or meta.get("page_number")})
                    verified_any = True
                detail.append(item)

            if not verified_any:
                discarded.append(change.get("name") or "unnamed change")
                continue

            change = dict(change)
            change["source_evidence_detail"] = detail
            change["source_evidence"] = [d["quote"] for d in detail if d["verified"]]
            signature = (L.normalize(change.get("change") or change.get("name")), tuple(sorted(L.normalize(q) for q in change["source_evidence"])))
            if signature in seen:
                continue
            seen.add(signature)
            kept.append(change)

        return kept, discarded

    def _extract_change_footprint(self, source, source_context):

        source_identity = self._identity_line(source)

        prompt = self.prompts.build_change_footprint_prompt(source_identity, source_context)

        result = self.llm.generate(
            prompt=prompt,
            system_prompt=self._extraction_system_prompt(),
            temperature=0.1,
            max_tokens=self.FOOTPRINT_MAX_TOKENS,
        )

        self.logger.info("[IMPACT-DIAG] footprint_llm_success=%s" % result.get("success"))

        if not result.get("success"):
            # The local model call itself did not complete -- a connectivity/availability failure, distinct from an
            # unparseable-but-received response handled below.
            return None

        parsed = self._parse_json(result.get("response", ""))

        if parsed is None:
            self.logger.warning("[IMPACT-DIAG] footprint JSON parse failed")
            # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: the model DID respond, but the response could not be safely recovered
            # into structured JSON. This is a validation failure, not an availability failure -- never silently
            # manufacture an empty/placeholder footprint to paper over it.
            raise ImpactAnalysisError(
                "ANALYSIS_VALIDATION_FAILED",
                "Impact Analysis could not validate the AI-generated structured result "
                "(change footprint extraction returned unparseable output).",
            )

        changes = parsed.get("changes", [])

        if not isinstance(changes, list):
            raise ImpactAnalysisError(
                "ANALYSIS_VALIDATION_FAILED",
                "Impact Analysis could not validate the AI-generated structured result "
                "(\"changes\" was not a list).",
            )

        # De-duplicate by (type, name) -- an LLM can legitimately emit the same change item twice. Merge instead of
        # producing two separate findings for the same underlying change.
        cleaned = []
        index_by_key = {}

        for item in changes:

            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()

            if not name:
                continue

            item_type = str(item.get("type", "")).strip() or "Testable Acceptance/Behavior Change"
            evidence = [
                str(e).strip() for e in (item.get("source_evidence") or []) if str(e).strip()
            ]

            key = (item_type.lower(), name.lower())

            if key in index_by_key:

                existing = cleaned[index_by_key[key]]

                for e in evidence:
                    if e not in existing["source_evidence"]:
                        existing["source_evidence"].append(e)

                existing["source_evidence"] = existing["source_evidence"][:3]

                if not existing["change"] and item.get("change"):
                    existing["change"] = str(item.get("change", "")).strip()

                continue

            index_by_key[key] = len(cleaned)

            cleaned.append({
                "type": item_type,
                "name": name,
                "change": str(item.get("change", "")).strip(),
                "source_evidence": evidence[:3],
            })

        return cleaned

    # --------------------------------------------------
    # Stage 3 -- Target Query Generation
    # --------------------------------------------------

    def _generate_target_queries(self, change):

        name = (change.get("name") or "").strip()
        change_text = (change.get("change") or "").strip()
        change_type = (change.get("type") or "").strip()

        candidates = []

        if name:
            candidates.append(name)

        if name and change_type:
            candidates.append(f"{change_type} {name}")

        if name and change_text:
            candidates.append(f"{name} {change_text}"[:200])

        seen = set()
        deduped = []

        for candidate in candidates:
            key = candidate.lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(candidate)

        return deduped[: self.MAX_QUERIES_PER_CHANGE]


    # --------------------------------------------------
    # Stage 4 -- Target Retrieval
    # --------------------------------------------------

    def _progress(self, stage, completed, total):
        if getattr(self, "cancel_check", None) and self.cancel_check():
            raise ImpactAnalysisError("ANALYSIS_CANCELLED", "Impact Analysis was cancelled. No completed result was saved.")
        callback = getattr(self, "progress_callback", None)
        if callback:
            callback(stage, completed, total)

    def _target_scope_scan(self, source, target, source_chunks):
        """One complete metadata-scoped scan can prove no admissible target
        evidence exists in scope. Never use a partial/failed scan to skip a
        ranked search, and never require an SRS: other CRFs are admissible.
        """
        try:
            # Inspect raw scoped text before the ranked listing's prefix-based
            # deduplication: it must not hide an independent document whose
            # opening text happens to match a source-family document.
            filters = [{k: v} for k, v in target.items() if k in ("domain", "module") and v]
            if not filters:
                return None
            where = filters[0] if len(filters) == 1 else {"$and": filters}
            listing = self.vector_store.collection.get(where=where, include=["documents", "metadatas"], limit=self.SOURCE_MAX_CHUNKS + 1)
            if not isinstance(listing, dict) or len(listing.get("ids", [])) > self.SOURCE_MAX_CHUNKS:
                return None
            docs, metas = listing.get("documents"), listing.get("metadatas")
            if not isinstance(docs, list) or not isinstance(metas, list) or len(docs) != len(metas) or len(docs) != len(listing.get("ids", [])):
                return None
            if any(m.get("domain") != target.get("domain") or (target.get("module") and m.get("module") != target["module"]) for m in metas):
                return None
            tagged = L.tag_chunks(docs, metas, source, source_chunks)
            if any(c["role"] in L.ADMISSIBLE_ROLES for c in tagged):
                return None
            return docs, metas
        except Exception:
            self.logger.info("Impact Analysis: complete target scope scan unavailable; using ranked retrieval.")
            return None

    def _retrieve_target(self, queries, target):

        all_documents = []
        all_metadata = []
        seen_keys = set()
        failures, successes = 0, 0
        cache = getattr(self, "_target_query_cache", {})

        for query in queries:

            key = (target.get("domain"), target.get("module"), query)
            try:
                if key not in cache:
                    cache[key] = self.retrieval.retrieve(
                        question=query, domain=target.get("domain"), module=target.get("module"),
                        top_k=self.TARGET_TOP_K_PER_QUERY, rerank_top=self.TARGET_RERANK_TOP_PER_QUERY)
                result = cache[key]
            except Exception:
                failures += 1
                continue

            if not result.get("success"):
                failures += 1
                continue
            successes += 1

            for doc, meta in zip(result.get("documents", []), result.get("metadata", [])):

                key = (meta.get("knowledge_id"), meta.get("document_version_id"), meta.get("document_type"), meta.get("version"), meta.get("file_name"), meta.get("chunk_number"), doc[:200])

                if key in seen_keys:
                    continue

                seen_keys.add(key)
                all_documents.append(doc)
                all_metadata.append(meta)

        self._target_retrieval_status = {"status": "RETRIEVAL_PARTIAL" if failures else "SEARCH_EXECUTED", "failed_queries": failures}
        if failures and not successes:
            raise ImpactAnalysisError("TARGET_RETRIEVAL_FAILED", "All target retrieval queries failed.")
        return all_documents, all_metadata

    def _build_target_context(self, baseline):
        """Numbered, labelled evidence blocks for the model. Returns (chunks actually shown, context text); the answer is
        verified against exactly these chunks."""

        shown, blocks, size = [], [], 0

        for chunk in baseline[: self.MAX_BASELINE_CHUNKS]:
            meta = chunk["meta"]
            header = "[E%d] %s | %s | version %s | chunk %s | %s" % (
                len(shown) + 1, meta.get("file_name") or "document", meta.get("document_type") or "document",
                meta.get("version") or "-", meta.get("chunk_number", "-"),
                "ANOTHER CHANGE REQUEST (requested behaviour, not implemented behaviour)" if chunk["role"] == L.ROLE_CHANGE_REQUEST else "target document",
            )
            block = header + "\n" + str(chunk["text"]).strip()
            if shown and size + len(block) > self.TARGET_MAX_CHARACTERS_PER_FINDING:
                break
            shown.append(chunk)
            blocks.append(block)
            size += len(block)

        return shown, "\n\n".join(blocks)

    MAX_RELATED_VERSIONS = 6

    def _load_related_versions(self, source):
        """Full text of the OTHER versions of the selected source document (same Domain/Module/Knowledge/Document Type), found from the
        Knowledge Hub's own records rather than from whatever the target search happened to return. Read-only. Returns
        (documents, ok); ``ok`` is False when the lookup itself failed, which is different from "there are no other versions"."""

        documents = []

        try:
            items = self.metadata.list_items_for_knowledge(source.get("domain"), source.get("module"), source.get("knowledge_name"))

            seen = set()
            for item in items or []:
                if str(item.get("document_type") or "") != str(source.get("document_type") or ""):
                    continue
                if str(item.get("version") or "") == str(source.get("version") or ""):
                    continue
                key = (str(item.get("version")), item.get("file_name"), item.get("document_version_id"))
                if key in seen:
                    continue
                seen.add(key)

                listing = self._scope_listing({
                    "domain": source.get("domain"), "module": source.get("module"), "knowledge_name": source.get("knowledge_name"),
                    "version": item.get("version"), "document_type": item.get("document_type"),
                    "document_version_id": item.get("document_version_id"), "source_file_names": [item.get("file_name")],
                }, self.SOURCE_MAX_CHUNKS)

                if listing and listing.get("chunks"):
                    documents.append({
                        "file_name": item.get("file_name"), "version": item.get("version"), "document_type": item.get("document_type"),
                        "knowledge_name": source.get("knowledge_name"), "truncated": bool(listing.get("truncated")), "total_chunks": listing.get("total", len(listing["chunks"])),
                        "chunks": [{"text": c["text"], "chunk_number": (c.get("metadata") or {}).get("chunk_number")} for c in listing["chunks"]],
                    })

            return documents, True

        except Exception:
            self.logger.exception("Impact Analysis: other-version lookup failed (non-fatal).")
            return [], False

    def _evidence_list(self, metadata_list, limit=3):

        seen = set()
        evidence = []

        for meta in metadata_list:

            key = (meta.get("knowledge_id"), meta.get("file_name"), meta.get("knowledge_name"), meta.get("document_type"), meta.get("version"))

            if key in seen:
                continue

            seen.add(key)

            evidence.append({
                "file_name": meta.get("file_name"),
                "domain": meta.get("domain"),
                "module": meta.get("module"),
                "knowledge_name": meta.get("knowledge_name"),
                "version": meta.get("version"),
                "document_type": meta.get("document_type"),
                "chunk_number": meta.get("chunk_number"),
                "page": meta.get("page") or meta.get("page_number"),
            })

            if len(evidence) >= limit:
                break

        return evidence

    def _has_conflicting_versions(self, metadata_list):
        """Kept for compatibility. A conflict now needs two versions of the same passage whose wording actually differs
        (see Core/impact_analysis_logic.compare_versions); merely retrieving two versions is not a conflict."""

        return False

    # --------------------------------------------------
    # Stage 5/6 -- Dependency Matching / Classification
    # --------------------------------------------------

    def _matching_system_prompt(self):

        return (
            "You are a QA analysis engine at Pakistan Single Window (PSW). "
            "You decide whether a target system must change because of a source change, using only "
            "the numbered evidence supplied, and you quote the evidence word for word. A target that already "
            "documents the same requirement is not impacted. Never invent a dependency. Always "
            "respond with exactly one JSON object and nothing else."
        )

    def _classify(self, change, target, target_context):
        """The model's structured answer, or None. The answer is a proposal only: Core/impact_analysis_logic.decide() verifies
        the quotation and decides the final classification."""

        if not target_context.strip():
            return None

        target_identity = self._identity_line(target)

        prompt = self.prompts.build_impact_matching_prompt(change, target_identity, target_context)

        result = self.llm.generate(
            prompt=prompt,
            system_prompt=self._matching_system_prompt(),
            temperature=0.1,
            max_tokens=self.MATCH_MAX_TOKENS,
        )

        if not result.get("success"):
            return None

        parsed = self._parse_json(result.get("response", ""))

        if not isinstance(parsed, dict):
            return None

        return parsed

    # --------------------------------------------------
    # Stage 7 -- Test Case Traceability (read-only)
    # --------------------------------------------------

    TEST_CASE_PAGE = 500
    TEST_CASE_MAX_PAGES = 10

    def _test_case_version_records(self, version_ids, source_ids):
        """Workbook version records of the given versions and sources (read-only). Overridable; raises when the records cannot be read."""
        from Database.db_manager import DatabaseManager
        from Core.change_impact.store import ScopeReader
        return ScopeReader(DatabaseManager(), {}).test_case_versions(source_ids, version_ids)

    def _annotate_test_scope(self, rows):
        """(annotated rows, ok). Each row is marked CURRENT / SUPERSEDED / UNVERSIONED / UNKNOWN by its workbook version (Core.test_case_scope)."""
        version_ids, source_ids = test_case_scope.collect_ids(rows)
        if not rows or not version_ids:
            return test_case_scope.annotate(rows, [], True), True          # legacy rows without a workbook version: nothing to look up
        try:
            return test_case_scope.annotate(rows, self._test_case_version_records(version_ids, source_ids), True), True
        except Exception:
            self.logger.exception("Impact Analysis: test case version records unavailable (non-fatal); version scope is unverified.")
            return test_case_scope.annotate(rows, [], False), False

    def _load_test_cases(self, target):
        """(rows, ok, info). ok is False when the repository could not be read, so 'unknown' is never reported as 'no coverage'. Every page of the target
        scope is read (up to a hard limit); info = {total, examined, complete} so a partial read is reported rather than silently treated as complete."""

        rows, total = [], None

        try:

            for page in range(self.TEST_CASE_MAX_PAGES):

                result = self.test_cases.list_all_test_cases(
                    domain=target.get("domain"), module=target.get("module"), limit=self.TEST_CASE_PAGE, offset=page * self.TEST_CASE_PAGE,
                )

                if not isinstance(result, dict) or result.get("success") is False or not isinstance(result.get("test_cases"), list):
                    raise ValueError("Invalid test repository response")

                batch = result.get("test_cases", []) if isinstance(result, dict) else []
                if isinstance(result, dict) and result.get("total") is not None:
                    total = result.get("total")
                rows.extend(batch)

                if not batch or total is None or len(rows) >= total:
                    break

            total = len(rows) if total is None else total

            rows, scope_ok = self._annotate_test_scope(rows)
            info = {"total": total, "examined": len(rows), "complete": len(rows) >= total,
                    "status": "SEARCH_EXECUTED", "status_note": "Test search executed in the selected scope.",
                    "version_scope": test_case_scope.summarise(rows, scope_ok)}
            if total == 0:
                # Reuse the repository's count contract; do not infer an empty
                # repository from an empty domain/module selection.
                try:
                    all_tests = self.test_cases.list_all_test_cases(limit=1, offset=0)
                    if not isinstance(all_tests, dict) or all_tests.get("success") is False or not isinstance(all_tests.get("total"), int):
                        raise ValueError("Repository-wide test count unavailable")
                    info["repository_total"] = all_tests["total"]
                    info["status"] = "NO_TEST_CASES_EXIST" if all_tests["total"] == 0 else "NO_TESTS_IN_SCOPE"
                    info["status_note"] = "No test cases exist in the repository." if all_tests["total"] == 0 else "No test cases in the selected target scope; %d test cases exist elsewhere in the repository." % all_tests["total"]
                except Exception:
                    info["status"] = "SCOPE_EMPTY_REPOSITORY_COUNT_UNKNOWN"
                    info["status_note"] = "No test cases in the selected target scope; the repository-wide count could not be read."
            return rows, True, info

        except Exception:

            self.logger.exception("Impact Analysis: test case lookup failed (non-fatal).")

            return [], False, {"total": None, "examined": 0, "complete": False, "status": "REPOSITORY_UNAVAILABLE",
                               "status_note": "Test repository unavailable; test search could not execute and coverage is unknown."}

    def _automation_impact(self, findings, test_rows):
        """Read-only Playwright / API / SQL lookup for the existing tests the findings matched. Failure is reported as AUTOMATION_NOT_READ, never as 'none'."""
        try:
            from Core import impact_automation
            reader = None
            if not any((f.get("test_case_coverage") or {}).get("test_cases") for f in findings):
                return impact_automation.analyse(findings, test_rows, None)       # nothing matched: no database access at all
            sql_reader = None
            try:
                from Database.db_manager import DatabaseManager
                from Core.change_impact.store import ScopeReader
                from Core.change_impact.automation import _sql_text_reader
                reader = ScopeReader(DatabaseManager(), {})
                try:
                    from Shared.central_repository import CentralRepository
                    sql_reader = _sql_text_reader(CentralRepository(), 200000)       # managed SQL files only, read-only
                except Exception:
                    sql_reader = None
            except Exception:
                self.logger.exception("Impact Analysis: automation reader unavailable (non-fatal).")
            return impact_automation.analyse(findings, test_rows, reader, sql_text_reader=sql_reader)
        except Exception:
            self.logger.exception("Impact Analysis: automation impact failed (non-fatal).")
            return {"status": "AUTOMATION_NOT_READ", "note": "Automation impact could not be determined; it is unknown, not 'none'.", "read_only": True,
                    "tests_matched": 0, "scenarios_matched": 0, "scenarios_with_automation": 0, "scenarios_without_automation": 0, "assets": {}, "findings_needing_review": 0}

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def _identity_line(self, scope):

        parts = [
            scope.get("domain"), scope.get("module"), scope.get("knowledge_name"),
            scope.get("version"), scope.get("document_type"), scope.get("source_document"),
        ]

        return " > ".join(p for p in parts if p)

    def _parse_json(self, text):

        if not text:
            return None

        text = text.strip()
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end < start:
            return None

        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)
        except Exception:
            self.logger.exception("Impact Analysis JSON parse failed.")
            return None
