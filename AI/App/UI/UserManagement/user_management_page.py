"""
QA AI Studio — Desktop User Management

Uses Core.user_repository.UserRepository, the same persisted users / roles /
permissions model used by the Web application. No Desktop-only user store.
"""

import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from Core.security import generate_temp_password
from Core.user_repository import ACTIONS, RESOURCES, UserRepository


class _UserDialog(QDialog):
    def __init__(self, repository, user=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.user = user or {}
        self.setWindowTitle("Edit User" if user else "Add User")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.username = QLineEdit(self.user.get("username", ""))
        self.full_name = QLineEdit(self.user.get("full_name", ""))
        self.email = QLineEdit(self.user.get("email", ""))
        self.contact = QLineEdit(self.user.get("contact_number", ""))
        self.role = QComboBox()
        self.roles = self.repository.list_roles()
        for role in self.roles:
            self.role.addItem(role["name"], role["id"])
        if user:
            idx = self.role.findData(self.user.get("role_id"))
            if idx >= 0:
                self.role.setCurrentIndex(idx)

        form.addRow("Username", self.username)
        form.addRow("Full name", self.full_name)
        form.addRow("Email", self.email)
        form.addRow("Contact", self.contact)
        form.addRow("Role", self.role)

        self.password_note = QLabel(
            "A temporary password is generated for new users and must be changed "
            "on first login." if not user else "Password is unchanged here."
        )
        self.password_note.setWordWrap(True)
        self.password_note.setObjectName("MutedLabel")
        layout.addWidget(self.password_note)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._validate_and_accept)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        if not self.username.text().strip():
            QMessageBox.warning(self, "Required", "Username is required.")
            return
        if self.role.currentData() is None:
            QMessageBox.warning(self, "Required", "Role is required.")
            return
        self.accept()

    def values(self):
        return {
            "username": self.username.text().strip(),
            "full_name": self.full_name.text().strip(),
            "email": self.email.text().strip(),
            "contact_number": self.contact.text().strip(),
            "role_id": self.role.currentData(),
        }


class _RoleDialog(QDialog):
    def __init__(self, repository, role=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.role = role or {}
        self.setWindowTitle("Edit Role & Permissions" if role else "Add Role")
        self.resize(760, 620)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self.role.get("name", ""))
        self.description = QLineEdit(self.role.get("description", ""))
        form.addRow("Role name", self.name)
        form.addRow("Description", self.description)
        root.addLayout(form)

        hint = QLabel("Grant only the actions this role needs. Permissions are persisted in the shared role_permissions table.")
        hint.setWordWrap(True)
        hint.setObjectName("MutedLabel")
        root.addWidget(hint)

        self.table = QTableWidget(len(RESOURCES), len(ACTIONS) + 1)
        self.table.setHorizontalHeaderLabels(["Resource"] + [a.capitalize() for a in ACTIONS])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(ACTIONS) + 1):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)

        existing = {(p["resource"], p["action"]) for p in self.role.get("permissions", [])}
        self.checks = {}
        for row, resource in enumerate(RESOURCES):
            item = QTableWidgetItem(resource.replace("_", " ").title())
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            for col, action in enumerate(ACTIONS, start=1):
                check = QCheckBox()
                check.setChecked((resource, action) in existing)
                holder = QWidget()
                h = QHBoxLayout(holder)
                h.setContentsMargins(0, 0, 0, 0)
                h.setAlignment(Qt.AlignCenter)
                h.addWidget(check)
                self.table.setCellWidget(row, col, holder)
                self.checks[(resource, action)] = check
        root.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._validate_and_accept)
        root.addWidget(buttons)

    def _validate_and_accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Required", "Role name is required.")
            return
        self.accept()

    def values(self):
        permissions = [
            {"resource": resource, "action": action}
            for (resource, action), checkbox in self.checks.items()
            if checkbox.isChecked()
        ]
        return {
            "name": self.name.text().strip(),
            "description": self.description.text().strip(),
            "permissions": permissions,
        }




