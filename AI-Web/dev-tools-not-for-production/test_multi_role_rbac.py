"""Isolated regression test for additive multi-role RBAC migration."""

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Core.user_repository as user_repository_module
from Web.routers import auth_router
from Web import deps


class _TempDatabaseManager:
    db_path = None

    def __init__(self):
        self.db_path = type(self).db_path

    def get_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class _Request:
    client = None

    def __init__(self):
        self.state = SimpleNamespace()


class MultiRoleRbacTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        source = Path(__file__).resolve().parents[1] / "Database" / "metadata.db"
        target = Path(self.temp_dir.name) / "metadata.db"
        shutil.copy2(source, target)
        _TempDatabaseManager.db_path = target
        self.original_manager = user_repository_module.DatabaseManager
        user_repository_module.DatabaseManager = _TempDatabaseManager
        self.repository = user_repository_module.UserRepository()

    def tearDown(self):
        user_repository_module.DatabaseManager = self.original_manager
        self.temp_dir.cleanup()

    def test_migration_assignment_switch_and_constraints(self):
        connection = self.repository.db.get_connection()
        before_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        missing = connection.execute(
            "SELECT COUNT(*) FROM users u WHERE u.role_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM user_roles ur "
            "WHERE ur.user_id=u.id AND ur.role_id=u.role_id)"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(missing, 0)

        admin = self.repository.get_role_by_name("Admin")
        engineer = self.repository.get_role_by_name("QA Engineer")
        viewer = self.repository.get_role_by_name("Viewer")
        self.assertTrue(admin and engineer and viewer)

        user_id = self.repository.create_user(
            "multi_role_test", "", "Multi Role Test", "TempPass123",
            role_ids=[admin["id"], engineer["id"], admin["id"]],
        )
        assigned = self.repository.get_user_roles(user_id)
        self.assertEqual({role["id"] for role in assigned}, {admin["id"], engineer["id"]})

        original_deps_repository = deps.UserRepository
        original_verify_token = deps.verify_token
        deps.UserRepository = lambda: self.repository
        try:
            deps.verify_token = lambda token: {"user_id": user_id, "role_id": engineer["id"]}
            resolved = deps.get_current_user(_Request(), SimpleNamespace(credentials="signed"))
            self.assertEqual(resolved["role_id"], engineer["id"])
            deps.verify_token = lambda token: {"user_id": user_id, "role_id": viewer["id"]}
            with self.assertRaises(HTTPException) as stale:
                deps.get_current_user(_Request(), SimpleNamespace(credentials="signed"))
            self.assertEqual(stale.exception.status_code, 401)
        finally:
            deps.UserRepository = original_deps_repository
            deps.verify_token = original_verify_token

        connection = self.repository.db.get_connection()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO user_roles(user_id,role_id) VALUES (?,?)",
                (user_id, admin["id"]),
            )
        connection.rollback()
        connection.close()

        original_repository = auth_router.UserRepository
        original_token = auth_router.create_token
        auth_router.UserRepository = lambda: self.repository
        auth_router.create_token = lambda *args: "refreshed-token"
        try:
            current_user = self.repository.get_user_by_id(user_id)
            switched = auth_router.switch_role(
                auth_router.SwitchRoleRequest(role_id=engineer["id"]),
                _Request(), current_user,
            )
            self.assertEqual(switched["token"], "refreshed-token")
            self.assertEqual(switched["user"]["active_role"]["id"], engineer["id"])
            self.assertEqual(
                switched["user"]["permissions"],
                {key: sorted(value) for key, value in self.repository.get_permissions_for_role(engineer["id"]).items()},
            )
            with self.assertRaises(HTTPException) as denied:
                auth_router.switch_role(
                    auth_router.SwitchRoleRequest(role_id=viewer["id"]),
                    _Request(), self.repository.get_user_by_id(user_id),
                )
            self.assertEqual(denied.exception.status_code, 403)
        finally:
            auth_router.UserRepository = original_repository
            auth_router.create_token = original_token

        self.repository.set_user_roles(
            user_id, [engineer["id"], viewer["id"]],
            active_role_id=engineer["id"],
        )
        self.assertEqual(
            {role["id"] for role in self.repository.get_user_roles(user_id)},
            {engineer["id"], viewer["id"]},
        )
        single_id = self.repository.create_user(
            "single_role_test", "", "Single Role Test", "TempPass123",
            role_ids=[viewer["id"]],
        )
        self.assertEqual(self.repository.get_user_roles(single_id), [{"id": viewer["id"], "name": "Viewer"}])

        connection = self.repository.db.get_connection()
        after_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        connection.close()
        self.assertEqual(after_users, before_users + 2)


if __name__ == "__main__":
    unittest.main()
