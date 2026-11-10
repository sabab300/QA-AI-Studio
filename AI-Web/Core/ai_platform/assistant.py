"""
AssistantService - LOCAL / GLOBAL / HYBRID question answering.

Three independent sources of knowledge (Task 4 corrections):

* ENTERPRISE DOCUMENTS  - Retriever over the knowledge base (read-only)
* GENERAL MODEL KNOWLEDGE - the selected LLM, no sources
* LIVE INTERNET SEARCH  - query only, used when needed / requested / configured

Modes
    LOCAL   enterprise documents only; grounded; abstains without evidence.
    GLOBAL  general model knowledge; live search only when the question needs
            current information, asks for it, or the policy says "always".
            Works offline: no search is needed for general questions.
    HYBRID  enterprise documents first; general model knowledge may add
            clearly-labelled background; search when permitted and appropriate.
    AUTO    chosen by Core.ai_platform.routing (explainable, no extra LLM call);
            ambiguous questions are decided on retrieval evidence.

Internet search being enabled NEVER causes a cloud LLM call: the answer model
is chosen independently and a cloud model is used only if explicitly selected
or authorised as a fallback, and permitted for that data class. Private
enterprise passages are never part of a GLOBAL / general-knowledge prompt.
"""

import re
import threading
import time
import uuid

from Core.ai_platform import evidence as ev
from Core.ai_platform import routing as routing_mod
from Core.ai_platform import runtime
from Core.ai_platform.cache import (
    ANSWER_CACHE, SEMANTIC_CACHE, settings_fingerprint,
)
from Core.ai_platform.gateway import CANCELS, GatewayError, effective_kind
from Core.ai_platform.retrieval import Retriever
from Core.ai_platform.sanitize import redact_for_cloud
from Core.ai_platform.tokens import compress_history, rewrite_followup

def keyword_route(prompt):
    """Compatibility wrapper - see Core.ai_platform.routing.decide()."""
    return routing_mod.keyword_route(prompt)


class _Timer:
    """Collect per-stage durations (ms) for one request."""

    def __init__(self):
        self.ms = {}
        self._t0 = time.perf_counter()

    def stage(self, name):
        return _Stage(self, name)

    def add(self, name, value):
        if value is not None:
            self.ms[name] = int(value)

    def total(self):
        return int((time.perf_counter() - self._t0) * 1000)


class _Stage:
    def __init__(self, timer, name):
        self.timer, self.name = timer, name

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.timer.ms[self.name] = self.timer.ms.get(self.name, 0) + int(
            (time.perf_counter() - self.t) * 1000)
        return False


