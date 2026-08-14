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
# Paths Initialization
# --------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

# Make project root importable (Core, Config, Database...)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Make App package importable (UI...)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Top-level AI package resolution
TOP_LEVEL_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(TOP_LEVEL_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOP_LEVEL_PROJECT_ROOT))

AI_DIR = Path(__file__).resolve().parent.parent
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

# --------------------------------------------------
# App Imports
# --------------------------------------------------
from Database.db_manager import DatabaseManager
from UI.Main.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    """Loads and applies the main QSS stylesheet if present."""
    style_file = APP_DIR / "UI" / "Resources" / "style.qss"

    if style_file.exists():
        file = QFile(str(style_file))
        if file.open(QFile.ReadOnly | QFile.Text):
            stream = QTextStream(file)
            app.setStyleSheet(stream.readAll())
            file.close()


def main() -> None:
    app = QApplication(sys.argv)

    app.setApplicationName("QA AI Studio")
    app.setOrganizationName("Pakistan Single Window")
    app.setApplicationVersion("1.0")

    # Initialize SQLite/PostgreSQL Database Schema
    DatabaseManager().initialize_database()

    # Load UI Styling
    load_stylesheet(app)

    # Launch Desktop Application Main Window
    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()