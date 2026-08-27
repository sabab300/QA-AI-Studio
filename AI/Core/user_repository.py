# Create: AI/Core/user_repository.py

"""
QA AI Studio
User / Role / Permission Repository

Version: 1.0

Backs Milestone 6 (User Management) of the QA AI Studio Production
Roadmap: Login, Role Management, Permissions, Audit Logs.

Self-initializing, following the same pattern already used by
Core/test_case_repository.py (ensure_schema() creates its own
tables on first use rather than touching Database/schema.py).

Password storage: PBKDF2-HMAC-SHA256 with a random per-user salt
(Core.security — stdlib only, no new dependency required just to
run the app). Roles are a simple table with one row per
(role, resource, action) permission grant, which keeps "can this
role do X on screen Y" a single indexed lookup instead of a JSON
blob that has to be parsed and kept in sync by hand.
"""

from datetime import datetime

from Database.db_manager import DatabaseManager
from Core.logger import Logger


# The 7 main menus from the Production Roadmap double as the
# permission "resources". Every screen/action check in the web
# layer should map to one of these plus an action verb below.
RESOURCES = (
    "knowledge",
    "qa_engineering",
    "automation",
    "ai_assistant",
    "dashboard",
    "settings",
    "users",
)

ACTIONS = ("view", "create", "edit", "delete", "execute")

# Seeded once, on first run only (ensure_schema() checks whether
# any role already exists before inserting these).
DEFAULT_ROLES = {
    "Admin": {resource: list(ACTIONS) for resource in RESOURCES},
    "QA Engineer": {
        "knowledge": ["view", "create", "edit"],
        "qa_engineering": ["view", "create", "edit", "execute"],
        "automation": ["view", "create", "edit", "execute"],
        "ai_assistant": ["view"],
        "dashboard": ["view"],
        "settings": [],
        "users": [],
    },
    "Viewer": {
        "knowledge": ["view"],
        "qa_engineering": ["view"],
        "automation": ["view"],
        "ai_assistant": ["view"],
        "dashboard": ["view"],
        "settings": [],
        "users": [],
    },
}