class _InFlight:
    """Reject an identical question from the same user while it is still being
    answered (double click / Enter pressed twice), instead of running the model
    twice."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}

    def acquire(self, key, ttl=900):
        now = time.time()
        with self._lock:
            for k, t in list(self._active.items()):
                if now - t > ttl:
                    del self._active[k]
            if key in self._active:
                return False
            self._active[key] = now
            return True

    def release(self, key):
        with self._lock:
            self._active.pop(key, None)


IN_FLIGHT = _InFlight()

CONFIDENCE_NOTE = ("The numeric 'confidence' field is a legacy heuristic (evidence strength and a "
                   "lexical source check), not a probability of correctness. See "
                   "platform.assurance for what was actually checked.")


def search_query_for_web(question):
    """What is allowed to leave the machine: the user's own question text,
    with credential-like strings and e-mail addresses masked, capped."""
    return redact_for_cloud(question)[:300]


class AssistantService:

    def __init__(self, platform=None, retriever=None, internet=None,
                 history_fn=None, tool_hook=None, hierarchy_fn=None):
        self._hierarchy_fn = hierarchy_fn
        self._terms_cache = None
        self._platform = platform
        self._retriever = retriever
        self._internet = internet
        self._history_fn = history_fn
        self._tool_hook = tool_hook

    # ------------------------------------------------------------ plumbing

    @property
    def platform(self):
        return self._platform or runtime.get_service()

    @property
    def tool_hook(self):
        return self._tool_hook

    def _hook_for(self, use_tools):
        if not use_tools:
            return None
        if self._tool_hook:
            return self._tool_hook
        try:
            return runtime.get_broker().hook
        except Exception:
            return None

    @property
    def retriever(self):
        if self._retriever is None:
            # one process-wide Retriever: the embedding model and the reranker are
            # loaded ONCE and shared by the assistant and the RAG diagnostics
            self._retriever = runtime.get_retriever()
        return self._retriever

    def internet(self):
        if self._internet is None:
            from Core.internet_search import InternetSearch
            self._internet = InternetSearch()
        return self._internet

    def _history(self, session_id, limit):
        if not session_id or limit <= 0:
            return []
        try:
            if self._history_fn:
                rows = self._history_fn(session_id, limit)
            else:
                from Core.memory_manager import MemoryManager
                rows = MemoryManager().get_recent(session_id=session_id, limit=limit)
            rows = [dict(r) for r in rows or []]
            return list(reversed(rows))    # get_recent is newest-first
        except Exception:
            return []

    # --------------------------------------------------------------- modes

    def _known_terms(self):
        """Domain / module names of the knowledge hierarchy (weak routing cue).

        Cached; any failure just means fewer cues, never a failed request."""

        if self._hierarchy_fn is not None:
            try:
                return list(self._hierarchy_fn() or [])
            except Exception:
                return []
        now = time.time()
        if self._terms_cache and now - self._terms_cache[0] < 120:
            return self._terms_cache[1]
        terms = []
        try:
            from Core.knowledge_web_repository import KnowledgeRepository
            repo = KnowledgeRepository()
            for d in repo.list_domains() or []:
                name = d.get("name") if isinstance(d, dict) else d
                if name:
                    terms.append(str(name))
                    for m in (repo.list_modules(name) or []):
                        mn = m.get("name") if isinstance(m, dict) else m
                        if mn:
                            terms.append(str(mn))
        except Exception:
            terms = []
        self._terms_cache = (now, terms)
        return terms

    def route_prompt(self, requested, scope, prompt, routing):
        """Full, explainable routing decision (see Core.ai_platform.routing)."""
        return routing_mod.decide(prompt, requested, scope or {}, routing,
                                  self._known_terms())

    def resolve_mode(self, requested, scope, prompt, routing):
        """Return (mode, reason) - compatibility wrapper around route_prompt."""
        d = self.route_prompt(requested, scope, prompt, routing)
        return d["mode"], d["reasons"][0]

    # ----------------------------------------------------------------- ask

    def ask(self, prompt, user=None, scope=None, mode="auto", model_mode=None,
            selection=None, use_tools=False, request_id=None, session_id=None):

        tm = _Timer()
        user = user or {}
        username = user.get("username") or ""
        scope = {k: v for k, v in (scope or {}).items() if v not in (None, "", [])}
        request_id = request_id or uuid.uuid4().hex[:16]

        plat = self.platform
        with tm.stage("settings"):
            settings = plat.store.get_settings()
        routing, rag, tokens = settings["routing"], settings["rag"], settings["tokens"]

        # identical question from the same user still running -> do not run twice
        flight_key = repr((user.get("id") or username, (prompt or "").strip().lower(),
                           sorted(scope.items()), (mode or "auto").lower()))
        if not IN_FLIGHT.acquire(flight_key):
            res = self._failure(
                "This question is already being answered. Wait for it to finish or press Stop.",
                {"reasons": []}, code="duplicate_request")
            res["route"], res["intent"], res["execution_time"] = (mode or "auto"), "answer", 0
            res["platform"]["request_id"] = request_id
            res["platform"]["route_decision"] = {"mode_requested": mode or "auto",
                                                 "reasons": ["Duplicate submission rejected."]}
            return res

        try:
            CANCELS.ensure(request_id, owner=user.get("id"))

            with tm.stage("routing"):
                route = self.route_prompt(mode, scope, prompt, routing)
            mode_used = route["mode"]

            effective_model_mode = (model_mode or routing.get("model_mode") or "auto")
            sel = selection if (effective_model_mode == "manual" and selection) else None

            decision = {"mode_requested": mode or "auto", "mode_used": mode_used,
                        "reasons": list(route["reasons"]), "internet_used": False,
                        "cloud_inference_used": False, "model_mode": effective_model_mode,
                        "fallback_used": False, "auto_routed": route["auto"],
                        "probe": route["probe"], "signals": route["signals"]}

            task = {"local": "answer", "global": "global_answer",
                    "hybrid": "hybrid_answer"}[mode_used]

            token = runtime.set_context(username=username, mode=mode_used,
                                        selection=sel, request_id=request_id, task=task)
            hist = {"turns_used": 0, "summarized": 0, "text": ""}
            try:
                with tm.stage("history"):
                    history_rows = self._history(
                        session_id, int(tokens.get("conversation_turns", 4)) + 6)
                    hist = compress_history(
                        history_rows, int(tokens.get("conversation_turns", 4)),
                        int(tokens.get("history_summary_chars", 600)),
                        bool(tokens.get("summarize_history", True)))

                if mode_used == "global":
                    result = self._global(prompt, rag, tokens, routing, sel, hist,
                                          decision, username, request_id, tm)
                else:
                    result = self._local_or_hybrid(
                        prompt, mode_used, scope, rag, tokens, routing, sel, hist,
                        history_rows, decision, user, username, request_id, settings,
                        use_tools, tm)
            except GatewayError as exc:
                result = self._failure(str(exc), decision, code=exc.code,
                                       attempts=exc.attempts)
            finally:
                runtime.reset_context(token)
                CANCELS.finish(request_id)
        finally:
            IN_FLIGHT.release(flight_key)

        final_mode = decision.get("mode_used", mode_used)
        result["route"] = final_mode
        result["intent"] = "answer"
        result["execution_time"] = round(tm.total() / 1000.0, 2)
        plat_out = result.setdefault("platform", {})
        plat_out["request_id"] = request_id
        plat_out["route_decision"] = decision
        plat_out["history"] = {"turns_used": hist["turns_used"],
                               "summarized": hist["summarized"]}
        timing = dict(tm.ms)
        timing["total"] = tm.total()
        plat_out["timing_ms"] = timing
        return result

    # ------------------------------------------------------- local / hybrid

    def _local_or_hybrid(self, prompt, mode, scope, rag, tokens, routing, sel,
                         hist, history_rows, decision, user, username,
                         request_id, settings, use_tools, tm):

        signals = decision.get("signals") or {}
        strong_enterprise = bool(signals.get("enterprise_strong"))
        coverage, chunks, retrieval, scope_pack = None, [], {}, None

        CANCELS.stage(request_id, "searching_knowledge")
        qtext = rewrite_followup(prompt, history_rows)

        # ---- broad question inside an explicit scope: look at the whole scope
        if signals.get("broad") and scope and hasattr(self.retriever, "scope_chunks"):
            with tm.stage("scope_listing"):
                scope_pack = self.retriever.scope_chunks(scope)
            if scope_pack and scope_pack.get("chunks"):
                decision["reasons"].append(
                    "Broad question inside a selected scope: the documents of that scope are "
                    "read in order (not ranked by similarity) so that more than the top few "
                    "passages are considered.")

        if scope_pack and scope_pack.get("chunks"):
            chunks = scope_pack["chunks"]
            assessment = {"sufficient": True, "reason": "scope_listing",
                          "best_similarity": None, "best_relevance": None,
                          "chunks": len(chunks)}
            retrieval = {"diagnostics": {"pool": scope_pack["total"], "after_threshold": scope_pack["total"],
                                         "rerank_used": False, "scope_listing": True},
                         "candidates": None, "knowledge_token": "", "filters": scope}
        else:
            with tm.stage("retrieval"):
                retrieval = self.retriever.retrieve(prompt, rag, scope, query_text=qtext)
            rt = ((retrieval.get("diagnostics") or {}).get("timing_ms")) or {}
            for k in ("embedding", "vector_search", "rerank", "rank_fusion"):
                tm.add(k, rt.get(k))
            chunks = retrieval.get("chunks") or []
            assessment = ev.assess_local(chunks, rag, retrieval.get("diagnostics"))

        decision["evidence"] = assessment
        retr_info = {"diagnostics": retrieval.get("diagnostics"),
                     "candidates": retrieval.get("candidates") if rag.get("diagnostics_enabled") else None,
                     "query_expanded": qtext != prompt}

        # ---- AUTO probe: was this really about the knowledge base? --------------
        if decision.get("probe") and not (scope_pack and scope_pack.get("chunks")):
            probe = ev.assess_probe(chunks, rag)
            decision["probe_result"] = probe
            if not probe["relevant"]:
                decision["reasons"].append(
                    "The knowledge base has no relevant evidence for this question "
                    f"({probe['reason']}), so it is answered from the model's general knowledge "
                    "(GLOBAL). No enterprise passage was sent to the model.")
                decision["mode_used"] = "global"
                return self._general_answer(
                    prompt, "general", "global", rag, tokens, sel, hist, decision,
                    username, request_id, tm,
                    notes=["No matching enterprise document was found; this answer is from the "
                           "model's general knowledge and is not checked against your documents."])
            decision["reasons"].append("The knowledge base has relevant evidence "
                                       f"({probe['reason']}), so LOCAL is used.")

        allowed_web = bool(routing.get("hybrid_use_internet", True)
                           and routing.get("internet_search_enabled", True))
        hybrid_web, web_notes = None, []

        if not assessment["sufficient"]:

            if mode == "local":
                return self._insufficient(prompt, assessment, retrieval, retr_info,
                                          decision, mode)

            # HYBRID: no usable enterprise evidence
            decision["reasons"].append(
                "Local evidence was insufficient "
                f"({assessment['reason']}).")

            if allowed_web:
                CANCELS.stage(request_id, "searching_web")
                with tm.stage("web_search"):
                    hybrid_web = self._web_search(prompt)
                if hybrid_web.get("success"):
                    decision["internet_used"] = True
                else:
                    decision["reasons"].append(
                        "External search failed: " + self._web_error_message(hybrid_web.get("error")))
                    web_notes.append("Internet search was unavailable.")
                    hybrid_web = None
            else:
                decision["reasons"].append("External search is disabled in settings.")

            if not hybrid_web:
                # No documents and no web. A question about PSW's own material
                # must not be answered from memory; a general one can be.
                if strong_enterprise:
                    return self._insufficient(prompt, assessment, retrieval, retr_info,
                                              decision, mode)
                return self._general_answer(
                    prompt, "hybrid_general", "hybrid", rag, tokens, sel, hist,
                    decision, username, request_id, tm,
                    notes=["No matching enterprise document was found"
                           + (" and internet search was not available" if web_notes else "")
                           + "; this answer is from the model's general knowledge and is not "
                             "checked against your documents."])

        elif mode == "hybrid":
            want_web, why = routing_mod.wants_search(signals, routing)
            if want_web and allowed_web and (signals.get("current_info") or signals.get("explicit_web")):
                CANCELS.stage(request_id, "searching_web")
                with tm.stage("web_search"):
                    hybrid_web = self._web_search(prompt)
                if hybrid_web.get("success"):
                    decision["internet_used"] = True
                    decision["reasons"].append("Local evidence was sufficient; public web sources "
                                               "were added because " + why[0].lower() + why[1:])
                else:
                    decision["reasons"].append(
                        "External search failed: " + self._web_error_message(hybrid_web.get("error")))
                    web_notes.append("Internet search was unavailable, so current/public "
                                     "information could not be verified; the answer uses the "
                                     "knowledge base only.")
                    hybrid_web = None
            else:
                decision["reasons"].append(
                    "Local evidence was sufficient; no external search was made.")

        use_chunks = chunks if assessment["sufficient"] else []
        unit_info = None
        if scope_pack and scope_pack.get("chunks") and ev.wants_requirement_units(prompt):
            # "List the requirements" over a whole scope: a rule-based pass reads EVERY
            # passage and keeps only the requirement-like statements, so a scope far
            # larger than the context budget can still be covered (and the answer says
            # exactly how much was read and what the method cannot see).
            units = ev.prioritise_units(ev.extract_requirement_units(scope_pack["chunks"]))
            if units:
                use_chunks = units
                unit_info = {"found": len(units)}
        data_class = "private" if use_chunks else "public"

        # -------- tools (read-only, optional) -----------------------------
        tool_ctx = {"context": "", "sources": [], "status": []}
        hook = self._hook_for(use_tools)
        if hook:
            try:
                CANCELS.stage(request_id, "using_tools")
                with tm.stage("tools"):
                    tool_ctx = hook(prompt=prompt, mode=mode, user=user,
                                    request_id=request_id) or tool_ctx
            except Exception:
                tool_ctx = {"context": "", "sources": [],
                            "status": [{"tool": "tool_hook", "ok": False,
                                        "error": "Tool execution failed."}]}
        if tool_ctx.get("context"):
            data_class = "private"

        # -------- prompt assembly ----------------------------------------
        with tm.stage("context_build"):
            kind = "hybrid" if mode == "hybrid" else "local"
            system, _ = ev.build_prompt(kind, prompt, "", hist["text"])
            budget = ev.context_budget_chars(rag, tokens, system, prompt, hist["text"])

            web_budget = int(rag.get("global_context_chars", 4000)) if hybrid_web else 0
            local_budget = max(budget - web_budget, 0) if hybrid_web else budget

            local_ev = ev.build_local_evidence(use_chunks, local_budget,
                                               compact=bool(scope_pack and scope_pack.get("chunks"))) \
                if use_chunks and local_budget > 300 else {"sources": [], "context": "", "chars": 0, "dropped_for_budget": 0}

            web_ev = ev.build_web_evidence(hybrid_web, min(web_budget, budget),
                                           start_index=1) if hybrid_web else {"sources": [], "context": ""}

            sources = local_ev["sources"] + web_ev["sources"] + tool_ctx.get("sources", [])

            if scope_pack and scope_pack.get("chunks"):
                total = scope_pack["total"]
                used = len(local_ev["sources"])
                docs_n = len({(c["metadata"].get("file_name"), c["metadata"].get("version"))
                              for c in scope_pack["chunks"]})
                if unit_info:
                    parents = {x["chunk_id"] for x in local_ev["sources"]}
                    coverage = {"method": "requirement_extraction", "scope_passages": total,
                                "passages_scanned": total, "passages_used": len(parents),
                                "units_found": unit_info["found"], "units_used": used,
                                "documents": docs_n,
                                "complete": bool(used >= unit_info["found"]
                                                 and not scope_pack.get("truncated")),
                                "order": "labelled requirements first, then document order"}
                    note = (f"Coverage: all {total} passages of the selected scope were scanned by a "
                            f"rule-based extractor; {unit_info['found']} requirement-like statements "
                            f"were found and {used} are provided below. Statements written without a "
                            "label or a shall/must wording cannot be detected this way.")
                    if not coverage["complete"]:
                        note += " Not all found statements fit; do not claim the list is complete."
                else:
                    coverage = {"method": "document_order", "scope_passages": total,
                                "passages_used": used, "documents": docs_n,
                                "complete": bool(used >= total and not scope_pack.get("truncated")),
                                "order": "document order"}
                    note = (f"Coverage: all {total} passages of the selected scope are provided below."
                            if coverage["complete"] else
                            f"Coverage: only {used} of {total} passages of the selected scope fit in the "
                            "context below (document order). Do not claim the list is complete.")
                _m = scope_pack["chunks"][0]["metadata"]
                note = ("Scope of every passage below: "
                        + " / ".join(str(x) for x in (_m.get("domain"), _m.get("module"),
                                                      _m.get("knowledge_name")) if x)
                        + (f" · {_m.get('document_type')} v{_m.get('version')}"
                           if _m.get("document_type") else "") + ".\n" + note)
                local_ev["context"] = note + "\n\n" + local_ev["context"]

            context_parts = [local_ev["context"]]
            if tool_ctx.get("context"):
                context_parts.append("Tool results:\n" + tool_ctx["context"])
            context = "\n\n".join(p for p in context_parts if p)

        if not sources:
            return self._insufficient(prompt, assessment, retrieval, retr_info,
                                      decision, mode)

        kind = "hybrid" if (mode == "hybrid") else "local"
        system, user_prompt = ev.build_prompt(kind, prompt, context, hist["text"],
                                              web_context=web_ev.get("context", ""))

        # -------- cache lookup -------------------------------------------
        cache_cfg = tokens.get("exact_cache") or {}
        cache_key, cacheable = None, (
            cache_cfg.get("enabled") and not hybrid_web and not tool_ctx.get("context")
            and not tool_ctx.get("status"))
        if cacheable:
            cache_key = ANSWER_CACHE.make_key(
                prompt, scope, retrieval.get("knowledge_token", ""),
                self._model_cfg(sel, task_name="answer"),
                settings_fingerprint(settings), mode, user.get("id"), user.get("role_id"))
            hit = ANSWER_CACHE.get(cache_key, int(cache_cfg.get("ttl_s", 600)))
            if hit:
                hit["platform"]["cache"] = {"hit": True}
                hit["platform"]["llm"]["cached"] = True
                self._log_cache_hit(username, mode, hit)
                return hit

        # -------- generate -----------------------------------------------
        CANCELS.stage(request_id, "generating")
        gw = self.platform.gateway
        with tm.stage("model_call"):
            gen = gw.generate(task={"local": "answer", "hybrid": "hybrid_answer"}[mode],
                              prompt=user_prompt, system=system, data_class=data_class,
                              selection=sel, params={"max_tokens": int(tokens.get("output_budget_tokens", 1024)),
                                                     "temperature": 0.1},
                              username=username, mode=mode, request_id=request_id)
        self._merge_provider_timing(tm, gen)

        decision["cloud_inference_used"] = bool(gen.cloud)
        decision["fallback_used"] = bool(gen.fallback_used)
        if gen.fallback_used:
            decision["reasons"].append("The selected model failed; the permitted fallback was used.")

        CANCELS.stage(request_id, "checking_sources")
        with tm.stage("source_validation"):
            answer, grounding = self._finish_answer(gen.text, sources, prompt, rag)
        confidence = ev.confidence_score(assessment, grounding, grounding["citations_used"])

        has_web = bool(web_ev.get("sources"))
        basis = ("mixed" if has_web else "enterprise_documents")
        assurance = ev.assurance(basis, assessment, grounding, coverage=coverage)

        result = self._success(answer, sources, grounding, confidence, gen, retr_info,
                               assessment, local_ev, web_ev, tool_ctx, hist,
                               assurance=assurance, coverage=coverage, notes=web_notes)
        if cache_key and grounding["grounded"] and not grounding["abstained"]:
            snapshot = dict(result)
            ANSWER_CACHE.put(cache_key, snapshot, int(cache_cfg.get("max_items", 200)))
            result["platform"]["cache"] = {"hit": False, "stored": True}
        else:
            result["platform"]["cache"] = {"hit": False, "stored": False}

        return result

    # ---------------------------------------------------------------- global

    def _global(self, prompt, rag, tokens, routing, sel, hist, decision,
                username, request_id, tm):

        signals = decision.get("signals") or routing_mod.analyze(prompt)
        use_search, why = routing_mod.wants_search(signals, routing)
        decision["reasons"].append(why)
        needs_live = bool(signals.get("current_info") or signals.get("explicit_web"))

        web, notes = None, []
        if use_search:
            CANCELS.stage(request_id, "searching_web")
            with tm.stage("web_search"):
                web = self._web_search(prompt)
            if web.get("success"):
                decision["internet_used"] = True
            else:
                msg = self._web_error_message(web.get("error"))
                decision["reasons"].append("Internet search failed: " + msg)
                if needs_live:
                    # live data cannot come from model memory: never fabricate it
                    return self._cannot_verify(msg, decision)
                notes.append("Internet search was unavailable, so this answer is from the "
                             "model's general knowledge and was not checked against the web.")
                web = None
        elif needs_live and not routing.get("internet_search_enabled", True):
            return self._cannot_verify(
                "Internet search is switched off in Settings → LLM → AI Routing.", decision)

        if not web:
            return self._general_answer(prompt, "general", "global", rag, tokens, sel, hist,
                                        decision, username, request_id, tm, notes=notes)

        system, _ = ev.build_prompt("global", prompt, "", "")
        budget = min(int(rag.get("global_context_chars", 4000)),
                     ev.context_budget_chars(rag, tokens, system, prompt, ""))
        web_ev = ev.build_web_evidence(web, budget)
        _, user_prompt = ev.build_prompt("global", prompt, web_ev["context"], "")

        CANCELS.stage(request_id, "generating")
        with tm.stage("model_call"):
            gen = self.platform.gateway.generate(
                task="global_answer", prompt=user_prompt, system=system,
                data_class="public", selection=sel,
                params={"max_tokens": int(tokens.get("output_budget_tokens", 1024)),
                        "temperature": 0.2},
                username=username, mode="global", request_id=request_id)
        self._merge_provider_timing(tm, gen)

        decision["cloud_inference_used"] = bool(gen.cloud)
        decision["fallback_used"] = bool(gen.fallback_used)

        CANCELS.stage(request_id, "checking_sources")
        with tm.stage("source_validation"):
            answer, grounding = self._finish_answer(gen.text, web_ev["sources"], prompt, rag)

        warnings = list(web.get("warnings") or []) + grounding["warnings"]
        n = len(web_ev["sources"])
        conf = max(0, min(100, int(round(
            (0.5 + 0.1 * min(n, 3)) * 100 * grounding["support_ratio"]
            * (1.0 if grounding["grounded"] else 0.7)))))

        res = self._success(answer, web_ev["sources"], grounding, conf, gen,
                            None, None, {"sources": [], "context": ""}, web_ev,
                            {"status": []}, hist,
                            assurance=ev.assurance("web", None, grounding, search_used=True))
        res["warnings"] = warnings
        res["platform"]["web_provider"] = web.get("provider")
        return res

    # --------------------------------------------- general model knowledge

    def _general_answer(self, prompt, kind, mode, rag, tokens, sel, hist, decision,
                        username, request_id, tm, notes=None):
        """Answer from the selected LLM's own knowledge: no sources, no private
        passages, no internet. Used by GLOBAL without search, by the AUTO probe
        fallback and by HYBRID when nothing relevant exists."""

        system, user_prompt = ev.build_prompt(kind, prompt, "", hist.get("text", ""))
        CANCELS.stage(request_id, "generating")
        with tm.stage("model_call"):
            gen = self.platform.gateway.generate(
                task={"global": "global_answer", "hybrid": "hybrid_answer"}[mode],
                prompt=user_prompt, system=system, data_class="public",
                selection=sel,
                params={"max_tokens": int(tokens.get("output_budget_tokens", 1024)),
                        "temperature": 0.2},
                username=username, mode=mode, request_id=request_id)
        self._merge_provider_timing(tm, gen)

        decision["cloud_inference_used"] = bool(gen.cloud)
        decision["fallback_used"] = bool(gen.fallback_used)
        decision["answer_basis"] = "model_knowledge"
        decision["reasons"].append("No enterprise passages and no web sources were included in the "
                                   "prompt; the selected model answered from general knowledge.")

        text = re.sub(r"\[[SWT]\d+\]", "", gen.text or "").strip()   # nothing to cite
        warnings = list(notes or [])
        if getattr(gen, "usage_estimated", False):
            warnings.append("Token counts are estimates (the provider did not report usage).")

        return {
            "success": True, "answer": text, "preformatted": True,
            "sources": [], "citations": [], "warnings": warnings,
            # legacy numeric field: not meaningful for model-knowledge answers
            "confidence": 0,
            "provider": gen.provider, "model": gen.model,
            "platform": {
                "llm": self._llm_block(gen),
                "retrieval": None, "evidence": None,
                "grounding": None, "tools": [], "insufficient_evidence": False,
                "answer_basis": "model_knowledge",
                "assurance": ev.assurance("model_knowledge"),
                "confidence_note": CONFIDENCE_NOTE,
            },
        }

    def _cannot_verify(self, reason, decision):
        """Current / live information was requested but cannot be fetched."""

        decision["reasons"].append("No answer was generated from model memory: the question "
                                   "needs current information that cannot be verified offline.")
        return {
            "success": True, "preformatted": True, "sources": [], "citations": [],
            "answer": ("I could not verify current information for this question. "
                       f"{reason} I did not answer from memory, because current facts "
                       "(versions, prices, news, office holders) may be outdated or wrong. "
                       "Try again when internet search is available, or rephrase the question "
                       "as a general one that does not depend on recent facts."),
            "warnings": ["Fresh information could not be verified: internet search unavailable."],
            "confidence": 0, "provider": None, "model": None,
            "platform": {"llm": None, "retrieval": None, "evidence": None, "tools": [],
                         "insufficient_evidence": False, "live_data_unavailable": True,
                         "assurance": {"basis": "none", "status": "unverifiable",
                                       "level": "none", "calibrated": False,
                                       "label": "Current information could not be verified"}},
        }


    # ------------------------------------------------------------- helpers

    @staticmethod
    def _web_error_message(error):
        """User-safe, actionable text for a failed internet search.

        Raw exception text can contain proxy / host details, so it is never
        shown; only the failure category is."""

        text = str(error or "").lower()
        if "timed out" in text or "timeout" in text:
            why = "the search provider timed out"
        elif "disabled" in text:
            return "Internet search is disabled for this deployment."
        else:
            why = "the search providers could not be reached"
        return (f"Internet search is unavailable right now ({why}). "
                "Check the server's internet / proxy access, or use LOCAL mode "
                "to answer from the knowledge base.")

    def _web_search(self, question):
        query = search_query_for_web(question)
        try:
            return self.internet().search(query) or {"success": False, "error": "No result."}
        except Exception:
            return {"success": False, "error": "Internet search failed."}

    def _finish_answer(self, text, sources, question, rag):
        grounding = ev.check_grounding(
            text, sources, question,
            bool(rag.get("citations_required", True))) \
            if rag.get("grounding_check", True) else \
            {"grounded": True, "abstained": False, "citations_used": sorted(set(re.findall(r"\[([SWT]\d+)\]", text or ""))),
             "invalid_citations": [], "unsupported": [], "support_ratio": 1.0,
             "warnings": [], "answer": (text or "").strip(), "specifics_checked": 0}
        return grounding["answer"], grounding

    def _model_cfg(self, sel, task_name):
        try:
            gw = self.platform.gateway
            pid, model, source = gw.resolve_primary(task_name, sel)
            p = self.platform.store.get_provider(pid) or {}
            return f"{pid}:{p.get('base_url')}:{model}:{source}:{p.get('params')}"
        except Exception:
            return "unresolved"

    @staticmethod
    def _public_sources(sources):
        out = []
        for s in sources:
            item = {k: v for k, v in s.items() if k != "text"}
            item["excerpt"] = " ".join((s.get("text") or "").split())[:320]
            out.append(item)
        return out

    @staticmethod
    def _llm_block(gen):
        return {"provider": gen.provider, "model": gen.model,
                "cloud": bool(gen.cloud), "fallback_used": bool(gen.fallback_used),
                "attempts": gen.attempts, "prompt_tokens": gen.prompt_tokens,
                "completion_tokens": gen.completion_tokens,
                "latency_ms": gen.latency_ms,
                "usage_estimated": bool(getattr(gen, "usage_estimated", False)),
                "timings": dict(getattr(gen, "timings", None) or {}),
                "cached": False}

    @staticmethod
    def _merge_provider_timing(tm, gen):
        """Add the provider-reported breakdown (model load / prompt evaluation /
        generation / queue + overhead) to the request timing."""
        t = getattr(gen, "timings", None) or {}
        for key, name in (("model_load", "model_load"), ("prompt_eval", "prompt_eval"),
                          ("generation", "generation"),
                          ("queue_and_overhead", "model_queue_overhead")):
            tm.add(name, t.get(key))

    def _success(self, answer, sources, grounding, confidence, gen, retr_info,
                 assessment, local_ev, web_ev, tool_ctx, hist, assurance=None,
                 coverage=None, notes=None):

        used = set(grounding["citations_used"])
        verified = set(grounding.get("verified_citations") or [])
        warnings = list(grounding["warnings"]) + list(notes or [])
        if coverage:
            if coverage.get("method") == "requirement_extraction":
                if coverage["complete"]:
                    warnings.append(
                        f"Coverage: all {coverage['scope_passages']} passages of the selected scope were "
                        f"scanned (rule-based); {coverage['units_found']} requirement-like statements were "
                        "found and provided. Requirements written without a label or a shall/must "
                        "wording cannot be detected this way.")
                else:
                    warnings.append(
                        f"Partial coverage: {coverage['units_used']} of {coverage['units_found']} "
                        f"requirement-like statements found in {coverage['scope_passages']} scanned "
                        "passages fit the context, so the list may be incomplete. Narrow the Knowledge / "
                        "Document / Version filter or raise Settings \u2192 LLM \u2192 Knowledge / RAG \u2192 "
                        "Context size.")
            elif coverage["complete"]:
                warnings.append(f"Coverage: all {coverage['scope_passages']} passages of the "
                                "selected scope were provided to the model.")
            else:
                warnings.append(
                    f"Partial coverage: the answer is based on {coverage['passages_used']} of "
                    f"{coverage['scope_passages']} passages of the selected scope, so it may be "
                    "incomplete. Narrow the Document / Version filter or raise Settings \u2192 LLM \u2192 "
                    "Knowledge / RAG \u2192 Context size to cover more.")
        if local_ev.get("dropped_for_budget") and not coverage:
            warnings.append(f"{local_ev['dropped_for_budget']} lower-ranked passage(s) did not "
                            "fit the context budget.")
        if getattr(gen, "usage_estimated", False):
            warnings.append("Token counts are estimates (the provider did not report usage).")

        for s in sources:
            s["cited"] = s["id"] in used
            s["verified"] = s["id"] in verified

        return {
            "success": True,
            "answer": answer,
            "preformatted": True,
            "sources": self._public_sources(sources),
            "citations": sorted(used),
            "warnings": warnings,
            "confidence": confidence,
            "provider": gen.provider,
            "model": gen.model,
            "platform": {
                "llm": self._llm_block(gen),
                "retrieval": retr_info,
                "evidence": assessment,
                "grounding": {k: v for k, v in grounding.items() if k != "answer"},
                "assurance": assurance,
                "coverage": coverage,
                "answer_basis": (assurance or {}).get("basis"),
                "confidence_note": CONFIDENCE_NOTE,
                "tools": tool_ctx.get("status", []),
                "insufficient_evidence": False,
            },
        }

    def _insufficient(self, prompt, assessment, retrieval, retr_info, decision, mode):

        diag = retrieval.get("diagnostics") or {}
        closest = []
        for c in (retrieval.get("candidates") or [])[:3]:
            if c["file_name"]:
                closest.append({"file_name": c["file_name"], "similarity": c["cosine"]})

        where = "the selected knowledge scope" if mode == "local" else "the selected scope and available sources"
        msg = (f"I could not find enough evidence in {where} to answer this reliably. "
               "No answer was generated, to avoid guessing. "
               "Try widening the Domain / Module / Document filters, rephrasing the question, "
               "or switch to HYBRID or GLOBAL mode if the topic is public.")
        if assessment.get("reason") == "referenced_item_not_found":
            miss = ", ".join(assessment.get("missing_references") or [])
            msg = (f"The selected knowledge does not contain {miss}. Passages that merely look "
                   "similar are not evidence for that specific item, so no answer was generated. "
                   "Check the identifier, widen the Domain / Module / Document selection, "
                   "or upload the document that defines it.")
            decision["reasons"].append("No LLM call was made: the referenced item was not found in the selected knowledge.")
        else:
            decision["reasons"].append("No LLM call was made: evidence did not meet the threshold.")

        return {
            "success": True, "answer": msg, "preformatted": True,
            "sources": [], "citations": [], "confidence": 0,
            "warnings": ["Insufficient evidence in the knowledge base for this question."],
            "provider": None, "model": None,
            "platform": {
                "llm": None, "retrieval": retr_info, "evidence": assessment,
                "closest_documents": closest, "tools": [],
                "insufficient_evidence": True,
                "assurance": ev.assurance("enterprise_documents", assessment,
                                          insufficient=True),
                "pool": diag.get("pool"),
            },
        }

    def _failure(self, message, decision, code="failed", attempts=None, provider=None):
        return {"success": False, "answer": "", "confidence": 0, "error": message,
                "preformatted": True, "sources": [], "warnings": [],
                "provider": provider, "model": None,
                "platform": {"error_code": code, "attempts": attempts or [],
                             "llm": None, "tools": [], "insufficient_evidence": False}}

    def _log_cache_hit(self, username, mode, hit):
        try:
            llm = hit["platform"]["llm"]
            self.platform.store.record_usage(
                username=username, task="answer", mode=mode,
                provider=llm.get("provider"), model=llm.get("model"),
                prompt_tokens=0, completion_tokens=0, latency_ms=0,
                cached=True, success=True, cloud=False, fallback=False)
        except Exception:
            pass
