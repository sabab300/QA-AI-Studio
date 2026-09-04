"""In-memory lifecycle for user-guided Knowledge Hub Playwright sessions."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

class KnowledgeDiscoverySessions:
    _sessions = {}
    _lock = threading.RLock()
    timeout_seconds = 30 * 60

    @classmethod
    def _cleanup_expired(cls):
        now = time.time()
        expired = []
        with cls._lock:
            for session_id, state in cls._sessions.items():
                if now - state["touched"] > cls.timeout_seconds:
                    expired.append(session_id)
        for session_id in expired:
            cls.close(session_id)

    @classmethod
    def create(cls, url, authentication_type="NONE"):
        cls._cleanup_expired()
        url = str(url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("A valid HTTP or HTTPS URL is required.")
        session_id = uuid.uuid4().hex
        from Core.url_authenticated_session import URLAuthenticatedSession
        with cls._lock:
            cls._sessions[session_id] = {
                "url": url,
                "authentication_type": str(authentication_type or "NONE").upper(),
                "runtime": URLAuthenticatedSession(headless=True),
                "executor": ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"kh-discovery-{session_id[:8]}"),
                "touched": time.time(),
            }
        return {"session_id": session_id, "url": url, "status": "Pending"}

    @classmethod
    def _get(cls, session_id):
        cls._cleanup_expired()
        with cls._lock:
            state = cls._sessions.get(session_id)
            if not state:
                raise KeyError("Discovery session not found or expired.")
            state["touched"] = time.time()
            return state

    @classmethod
    def authenticate(cls, session_id, credentials=None, analysis=None):
        state = cls._get(session_id)
        analysis = dict(analysis or {})
        analysis["authentication_type"] = state["authentication_type"]
        result = state["executor"].submit(
            state["runtime"].authenticate,
            state["url"],
            analysis=analysis,
            credentials=dict(credentials or {}),
        ).result()
        # Never return runtime objects or browser storage state to the client.
        return {
            key: value for key, value in result.items()
            if key not in {"session", "_authenticated_session", "authenticated_session", "storage_state"}
        }

    @classmethod
    def navigate(cls, session_id, url):
        state = cls._get(session_id)
        def _navigate():
            page = state["runtime"].get_authenticated_page()
            page.goto(url, wait_until="domcontentloaded")
            return {"success": True, "url": page.url, "page_title": page.title()}
        return state["executor"].submit(_navigate).result()

    @classmethod
    def scan(cls, session_id, label=""):
        from Core.url_discovery_engine import URLDiscoveryEngine
        state = cls._get(session_id)
        def _scan():
            runtime = state["runtime"]
            return URLDiscoveryEngine(
                context=runtime.get_authenticated_context(),
                page=runtime.get_authenticated_page(),
            ).scan_current_view(label=label)
        return state["executor"].submit(_scan).result()

    @classmethod
    def save(cls, session_id, steps, hierarchy):
        from Core.discovery_repository import DiscoveryRepository
        state = cls._get(session_id)
        result = DiscoveryRepository().save_flow_result(
            steps=steps,
            source_url=state["url"],
            auth_type=state["authentication_type"],
            application_name=hierarchy.get("application_name"),
            business_process_name=hierarchy.get("business_process_name"),
            variant_name=hierarchy.get("variant_name"),
            domain=hierarchy.get("domain"),
            module=hierarchy.get("module"),
            knowledge_name=hierarchy.get("knowledge_name"),
            version=hierarchy.get("version"),
        )
        if result.get("success"):
            cls.close(session_id)
        return result

    @classmethod
    def close(cls, session_id):
        with cls._lock:
            state = cls._sessions.pop(session_id, None)
        if state:
            try:
                state["executor"].submit(state["runtime"].close).result(timeout=15)
            finally:
                state["executor"].shutdown(wait=False, cancel_futures=True)
        return bool(state)
