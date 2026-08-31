# Replace: AI/Web/deps.py

"""
QA AI Studio — Web
Shared FastAPI Dependencies

Version: 1.1

get_current_user() and require_permission() are the two building
blocks every other router in Web/routers/ is expected to use —
nothing under /api should be reachable without a valid session
token, and nothing that mutates data should be reachable without
the specific role_permissions row for it (see Core/user_repository.py
for the resource/action vocabulary).

v1.1: switched from a plain `Header(None)` parameter to FastAPI's
HTTPBearer security scheme. Functionally identical, but a plain
header parameter never gets registered as an OpenAPI "security
scheme" — so /docs' global Authorize padlock has nothing to attach
to, and every endpoint silently needed Authorization typed into its
own per-request field instead. HTTPBearer fixes that: click
Authorize once in /docs, paste the token, and every endpoint below
picks it up automatically.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from Core.security import verify_token
from Core.user_repository import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):

    if credentials is None or not credentials.credentials:

        raise HTTPException(
            status_code=401, detail="Missing or malformed Authorization header."
        )

    payload = verify_token(credentials.credentials)

    if payload is None:

        raise HTTPException(
            status_code=401, detail="Session expired or invalid — please log in again."
        )

    repository = UserRepository()

    user = repository.get_user_by_id(payload["user_id"])

    if user is None or not user["is_active"]:

        raise HTTPException(
            status_code=401, detail="This account is no longer active."
        )

    request.state.current_user = user

    return user


def require_permission(resource: str, action: str):
    """
    Usage: @router.post(..., dependencies=[Depends(require_permission("users", "create"))])

    Returns a FastAPI dependency closed over this specific
    (resource, action) pair — it re-checks the caller's session
    token itself (rather than depending on get_current_user via
    Depends(...)) so it stays a single, easily-testable function.
    """

    async def _require(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    ):

        user = get_current_user(request, credentials)

        repository = UserRepository()

        permissions = repository.get_permissions_for_role(user["role_id"])

        allowed = action in permissions.get(resource, set())

        if not allowed:

            raise HTTPException(
                status_code=403,
                detail=(
                    f"Your role ('{user.get('role_name')}') does not have "
                    f"'{action}' access to '{resource}'."
                ),
            )

        return user

    return _require