class UserRepository:

    def __init__(self):

        self.db = DatabaseManager()

        self.logger = Logger.get_logger()

        self.ensure_schema()

    # --------------------------------------------------
    # Schema
    # --------------------------------------------------

    def ensure_schema(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS roles
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                is_system INTEGER DEFAULT 0,
                created_date TEXT
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS role_permissions
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER NOT NULL,
                resource TEXT NOT NULL,
                action TEXT NOT NULL,
                UNIQUE(role_id, resource, action),
                FOREIGN KEY(role_id) REFERENCES roles(id) ON DELETE CASCADE
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT,
                full_name TEXT,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role_id INTEGER,
                is_active INTEGER DEFAULT 1,
                must_change_password INTEGER DEFAULT 0,
                created_date TEXT,
                modified_date TEXT,
                last_login_date TEXT,
                FOREIGN KEY(role_id) REFERENCES roles(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                resource TEXT,
                detail TEXT,
                ip_address TEXT,
                created_date TEXT
            )
            """
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_created "
            "ON audit_logs(created_date)"
        )

        conn.commit()

        # Seed default roles + permissions + a first Admin user,
        # but only the very first time this table is ever empty —
        # never overwrites anything an operator has since changed.
        cursor.execute("SELECT COUNT(*) FROM roles")

        if cursor.fetchone()[0] == 0:

            self._seed_default_roles(cursor)

            conn.commit()

        cursor.execute("SELECT COUNT(*) FROM users")

        admin_password = None

        if cursor.fetchone()[0] == 0:

            admin_password = self._seed_default_admin(cursor)

            conn.commit()

        conn.close()

        if admin_password:

            # Intentionally also printed, not just logged — this
            # is the one and only time this password is ever
            # available in clear text, and log files rotate.
            message = (
                "QA AI Studio: created initial admin account — "
                f"username 'admin', password '{admin_password}'. "
                "You will be required to change it on first login."
            )

            print(message)

            self.logger.warning(message)

    def _seed_default_roles(self, cursor):

        now = datetime.now().isoformat()

        for role_name, permissions in DEFAULT_ROLES.items():

            cursor.execute(
                "INSERT INTO roles (name, description, is_system, "
                "created_date) VALUES (?,?,?,?)",
                (role_name, f"Built-in {role_name} role", 1, now),
            )

            role_id = cursor.lastrowid

            for resource, actions in permissions.items():

                for action in actions:

                    cursor.execute(
                        "INSERT OR IGNORE INTO role_permissions "
                        "(role_id, resource, action) VALUES (?,?,?)",
                        (role_id, resource, action),
                    )

    def _seed_default_admin(self, cursor):

        from Core.security import hash_password, generate_temp_password

        cursor.execute("SELECT id FROM roles WHERE name='Admin'")

        row = cursor.fetchone()

        admin_role_id = row[0] if row else None

        temp_password = generate_temp_password()

        password_hash, password_salt = hash_password(temp_password)

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO users
            (username, email, full_name, password_hash, password_salt,
             role_id, is_active, must_change_password, created_date,
             modified_date)
            VALUES (?,?,?,?,?,?,1,1,?,?)
            """,
            (
                "admin",
                "",
                "Administrator",
                password_hash,
                password_salt,
                admin_role_id,
                now,
                now,
            ),
        )

        return temp_password

    # --------------------------------------------------
    # Users
    # --------------------------------------------------

    def get_user_by_username(self, username):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT u.*, r.name AS role_name
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE u.username = ?
            """,
            (username,),
        )

        row = cursor.fetchone()

        conn.close()

        return row

    def get_user_by_id(self, user_id):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT u.*, r.name AS role_name
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE u.id = ?
            """,
            (user_id,),
        )

        row = cursor.fetchone()

        conn.close()

        return row

    def list_users(self):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT u.id, u.username, u.email, u.full_name, u.is_active,
                   u.must_change_password, u.created_date,
                   u.last_login_date, r.id AS role_id, r.name AS role_name
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            ORDER BY u.username
            """
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def create_user(self, username, email, full_name, password, role_id):

        from Core.security import hash_password

        password_hash, password_salt = hash_password(password)

        now = datetime.now().isoformat()

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO users
            (username, email, full_name, password_hash, password_salt,
             role_id, is_active, must_change_password, created_date,
             modified_date)
            VALUES (?,?,?,?,?,?,1,1,?,?)
            """,
            (
                username, email, full_name, password_hash,
                password_salt, role_id, now, now,
            ),
        )

        new_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return new_id

    def set_active(self, user_id, is_active):

        self._update_field(user_id, "is_active", 1 if is_active else 0)

    def update_role(self, user_id, role_id):

        self._update_field(user_id, "role_id", role_id)

    def set_password(self, user_id, password, must_change_password=False):

        from Core.security import hash_password

        password_hash, password_salt = hash_password(password)

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE users
            SET password_hash=?, password_salt=?, must_change_password=?,
                modified_date=?
            WHERE id=?
            """,
            (
                password_hash, password_salt,
                1 if must_change_password else 0, now, user_id,
            ),
        )

        conn.commit()

        conn.close()

    def record_login(self, user_id):

        self._update_field(
            user_id, "last_login_date", datetime.now().isoformat()
        )

    def _update_field(self, user_id, field, value):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            f"UPDATE users SET {field}=?, modified_date=? WHERE id=?",
            (value, now, user_id),
        )

        conn.commit()

        conn.close()

    # --------------------------------------------------
    # Roles / Permissions
    # --------------------------------------------------

    def list_roles(self):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, name, description, is_system FROM roles "
            "ORDER BY name"
        )

        roles = cursor.fetchall()

        for role in roles:

            cursor.execute(
                "SELECT resource, action FROM role_permissions "
                "WHERE role_id=?",
                (role["id"],),
            )

            role["permissions"] = [
                {"resource": r, "action": a} for r, a in cursor.fetchall()
            ]

        conn.close()

        return roles

    def get_permissions_for_role(self, role_id):
        """Returns {resource: set(actions)} for fast in-request checks."""

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT resource, action FROM role_permissions WHERE role_id=?",
            (role_id,),
        )

        permissions = {}

        for resource, action in cursor.fetchall():

            permissions.setdefault(resource, set()).add(action)

        conn.close()

        return permissions

    def create_role(self, name, description=""):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO roles (name, description, is_system, "
            "created_date) VALUES (?,?,0,?)",
            (name, description, datetime.now().isoformat()),
        )

        new_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return new_id

    def set_role_permissions(self, role_id, permissions):
        """
        `permissions`: list of {"resource": ..., "action": ...} dicts —
        replaces the role's entire permission set atomically so the UI
        can just send "here is the full checked list" every time.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM role_permissions WHERE role_id=?", (role_id,)
        )

        for entry in permissions:

            cursor.execute(
                "INSERT OR IGNORE INTO role_permissions "
                "(role_id, resource, action) VALUES (?,?,?)",
                (role_id, entry["resource"], entry["action"]),
            )

        conn.commit()

        conn.close()

    # --------------------------------------------------
    # Audit Log
    # --------------------------------------------------

    def write_audit_log(
        self, user_id, username, action, resource=None,
        detail=None, ip_address=None,
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO audit_logs
            (user_id, username, action, resource, detail, ip_address,
             created_date)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                user_id, username, action, resource, detail,
                ip_address, datetime.now().isoformat(),
            ),
        )

        conn.commit()

        conn.close()

    def list_audit_logs(self, limit=200):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    @staticmethod
    def _dict_factory(cursor, row):

        columns = [col[0] for col in cursor.description]

        return {columns[i]: row[i] for i in range(len(columns))}