class _TemporaryPasswordDialog(QDialog):
    """One-time credential handoff with explicit clipboard action."""
    def __init__(self, username, password, parent=None):
        super().__init__(parent)
        self.password = password
        self.setWindowTitle("User created")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        title = QLabel("User created successfully")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        note = QLabel(f"Username: <b>{username}</b><br>The temporary password is shown once. The user must change it on first login.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.password_box = QLineEdit(password)
        self.password_box.setReadOnly(True)
        self.password_box.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.password_box)
        actions = QHBoxLayout()
        show_btn = QPushButton("Show")
        copy_btn = QPushButton("Copy Password")
        close_btn = QPushButton("Close")
        show_btn.clicked.connect(lambda: self.password_box.setEchoMode(QLineEdit.Normal if self.password_box.echoMode() == QLineEdit.Password else QLineEdit.Password))
        copy_btn.clicked.connect(lambda: self._copy(copy_btn))
        close_btn.clicked.connect(self.accept)
        actions.addWidget(show_btn)
        actions.addWidget(copy_btn)
        actions.addStretch()
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    def _copy(self, button):
        QApplication.clipboard().setText(self.password)
        button.setText("Copied ✓")

class UserManagementPage(QWidget):
    """Real Desktop UI over the shared user/role/permission repository."""

    def __init__(self):
        super().__init__()
        self.repository = UserRepository()
        self._build_ui()
        self.refresh_all()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabstrip = QFrame()
        tabstrip.setObjectName("DesktopTabStrip")
        row = QHBoxLayout(tabstrip)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(18)
        self.users_tab = QPushButton("Users")
        self.roles_tab = QPushButton("Roles & Permissions")
        self.audit_tab = QPushButton("Audit Log")
        self.tab_buttons = [self.users_tab, self.roles_tab, self.audit_tab]
        for button in self.tab_buttons:
            button.setObjectName("DesktopTabButton")
            button.setCheckable(True)
            button.setMinimumHeight(30)
            row.addWidget(button)
        row.addStretch()
        root.addWidget(tabstrip)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_users_page())
        self.pages.addWidget(self._build_roles_page())
        self.pages.addWidget(self._build_audit_page())

        self.users_tab.clicked.connect(lambda: self.show_tab(0))
        self.roles_tab.clicked.connect(lambda: self.show_tab(1))
        self.audit_tab.clicked.connect(lambda: self.show_tab(2))
        self.show_tab(0)

    def _build_users_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        self.user_search = QLineEdit()
        self.user_search.setPlaceholderText("Search users…")
        self.user_search.textChanged.connect(self._filter_users)
        toolbar.addWidget(self.user_search, 1)
        add = QPushButton("Add User")
        refresh = QPushButton("Refresh")
        add.clicked.connect(self.add_user)
        refresh.clicked.connect(self.refresh_users)
        toolbar.addWidget(add)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)

        self.users_table = QTableWidget(0, 8)
        self.users_table.setHorizontalHeaderLabels([
            "Username", "Full Name", "Email", "Contact", "Role", "Status", "Last Login", "Actions"
        ])
        self.users_table.verticalHeader().setVisible(False)
        self.users_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.users_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.users_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.users_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.users_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.users_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        layout.addWidget(self.users_table, 1)
        return page

    def _build_roles_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        add = QPushButton("Add Role")
        refresh = QPushButton("Refresh")
        add.clicked.connect(self.add_role)
        refresh.clicked.connect(self.refresh_roles)
        toolbar.addWidget(add)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)

        self.roles_table = QTableWidget(0, 5)
        self.roles_table.setHorizontalHeaderLabels(["Role", "Description", "Type", "Permissions", "Actions"])
        self.roles_table.verticalHeader().setVisible(False)
        self.roles_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.roles_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.roles_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.roles_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.roles_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self.roles_table, 1)
        return page

    def _build_audit_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        toolbar.addStretch()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_audit)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)
        self.audit_table = QTableWidget(0, 5)
        self.audit_table.setHorizontalHeaderLabels(["Time", "User", "Action", "Resource", "Detail"])
        self.audit_table.verticalHeader().setVisible(False)
        self.audit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.audit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.audit_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.audit_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.audit_table, 1)
        return page

    def show_tab(self, index):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.tab_buttons):
            button.setChecked(i == index)

    @staticmethod
    def _item(value):
        return QTableWidgetItem("" if value is None else str(value))

    @staticmethod
    def _format_date(value):
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)

    def refresh_all(self):
        self.refresh_users()
        self.refresh_roles()
        self.refresh_audit()

    def refresh_users(self):
        self._users = self.repository.list_users()
        self._render_users(self._users)

    def _filter_users(self):
        text = self.user_search.text().strip().lower()
        rows = self._users if not text else [
            user for user in self._users
            if text in " ".join(str(user.get(key, "")) for key in ("username", "full_name", "email", "role_name")).lower()
        ]
        self._render_users(rows)

    def _render_users(self, users):
        self.users_table.setRowCount(0)
        for user in users:
            row = self.users_table.rowCount()
            self.users_table.insertRow(row)
            values = [
                user.get("username"), user.get("full_name"), user.get("email"), user.get("contact_number"),
                user.get("role_name") or "—", "Active" if user.get("is_active") else "Disabled",
                self._format_date(user.get("last_login_date")),
            ]
            for col, value in enumerate(values):
                self.users_table.setItem(row, col, self._item(value))
            holder = QWidget()
            actions = QHBoxLayout(holder)
            actions.setContentsMargins(0, 0, 0, 0)
            edit = QPushButton("Edit")
            toggle = QPushButton("Disable" if user.get("is_active") else "Enable")
            delete = QPushButton("Delete")
            edit.clicked.connect(lambda _=False, u=user: self.edit_user(u))
            toggle.clicked.connect(lambda _=False, u=user: self.toggle_user(u))
            delete.clicked.connect(lambda _=False, u=user: self.delete_user(u))
            actions.addWidget(edit)
            actions.addWidget(toggle)
            actions.addWidget(delete)
            self.users_table.setCellWidget(row, 7, holder)
        self.users_table.resizeRowsToContents()

    def add_user(self):
        dialog = _UserDialog(self.repository, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        password = generate_temp_password()
        try:
            user_id = self.repository.create_user(password=password, **values)
            self.repository.write_audit_log(user_id=None, username="desktop-session", action="create", resource="users", detail=f"Created user {values['username']}")
        except sqlite3.IntegrityError as exc:
            QMessageBox.warning(self, "Could not create user", str(exc))
            return
        self.refresh_users()
        _TemporaryPasswordDialog(values["username"], password, self).exec()

    def edit_user(self, user):
        dialog = _UserDialog(self.repository, user=user, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        try:
            self.repository.update_user_profile(user["id"], **values)
            self.repository.write_audit_log(None, "desktop-session", "edit", "users", f"Updated user {values['username']}")
        except sqlite3.IntegrityError as exc:
            QMessageBox.warning(self, "Could not update user", str(exc))
            return
        self.refresh_users()

    def toggle_user(self, user):
        self.repository.set_active(user["id"], not bool(user.get("is_active")))
        self.repository.write_audit_log(None, "desktop-session", "edit", "users", f"{'Enabled' if not user.get('is_active') else 'Disabled'} user {user.get('username')}")
        self.refresh_users()

    def delete_user(self, user):
        answer = QMessageBox.question(self, "Delete User", f"Delete user '{user.get('username')}'? This is a soft delete and preserves audit history.")
        if answer != QMessageBox.Yes:
            return
        admin = self.repository.get_role_by_name("Admin")
        if admin and user.get("role_id") == admin.get("id") and self.repository.count_active_admins(admin["id"], exclude_user_id=user["id"]) < 1:
            QMessageBox.warning(self, "Not allowed", "The last active Admin user cannot be deleted.")
            return
        self.repository.soft_delete_user(user["id"])
        self.repository.write_audit_log(None, "desktop-session", "delete", "users", f"Deleted user {user.get('username')}")
        self.refresh_users()

    def refresh_roles(self):
        roles = self.repository.list_roles()
        self.roles_table.setRowCount(0)
        for role in roles:
            row = self.roles_table.rowCount()
            self.roles_table.insertRow(row)
            summary = self.repository.summarize_permissions(role.get("permissions", []))
            values = [role.get("name"), role.get("description"), "System" if role.get("is_system") else "Custom", summary]
            for col, value in enumerate(values):
                self.roles_table.setItem(row, col, self._item(value))
            holder = QWidget()
            actions = QHBoxLayout(holder)
            actions.setContentsMargins(0, 0, 0, 0)
            edit = QPushButton("Edit")
            delete = QPushButton("Delete")
            edit.clicked.connect(lambda _=False, r=role: self.edit_role(r))
            delete.clicked.connect(lambda _=False, r=role: self.delete_role(r))
            actions.addWidget(edit)
            actions.addWidget(delete)
            self.roles_table.setCellWidget(row, 4, holder)
        self.roles_table.resizeRowsToContents()

    def add_role(self):
        dialog = _RoleDialog(self.repository, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        try:
            role_id = self.repository.create_role(values["name"], values["description"])
            self.repository.set_role_permissions(role_id, values["permissions"])
            self.repository.write_audit_log(None, "desktop-session", "create", "users", f"Created role {values['name']}: {self.repository.summarize_permissions(values['permissions'])}")
        except sqlite3.IntegrityError as exc:
            QMessageBox.warning(self, "Could not create role", str(exc))
            return
        self.refresh_roles()

    def edit_role(self, role):
        # System role names/descriptions remain untouched; permissions can still be administered.
        dialog = _RoleDialog(self.repository, role=role, parent=self)
        # The shared repository currently exposes permission updates but not
        # role-name/description updates. Keep those fields read-only rather than
        # pretending an edit will persist.
        dialog.name.setEnabled(False)
        dialog.description.setEnabled(False)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        self.repository.set_role_permissions(role["id"], values["permissions"])
        self.repository.write_audit_log(None, "desktop-session", "edit", "users", f"Updated permissions for role {role.get('name')}: {self.repository.summarize_permissions(values['permissions'])}")
        self.refresh_roles()

    def delete_role(self, role):
        if role.get("is_system"):
            QMessageBox.warning(self, "Not allowed", "Built-in system roles cannot be deleted.")
            return
        users = self.repository.count_users_with_role(role["id"])
        if users:
            QMessageBox.warning(self, "Role in use", f"This role is assigned to {users} user(s). Reassign those users first.")
            return
        answer = QMessageBox.question(self, "Delete Role", f"Delete role '{role.get('name')}'?")
        if answer != QMessageBox.Yes:
            return
        self.repository.soft_delete_role(role["id"])
        self.repository.write_audit_log(None, "desktop-session", "delete", "users", f"Deleted role {role.get('name')}")
        self.refresh_roles()

    def refresh_audit(self):
        rows = self.repository.list_audit_logs(limit=300)
        self.audit_table.setRowCount(0)
        for entry in rows:
            row = self.audit_table.rowCount()
            self.audit_table.insertRow(row)
            values = [
                self._format_date(entry.get("created_date")), entry.get("username") or "—",
                entry.get("action") or "—", entry.get("resource") or "—", entry.get("detail") or "—",
            ]
            for col, value in enumerate(values):
                self.audit_table.setItem(row, col, self._item(value))
        self.audit_table.resizeRowsToContents()
