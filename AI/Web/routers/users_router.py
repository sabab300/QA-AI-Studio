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
    contact_number: Optional[str] = ""
    role_id: int


class UpdateUserRoleRequest(BaseModel):
    role_id: int


class UpdateUserProfileRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    contact_number: Optional[str] = None
    role_id: Optional[int] = None


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
        contact_number=payload.contact_number,
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


@router.put("/{user_id}")
def update_user_profile(
    user_id: int,
    payload: UpdateUserProfileRequest,
    current_user=Depends(require_permission("users", "edit")),
):
    """
    Edits a user's profile. Every field but the user's id itself can
    change here — username, email, full name, contact number, and
    which role they hold. Permissions are never edited per-user
    (they live entirely on the role), so this endpoint deliberately
    has no permissions payload at all.
    """

    repository = UserRepository()

    target = repository.get_user_by_id(user_id)

    if target is None:

        raise HTTPException(status_code=404, detail="User not found.")

    if payload.username and payload.username != target["username"]:

        existing = repository.get_user_by_username(payload.username)

        if existing and existing["id"] != user_id:

            raise HTTPException(
                status_code=409, detail="That username is already taken."
            )

    repository.update_user_profile(
        user_id,
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        contact_number=payload.contact_number,
        role_id=payload.role_id,
    )

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="UPDATE_USER_PROFILE",
        resource="users",
        detail=f"Updated profile for user_id={user_id} ('{target['username']}')",
    )

    return {"status": "updated"}


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    current_user=Depends(require_permission("users", "delete")),
):
    """
    Soft-delete only — the row and every audit_logs entry that
    references it are preserved, but the account is hidden from the
    active grid and immediately unable to authenticate (is_active is
    cleared alongside is_deleted). Restricted to the Admin role
    explicitly, on top of the users:delete permission check above,
    so a custom role can never be granted this by accident the way
    the Admin role itself was during permissions testing.
    """

    if current_user.get("role_name") != "Admin":

        raise HTTPException(
            status_code=403,
            detail="Only the Admin role can delete users.",
        )

    if user_id == current_user["id"]:

        raise HTTPException(
            status_code=400, detail="You cannot delete your own account."
        )

    repository = UserRepository()

    target = repository.get_user_by_id(user_id)

    if target is None:

        raise HTTPException(status_code=404, detail="User not found.")

    if target.get("role_name") == "Admin":

        remaining = repository.count_active_admins(
            target["role_id"], exclude_user_id=user_id
        )

        if remaining == 0:

            raise HTTPException(
                status_code=400,
                detail=(
                    "This is the last active Admin account — deleting it "
                    "would lock everyone out. Promote another user to "
                    "Admin first."
                ),
            )

    repository.soft_delete_user(user_id)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="DELETE_USER",
        resource="users",
        detail=f"Deleted user_id={user_id} ('{target['username']}')",
    )

    return {"status": "deleted"}


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

    if repository.get_role_by_name(payload.name):

        raise HTTPException(status_code=409, detail="A role with that name already exists.")

    new_id = repository.create_role(payload.name, payload.description)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="CREATE_ROLE",
        resource="users",
        detail=f"Created role '{payload.name}' (id={new_id})",
    )

    return {"id": new_id}


@roles_router.delete("/{role_id}")
def delete_role(
    role_id: int,
    current_user=Depends(require_permission("users", "delete")),
):
    """Soft-delete only, and blocked entirely for the three built-in
    roles (Admin/QA Engineer/Viewer) and for any role still assigned
    to at least one user — both to avoid ever leaving a user pointing
    at a role that no longer effectively exists."""

    if current_user.get("role_name") != "Admin":

        raise HTTPException(
            status_code=403,
            detail="Only the Admin role can delete roles.",
        )

    repository = UserRepository()

    role = repository.get_role_by_id(role_id)

    if role is None:

        raise HTTPException(status_code=404, detail="Role not found.")

    if role.get("is_system"):

        raise HTTPException(
            status_code=400,
            detail=f"'{role['name']}' is a built-in role and can't be deleted.",
        )

    assigned = repository.count_users_with_role(role_id)

    if assigned > 0:

        raise HTTPException(
            status_code=400,
            detail=(
                f"{assigned} user(s) are still assigned to '{role['name']}' — "
                "move them to a different role first."
            ),
        )

    repository.soft_delete_role(role_id)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="DELETE_ROLE",
        resource="users",
        detail=f"Deleted role '{role['name']}' (id={role_id})",
    )

    return {"status": "deleted"}


@roles_router.put("/{role_id}/permissions")
def set_role_permissions(
    role_id: int,
    payload: SetRolePermissionsRequest,
    current_user=Depends(require_permission("users", "edit")),
):

    repository = UserRepository()

    permission_dicts = [entry.dict() for entry in payload.permissions]

    repository.set_role_permissions(role_id, permission_dicts)

    summary = repository.summarize_permissions(permission_dicts)

    repository.write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action="SET_ROLE_PERMISSIONS",
        resource="users",
        detail=f"Updated permissions for role_id={role_id} — {summary}",
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