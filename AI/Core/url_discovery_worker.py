# AI/Core/url_discovery_worker.py

from AI.Core.url_access_analyzer import AuthenticatedSessionManager
from AI.Core.url_discovery_engine import URLDiscoveryEngine


def run_authenticated_discovery(login_url: str, target_url: str, credentials: dict) -> dict:
    """Orchestrates authentication and discovery on active Playwright sessions."""
    session_mgr = AuthenticatedSessionManager()

    # Step 1: Create session payload
    session_data = session_mgr.create_authenticated_session(
        login_url=login_url,
        username=credentials.get("username", ""),
        password=credentials.get("password", "")
    )

    # Step 2: Strictly validate active session payload
    if not session_data or not isinstance(session_data, dict) or not session_data.get("context"):
        raise RuntimeError("Authentication completed but no live authenticated Playwright session was returned.")

    try:
        # Step 3: Execute discovery over live context
        context = session_data["context"]
        active_url = session_data.get("current_url", target_url)

        discovery_engine = URLDiscoveryEngine(context=context)
        discovery_result = discovery_engine.discover(url=active_url)
        return discovery_result

    finally:
        # Step 4: Cleanup after discovery completes
        session_mgr.close()