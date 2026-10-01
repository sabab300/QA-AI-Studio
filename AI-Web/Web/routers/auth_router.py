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


class SwitchRoleRequest(BaseModel):
    role_id: int


def _current_user_payload(repository, user):

    roles = repository.get_user_roles(user["id"])
    permissions = repository.get_permissions_for_role(user["role_id"])
    active_role = next(
        (role for role in roles if role["id"] == user["role_id"]),
        {"id": user["role_id"], "name": user.get("role_name") or ""},
    )

    return {
        "id": user["id"],
        "username": user["username"],
        "full_name": user["full_name"],
        # Backward-compatible active-role aliases.
        "role_id": active_role["id"],
        "role_name": active_role["name"],
        "roles": roles,
        "active_role": active_role,
        "must_change_password": bool(user["must_change_password"]),
        "permissions": {
            resource: sorted(actions)
            for resource, actions in permissions.items()
        },
    }


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

    response_user = _current_user_payload(repository, user)

    return {
        "token": token,
        "must_change_password": bool(user["must_change_password"]),
        "user": response_user,
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

    return _current_user_payload(repository, current_user)


@router.post("/switch-role")
def switch_role(
    payload: SwitchRoleRequest,
    request: Request,
    current_user=Depends(get_current_user),
):

    repository = UserRepository()

    if not repository.is_role_assigned(current_user["id"], payload.role_id):

        raise HTTPException(
            status_code=403,
            detail="That role is not assigned to your account.",
        )

    role = repository.get_role_by_id(payload.role_id)

    if role is None or role.get("is_deleted"):

        raise HTTPException(status_code=404, detail="Role not found.")

    repository.set_default_role(current_user["id"], payload.role_id)
    updated_user = repository.get_user_by_id(current_user["id"])
    token = create_token(
        updated_user["id"], updated_user["username"], role["id"], role["name"]
    )
    repository.write_audit_log(
        user_id=updated_user["id"],
        username=updated_user["username"],
        action="SWITCH_ACTIVE_ROLE",
        resource="users",
        detail=f"Activated assigned role_id={role['id']} ('{role['name']}')",
        ip_address=request.client.host if request.client else None,
    )

    return {
        "token": token,
        "user": _current_user_payload(repository, updated_user),
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
