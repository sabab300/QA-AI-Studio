# Create: AI/Web/routers/auth_router.py

"""
QA AI Studio — Web
Auth Router (Login / Session / Change Password)

Version: 1.0
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from Core.security import create_token, verify_password, hash_password
from Core.user_repository import UserRepository
from Web.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
def login(payload: LoginRequest, request: Request):

    repository = UserRepository()

    user = repository.get_user_by_username(payload.username)

    valid = user is not None and verify_password(
        payload.password, user["password_hash"], user["password_salt"]
    )

    if not valid:

        repository.write_audit_log(
            user_id=user["id"] if user else None,
            username=payload.username,
            action="LOGIN_FAILED",
            ip_address=request.client.host if request.client else None,
        )

        raise HTTPException(status_code=401, detail="Invalid username or password.")

    if not user["is_active"]:

        raise HTTPException(status_code=403, detail="This account has been disabled.")

    token = create_token(
        user["id"], user["username"], user["role_id"], user["role_name"]
    )

    repository.record_login(user["id"])

    repository.write_audit_log(
        user_id=user["id"],
        username=user["username"],
        action="LOGIN",
        ip_address=request.client.host if request.client else None,
    )

    return {
        "token": token,
        "must_change_password": bool(user["must_change_password"]),
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role_id": user["role_id"],
            "role_name": user["role_name"],
        },
    }


@router.post("/logout")
def logout(request: Request, current_user=Depends(get_current_user)):

    repository = UserRepository()

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="LOGOUT",
        ip_address=request.client.host if request.client else None,
    )

    # Tokens are stateless (HMAC-signed, not stored server-side), so
    # "logout" is a client-side discard plus an audit trail entry —
    # there is no server-side session to revoke yet. A future
    # iteration wanting forced/remote logout would need a token
    # denylist table; noted here rather than silently pretended away.
    return {"status": "logged_out"}


@router.get("/me")
def me(current_user=Depends(get_current_user)):

    repository = UserRepository()

    permissions = repository.get_permissions_for_role(current_user["role_id"])

    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "full_name": current_user["full_name"],
        "role_id": current_user["role_id"],
        "role_name": current_user["role_name"],
        "must_change_password": bool(current_user["must_change_password"]),
        "permissions": {
            resource: sorted(actions)
            for resource, actions in permissions.items()
        },
    }


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest, current_user=Depends(get_current_user)
):

    if not verify_password(
        payload.current_password,
        current_user["password_hash"],
        current_user["password_salt"],
    ):

        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    repository = UserRepository()

    repository.set_password(
        current_user["id"], payload.new_password, must_change_password=False
    )

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="CHANGE_PASSWORD",
    )

    return {"status": "password_changed"}
