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
from Core.impact_report_builder import ImpactReportBuilder
from Core.impact_analysis_repository import ImpactAnalysisRepository


class ImpactAnalysisError(Exception):
    """Carries one of the controlled failure codes Requirement 34 asks
    for -- never collapsed into a generic 'Unable to answer.'"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ImpactAnalysisService:

    # Sensible, configurable caps (Requirement 15/30) -- keep total LLM
    # calls and retrieval volume bounded regardless of how large a CRF's
    # extracted footprint turns out to be.
    MAX_CHANGE_ITEMS = 8
    MAX_QUERIES_PER_CHANGE = 3

    SOURCE_TOP_K = 50
    SOURCE_RERANK_TOP = 15
    SOURCE_MAX_CHARACTERS = 8000

    TARGET_TOP_K_PER_QUERY = 15
    TARGET_RERANK_TOP_PER_QUERY = 4
    TARGET_MAX_CHARACTERS_PER_FINDING = 3500

    FOOTPRINT_MAX_TOKENS = 1024
    MATCH_MAX_TOKENS = 512

    TEXT_FIELDS_FOR_MATCH = ("scenario", "test_case", "steps", "expected_result", "pre_conditions")

    VALID_CLASSIFICATIONS = {"CONFIRMED IMPACT", "POTENTIAL IMPACT", "NO DOCUMENTED IMPACT"}
    VALID_EVIDENCE_STRENGTHS = {"DIRECT", "INDIRECT", "INSUFFICIENT"}

    def __init__(self):

        self.logger = Logger.get_logger()

        self.metadata = MetadataManager()
        self.vector_store = VectorStore()
        self.retrieval = RetrievalEngine()
        self.context_builder = ContextBuilder()
        self.llm = OllamaProvider()
        self.prompts = PromptBuilder()
        self.test_cases = TestCaseRepository()
        self.report_builder = ImpactReportBuilder()
        self.history = ImpactAnalysisRepository()

    # --------------------------------------------------
    # Entry Point
    # --------------------------------------------------

    def analyze(self, session_id, source, target, question=None):

        start = time.perf_counter()

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

        coverage_warnings = []

        # Stage 1 -- source document retrieval, constrained to exactly
        # the selected document via source_file_names.
        source_documents, source_metadata = self._retrieve_source(source)

        self.logger.info("[IMPACT-DIAG] source_chunks=%s" % len(source_documents))

        source_context = self.context_builder.build(
            source_documents, source_metadata, max_characters=self.SOURCE_MAX_CHARACTERS,
        )

        if not source_context.strip():
            return {
                "success": False,
                "error_code": "NO_CHANGE_FOOTPRINT",
                "error": "No retrievable source content for the selected document.",
                "execution_time": round(time.perf_counter() - start, 2),
            }

        # Stage 2 -- structured change footprint extraction.
        footprint = self._extract_change_footprint(source, source_context)

        if footprint is None:
            return {
                "success": False,
                "error_code": "OLLAMA_UNAVAILABLE",
                "error": "Change footprint extraction failed -- Ollama did not return a usable response.",
                "execution_time": round(time.perf_counter() - start, 2),
            }

        self.logger.info("[IMPACT-DIAG] extracted_changes=%s" % len(footprint))

        if not footprint:
            # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: a genuinely empty extraction
            # (Ollama responded with valid JSON but an empty "changes" array)
            # is Requirement 34's named NO_CHANGE_FOOTPRINT failure code, not
            # a silent success -- it must not be indistinguishable from a
            # real "zero impact" result in the response contract.
            return {
                "success": False,
                "error_code": "NO_CHANGE_FOOTPRINT",
                "error": "No supported change footprint could be extracted from the selected source document.",
                "execution_time": round(time.perf_counter() - start, 2),
            }

        assessed_footprint = footprint[: self.MAX_CHANGE_ITEMS]

        if len(footprint) > self.MAX_CHANGE_ITEMS:
            coverage_warnings.append(
                f"{len(footprint) - self.MAX_CHANGE_ITEMS} additional extracted source change(s) "
                "were not individually assessed due to analysis size limits."
            )

        findings = []
        total_target_chunks = 0
        matched_docs = set()
        total_queries = 0
        test_case_cache = None

        for index, change in enumerate(assessed_footprint, start=1):

            queries = self._generate_target_queries(change)
            total_queries += len(queries)

            target_documents, target_metadata = self._retrieve_target(queries, target)

            total_target_chunks += len(target_documents)

            for meta in target_metadata:
                matched_docs.add((meta.get("knowledge_id"), meta.get("file_name"), meta.get("knowledge_name"), meta.get("document_type"), meta.get("version")))

            target_context = self.context_builder.build(
                target_documents, target_metadata,
                max_characters=self.TARGET_MAX_CHARACTERS_PER_FINDING,
            )

            classification_result = self._classify(change, target, target_context)

            if classification_result is None:
                classification_result = {
                    "classification": "NO DOCUMENTED IMPACT",
                    "evidence_strength": "INSUFFICIENT",
                    "impact_area": change.get("type", ""),
                    "target_component": "",
                    "impact_description": "",
                    "reasoning_summary": (
                        "Classification could not be completed for this change "
                        "(Ollama did not return a usable response)."
                    ),
                    "recommended_verification": [],
                }

            finding = {
                "finding_id": f"IA-{index:03d}",
                "classification": classification_result.get("classification", "NO DOCUMENTED IMPACT"),
                "evidence_strength": classification_result.get("evidence_strength", "INSUFFICIENT"),
                "impact_area": classification_result.get("impact_area") or change.get("type", ""),
                "source_change": {
                    "type": change.get("type"), "name": change.get("name"), "change": change.get("change"),
                },
                "source_evidence": change.get("source_evidence", []),
                "target_domain": target.get("domain"),
                "target_module": target.get("module"),
                "target_component": classification_result.get("target_component", ""),
                "target_evidence": self._evidence_list(target_metadata),
                "impact_description": classification_result.get("impact_description", ""),
                "reasoning_summary": classification_result.get("reasoning_summary", ""),
                "recommended_verification": classification_result.get("recommended_verification", []),
                "conflicting_evidence": self._has_conflicting_versions(target_metadata),
            }

            if finding["classification"] != "NO DOCUMENTED IMPACT":

                if test_case_cache is None:
                    test_case_cache = self._load_test_cases(target)

                finding["test_case_coverage"] = self._classify_test_case_coverage(
                    change, target_metadata, test_case_cache,
                )

            findings.append(finding)

        confirmed = sum(1 for f in findings if f["classification"] == "CONFIRMED IMPACT")
        potential = sum(1 for f in findings if f["classification"] == "POTENTIAL IMPACT")
        undocumented = sum(1 for f in findings if f["classification"] == "NO DOCUMENTED IMPACT")
        testcase_matches = sum(
            len((f.get("test_case_coverage") or {}).get("test_cases", [])) for f in findings
        )

        self.logger.info(
            "[IMPACT-DIAG] target_queries=%s target_chunks=%s confirmed=%s potential=%s "
            "undocumented=%s testcase_matches=%s" % (
                total_queries, total_target_chunks, confirmed, potential, undocumented, testcase_matches,
            )
        )

        if total_target_chunks == 0:
            scope = target.get("domain") + (f" / {target.get('module')}" if target.get("module") else "")
            coverage_warnings.append(
                f"Target scope ({scope}) returned no matching evidence for any extracted "
                "change -- findings below reflect an absence of documented target knowledge, "
                "not a confirmed absence of real-world impact."
            )

        result = {
            "success": True,
            "source": source,
            "target": target,
            "coverage": {
                "source_chunks_analyzed": len(source_documents),
                "target_chunks_analyzed": total_target_chunks,
                "target_documents_matched": len(matched_docs),
                "warnings": coverage_warnings,
            },
            "change_footprint": footprint,
            "findings": findings,
            "execution_time": round(time.perf_counter() - start, 2),
        }

        result["report_text"] = self.report_builder.build(result)

        self._save_history(session_id, source, target, question, result)

        return result

    def _save_history(self, session_id, source, target, question, result):

        try:
            self.history.save(
                session_id, source, target, question, result,
                execution_time=result.get("execution_time", 0),
            )
        except Exception:
            self.logger.exception("Failed to persist Impact Analysis history (non-fatal).")

    # --------------------------------------------------
    # Stage 1 -- Source Retrieval
    # --------------------------------------------------

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
            # The local Ollama call itself did not complete -- a
            # connectivity/availability failure, distinct from an
            # unparseable-but-received response handled below.
            return None

        parsed = self._parse_json(result.get("response", ""))

        if parsed is None:
            self.logger.warning("[IMPACT-DIAG] footprint JSON parse failed")
            # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: Ollama DID respond, but the
            # response could not be safely recovered into structured JSON
            # (no fenced/bracketed object found, or invalid JSON inside
            # it). This is a validation failure, not an availability
            # failure -- never silently manufacture an empty/placeholder
            # footprint to paper over it.
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

        # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: de-duplicate by (type, name) --
        # an LLM can legitimately emit the same change item twice (e.g.
        # once per sentence that mentions it). Merge instead of producing
        # two separate findings for the same underlying change.
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

    def _retrieve_target(self, queries, target):

        all_documents = []
        all_metadata = []
        seen_keys = set()

        for query in queries:

            result = self.retrieval.retrieve(
                question=query,
                domain=target.get("domain"),
                module=target.get("module"),
                top_k=self.TARGET_TOP_K_PER_QUERY,
                rerank_top=self.TARGET_RERANK_TOP_PER_QUERY,
            )

            if not result.get("success"):
                continue

            for doc, meta in zip(result.get("documents", []), result.get("metadata", [])):

                key = (meta.get("knowledge_id"), meta.get("document_version_id"), meta.get("document_type"), meta.get("version"), meta.get("file_name"), meta.get("chunk_number"), doc[:200])

                if key in seen_keys:
                    continue

                seen_keys.add(key)
                all_documents.append(doc)
                all_metadata.append(meta)

        return all_documents, all_metadata

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
                # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: pass through chunk-level
                # identity when the retrieved metadata actually carried it
                # (same fields Core/citation_engine.py already cites from)
                # -- never invented when absent, per Requirement 26/29.
                "chunk_number": meta.get("chunk_number"),
                "page": meta.get("page") or meta.get("page_number"),
            })

            if len(evidence) >= limit:
                break

        return evidence

    def _has_conflicting_versions(self, metadata_list):
        """Heuristic proxy for Requirement 27 -- if the target evidence
        used for one finding spans more than one distinct Version of the
        same Knowledge item, flag it rather than silently reconciling.
        This is a textual-contradiction PROXY (same document, different
        versions retrieved), not full semantic contradiction detection,
        and is reported as such."""

        by_knowledge = {}

        for meta in metadata_list:

            name = meta.get("knowledge_name")
            version = meta.get("version")

            if not name or not version:
                continue

            identity=(meta.get('domain'),meta.get('module'),name,meta.get('document_type'))
            by_knowledge.setdefault(identity, set()).add(version)

        return any(len(versions) > 1 for versions in by_knowledge.values())

    # --------------------------------------------------
    # Stage 5/6 -- Dependency Matching / Classification
    # --------------------------------------------------

    def _matching_system_prompt(self):

        return (
            "You are a QA analysis engine at Pakistan Single Window (PSW). "
            "You classify whether a target system's documented evidence "
            "establishes a dependency on a source system change, using only "
            "the evidence supplied. Never invent a dependency. Always "
            "respond with exactly one JSON object and nothing else."
        )

    def _classify(self, change, target, target_context):

        if not target_context.strip():

            return {
                "classification": "NO DOCUMENTED IMPACT",
                "evidence_strength": "INSUFFICIENT",
                "impact_area": change.get("type", ""),
                "target_component": "",
                "impact_description": "",
                "reasoning_summary": "No target evidence was retrieved for this change.",
                "recommended_verification": [],
            }

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

        if parsed is None:
            return None

        classification = str(parsed.get("classification", "")).strip().upper()

        if classification not in self.VALID_CLASSIFICATIONS:
            classification = "POTENTIAL IMPACT" if parsed.get("target_component") else "NO DOCUMENTED IMPACT"

        evidence_strength = str(parsed.get("evidence_strength", "")).strip().upper()

        if evidence_strength not in self.VALID_EVIDENCE_STRENGTHS:
            evidence_strength = "INSUFFICIENT"

        # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: enforce Requirement 36 in
        # code, not only in the prompt -- never let the model's own
        # CONFIRMED IMPACT claim stand unless it also claimed DIRECT
        # evidence strength.
        if classification == "CONFIRMED IMPACT" and evidence_strength != "DIRECT":
            classification = "POTENTIAL IMPACT"

        return {
            "classification": classification,
            "evidence_strength": evidence_strength,
            "impact_area": str(parsed.get("impact_area", "")).strip(),
            "target_component": str(parsed.get("target_component", "")).strip(),
            "impact_description": str(parsed.get("impact_description", "")).strip(),
            "reasoning_summary": str(parsed.get("reasoning_summary", "")).strip(),
            "recommended_verification": [
                str(v).strip() for v in (parsed.get("recommended_verification") or []) if str(v).strip()
            ][:5],
        }

    # --------------------------------------------------
    # Stage 7 -- Test Case Traceability (read-only)
    # --------------------------------------------------

    def _load_test_cases(self, target):

        try:

            result = self.test_cases.list_all_test_cases(
                domain=target.get("domain"), module=target.get("module"), limit=500,
            )

            return result.get("test_cases", []) if isinstance(result, dict) else []

        except Exception:

            self.logger.exception("Impact Analysis: test case lookup failed (non-fatal).")

            return []

    def _classify_test_case_coverage(self, change, target_metadata, test_case_rows):

        name = (change.get("name") or "").strip()

        if len(name) < 3:
            return {"classification": "NO MATCHING COVERAGE FOUND", "test_cases": []}

        name_lower = name.lower()

        target_knowledge_names = {
            meta.get("knowledge_name") for meta in target_metadata if meta.get("knowledge_name")
        }

        existing_coverage = []
        review_required = []

        for row in test_case_rows:

            haystack = " ".join(
                str(row.get(field) or "") for field in self.TEXT_FIELDS_FOR_MATCH
            ).lower()

            if name_lower not in haystack:
                continue

            identifier = row.get("tc_id") or row.get("tc_number") or f"row-{row.get('id')}"

            if row.get("knowledge_name") in target_knowledge_names:
                existing_coverage.append(identifier)
            else:
                review_required.append(identifier)

        if existing_coverage:
            # QA-AI-STUDIO-IMPACT-ANALYSIS-V1: "RELATED EXISTING TEST", not
            # "EXISTING COVERAGE" -- a literal field/name match plus
            # knowledge_name alignment means an existing test case
            # references the same area, not that the changed behavior is
            # already verified. Never imply a changed requirement is
            # covered merely because an old test mentions the same field.
            return {"classification": "RELATED EXISTING TEST", "test_cases": existing_coverage}

        if review_required:
            return {"classification": "REVIEW REQUIRED", "test_cases": review_required}

        return {"classification": "NO MATCHING COVERAGE FOUND", "test_cases": []}

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
