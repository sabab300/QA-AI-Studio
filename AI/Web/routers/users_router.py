# Create: AI/Web/routers/users_router.py

"""
QA AI Studio — Web
Users & Roles Router (Milestone 6 — User Management)

Version: 1.0
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from Core.security import generate_temp_password
from Core.user_repository import UserRepository
from Web.deps import get_current_user, require_permission

router = APIRouter(prefix="/api/users", tags=["users"])
roles_router = APIRouter(prefix="/api/roles", tags=["roles"])
audit_router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


class CreateUserRequest(BaseModel):
    username: str
    email: Optional[str] = ""
    full_name: Optional[str] = ""
    role_id: int


class UpdateUserRoleRequest(BaseModel):
    role_id: int


class SetActiveRequest(BaseModel):
    is_active: bool


class PermissionEntry(BaseModel):
    resource: str
    action: str


class SetRolePermissionsRequest(BaseModel):
    permissions: List[PermissionEntry]


class CreateRoleRequest(BaseModel):
    name: str
    description: Optional[str] = ""


@router.get("")
def list_users(current_user=Depends(require_permission("users", "view"))):

    repository = UserRepository()

    return {"users": repository.list_users()}


@router.post("")
def create_user(
    payload: CreateUserRequest,
    current_user=Depends(require_permission("users", "create")),
):

    repository = UserRepository()

    if repository.get_user_by_username(payload.username):

        raise HTTPException(status_code=409, detail="That username is already taken.")

    temp_password = generate_temp_password()

    new_id = repository.create_user(
        payload.username, payload.email, payload.full_name,
        temp_password, payload.role_id,
    )

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="CREATE_USER",
        resource="users",
        detail=f"Created user '{payload.username}' (id={new_id})",
    )

    # Returned once, same reasoning as the first-run admin password —
    # the caller (an admin) is expected to relay this to the new
    # user out of band; QA AI Studio never emails or stores it again.
    return {"id": new_id, "temporary_password": temp_password}


@router.put("/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UpdateUserRoleRequest,
    current_user=Depends(require_permission("users", "edit")),
):

    repository = UserRepository()

    repository.update_role(user_id, payload.role_id)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="UPDATE_USER_ROLE",
        resource="users",
        detail=f"Set role_id={payload.role_id} on user_id={user_id}",
    )

    return {"status": "updated"}


@router.put("/{user_id}/active")
def set_user_active(
    user_id: int,
    payload: SetActiveRequest,
    current_user=Depends(require_permission("users", "edit")),
):

    if user_id == current_user["id"] and not payload.is_active:

        raise HTTPException(
            status_code=400, detail="You cannot deactivate your own account."
        )

    repository = UserRepository()

    repository.set_active(user_id, payload.is_active)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="SET_USER_ACTIVE",
        resource="users",
        detail=f"is_active={payload.is_active} on user_id={user_id}",
    )

    return {"status": "updated"}


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    current_user=Depends(require_permission("users", "edit")),
):

    repository = UserRepository()

    temp_password = generate_temp_password()

    repository.set_password(user_id, temp_password, must_change_password=True)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="RESET_PASSWORD",
        resource="users",
        detail=f"Reset password for user_id={user_id}",
    )

    return {"temporary_password": temp_password}


# --------------------------------------------------
# Roles
# --------------------------------------------------

@roles_router.get("")
def list_roles(current_user=Depends(require_permission("users", "view"))):

    repository = UserRepository()

    return {"roles": repository.list_roles()}


@roles_router.post("")
def create_role(
    payload: CreateRoleRequest,
    current_user=Depends(require_permission("users", "create")),
):

    repository = UserRepository()

    new_id = repository.create_role(payload.name, payload.description)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="CREATE_ROLE",
        resource="users",
        detail=f"Created role '{payload.name}' (id={new_id})",
    )

    return {"id": new_id}


@roles_router.put("/{role_id}/permissions")
def set_role_permissions(
    role_id: int,
    payload: SetRolePermissionsRequest,
    current_user=Depends(require_permission("users", "edit")),
):

    repository = UserRepository()

    repository.set_role_permissions(
        role_id, [entry.dict() for entry in payload.permissions]
    )

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="SET_ROLE_PERMISSIONS",
        resource="users",
        detail=f"Updated permissions for role_id={role_id}",
    )

    return {"status": "updated"}


# --------------------------------------------------
# Audit Log
# --------------------------------------------------

@audit_router.get("")
def list_audit_logs(
    limit: int = 200,
    current_user=Depends(require_permission("settings", "view")),
):

    repository = UserRepository()

    return {"audit_logs": repository.list_audit_logs(limit=limit)}