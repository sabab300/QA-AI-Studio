import unittest
import sqlite3
import tempfile
from pathlib import Path

from Core.discovery_repository import DiscoveryRepository
from Core.url_discovery_engine import URLDiscoveryEngine, build_xpath_alternative


class _Page:
    def __init__(self, closed=False):
        self._closed = closed

    def is_closed(self):
        return self._closed


class _Match:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _Frame:
    def __init__(self, count):
        self._count = count

    def locator(self, _selector):
        return _Match(self._count)


class URLDiscoveryEngineTests(unittest.TestCase):
    def test_name_only_xpath_is_available(self):
        self.assertEqual(
            build_xpath_alternative("input", {"name": "customer_name"}),
            "//input[@name='customer_name']",
        )

    def test_latest_open_context_page_wins(self):
        original = _Page()
        popup = _Page()
        context = type("Context", (), {"pages": [original, popup]})()
        engine = URLDiscoveryEngine(context=context, page=original)
        self.assertIs(engine._get_active_page(), popup)

    def test_locator_quality_uses_live_match_count(self):
        self.assertEqual(
            URLDiscoveryEngine._locator_validation(_Frame(1), "#save", "id"),
            {
                "locator_match_count": 1,
                "locator_validation": "unique",
                "locator_quality": "stable",
            },
        )
        self.assertEqual(
            URLDiscoveryEngine._locator_validation(_Frame(2), "button", "tag-only")[
                "locator_quality"
            ],
            "weak",
        )

    def test_workflow_step_resave_updates_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "discovery.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """CREATE TABLE discovery_workflow_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    variant_id INTEGER, step_order INTEGER, page_id INTEGER,
                    tab_id INTEGER, step_name TEXT, depends_on_step_id INTEGER,
                    is_end_step INTEGER, created_date TEXT, modified_date TEXT
                )"""
            )
            connection.commit()
            connection.close()

            repository = DiscoveryRepository.__new__(DiscoveryRepository)
            repository.db = type(
                "Database", (), {"get_connection": lambda _self: sqlite3.connect(database_path)}
            )()

            first = repository._add_workflow_step(1, 1, 10, 20, "Original", None, False)
            second = repository._add_workflow_step(1, 1, 11, 21, "Updated", None, True)
            repository._add_workflow_step(1, 2, 12, 22, "Removed", first, False)
            repository._trim_workflow_steps(1, 1)

            connection = sqlite3.connect(database_path)
            rows = connection.execute(
                "SELECT id, page_id, step_name, is_end_step FROM discovery_workflow_steps"
            ).fetchall()
            connection.close()
            self.assertEqual(first, second)
            self.assertEqual(rows, [(first, 11, "Updated", 1)])


if __name__ == "__main__":
    unittest.main()
