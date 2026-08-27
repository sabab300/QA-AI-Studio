"""
QA AI Studio — reset the built-in Admin role back to full access.

What this fixes
----------------
If the Admin role's permissions get edited down (for example, while testing
the permissions popup) it can end up with only a handful of the 35
resource/action combinations it should always have — which then locks the
Admin account out of screens it needs, including the Users & Access screen
that would normally be used to fix this.

This script talks to the database directly, so it works even when the app
itself can't be used to fix it.

How to use it
--------------
1. Stop the backend first (Ctrl+C the `uvicorn` window), so nothing else is
   writing to the database at the same time.
2. Put this file in the same `AI` folder as your `Database` folder (i.e.
   next to `Database/metadata.db`), or just run it from that folder.
3. Run:
       python reset_admin_permissions.py
4. Restart the backend as usual.
5. In the browser, log out and log back in as Admin (or just refresh the
   page after logging in again) — the frontend caches your permissions
   from login, so it won't see the fix until it re-fetches them.

It's safe to run more than once. It only adds whatever permissions are
missing for the Admin role — it never touches any other role, any user, or
any other table.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Database", "metadata.db")

RESOURCES = ["knowledge", "qa_engineering", "automation", "ai_assistant", "dashboard", "settings", "users"]
ACTIONS = ["view", "create", "edit", "delete", "execute"]


def main():
    if not os.path.exists(DB_PATH):
        print("Could not find Database/metadata.db next to this script.")
        print("Move this file into your AI folder (the one containing the Database folder) and run it again.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    row = cur.execute("SELECT id FROM roles WHERE name = 'Admin' AND is_system = 1").fetchone()
    if not row:
        print("Could not find the built-in Admin role (name='Admin', is_system=1). Nothing changed.")
        conn.close()
        return
    admin_role_id = row[0]

    added = 0
    for resource in RESOURCES:
        for action in ACTIONS:
            exists = cur.execute(
                "SELECT 1 FROM role_permissions WHERE role_id = ? AND resource = ? AND action = ?",
                (admin_role_id, resource, action),
            ).fetchone()
            if not exists:
                cur.execute(
                    "INSERT INTO role_permissions (role_id, resource, action) VALUES (?, ?, ?)",
                    (admin_role_id, resource, action),
                )
                added += 1

    conn.commit()
    total = cur.execute(
        "SELECT COUNT(*) FROM role_permissions WHERE role_id = ?", (admin_role_id,)
    ).fetchone()[0]
    conn.close()

    print(f"Admin role (id={admin_role_id}): added {added} missing permission(s).")
    print(f"Admin now has {total} / 35 permissions.")
    if total == 35:
        print("Full access restored. Log out and log back in (or refresh after logging in) to see it take effect.")


if __name__ == "__main__":
    main()