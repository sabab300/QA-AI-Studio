"""
QA AI Studio
Application Entry Point

Version: 1.1
"""

import sys
from pathlib import Path

from PySide6.QtCore import QFile, QTextStream
from PySide6.QtWidgets import QApplication

# --------------------------------------------------
# Paths
# --------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

# Make project importable (Core, Config, Database...)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Make App package importable (UI...)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Set project root (QA AI Agent directory containing the top-level 'AI' package)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Also ensure the inner directory is included if required
AI_DIR = Path(__file__).resolve().parent.parent
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

# Existing imports follow below...
from UI.Main.main_window import MainWindow

# --------------------------------------------------

from UI.Main.main_window import MainWindow
from Database.db_manager import DatabaseManager


def load_stylesheet(app):

    style_file = APP_DIR / "UI" / "Resources" / "style.qss"

    if style_file.exists():

        file = QFile(str(style_file))

        if file.open(QFile.ReadOnly | QFile.Text):

            stream = QTextStream(file)

            app.setStyleSheet(stream.readAll())

            file.close()


def main():

    app = QApplication(sys.argv)

    app.setApplicationName("QA AI Studio")
    app.setOrganizationName("Pakistan Single Window")
    app.setApplicationVersion("1.0")

    # Critical fix (Phase 1 consistency pass): this was never being
    # called anywhere before. The app only worked because
    # Database/metadata.db already had tables in it from an older,
    # since-removed code path — a fresh install or a deleted DB file
    # would have crashed immediately. This makes schema creation
    # part of every actual app startup, and is safe to run every
    # time (only creates what's missing).
    DatabaseManager().initialize_database()

    load_stylesheet(app)

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()