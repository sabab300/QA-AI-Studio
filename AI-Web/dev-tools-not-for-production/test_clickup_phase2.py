"""Focused offline tests for ClickUp Phase 2. No external task is created."""

import sys
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Core.test_case_publishing import PublishingRepository, PublishingService
from Core.test_case_workbook import DEFAULT_CLICKUP_DESCRIPTION_TEMPLATE, TEMPLATE_TOKEN_ALIASES, normalize_template_tokens


class FakeRepository:
    def __init__(self):
        self.items = []
        self.config = {
            "id": 7, "target": "CLICKUP", "settings": {
                "list_id": "44",
                "destination_fields": [
                    {"id": "name", "name": "Task Name", "type": "text", "scope": "standard", "required": True},
                    {"id": "severity", "name": "QA Bug Severity", "type": "drop_down", "scope": "custom", "required": True, "options": [{"id": "Critical", "name": "Critical"}, {"id": "Low", "name": "Low"}]},
                ],
            },
            "mappings": {
                "name": {"type": "DIRECT", "source": "scenario"},
                "description": {"type": "TEMPLATE", "template": "{{tc_number}}: {{steps}}"},
                "severity": {"type": "VALUE_MAP", "source": "importance", "value_map": {"high": "Critical"}},
            },
        }

    def get_config(self, config_id, include_secret=False):
        return self.config

    def duplicate_for(self, stable_id, target, config_id):
        return {"external_id": "CU-1"} if stable_id == "TC002" else None

    def create_run(self, *_args): return 1, "run-1"
    def add_item(self, *args, **kwargs): self.items.append((args, kwargs))
    def finish_run(self, _run_id, success, failed, skipped): return "PARTIAL" if failed and success else "COMPLETED"


class TempDatabase:
    def __init__(self, path): self.path = path
    def get_connection(self): return sqlite3.connect(self.path)


class ClickUpPhase2Tests(unittest.TestCase):
    def service(self):
        service = PublishingService.__new__(PublishingService)
        service.repository = FakeRepository()
        return service

    def test_discovery_and_metadata_are_normalized(self):
        service = self.service()
        self.assertEqual(service._normalize_clickup_discovery("workspaces", {"teams": [{"id": 1, "name": "PSW"}]})["items"], [{"id": "1", "name": "PSW"}])
        metadata = service._normalize_metadata(
            {"id": "44", "name": "Bugs", "statuses": [{"status": "open", "type": "open"}]},
            {"fields": [{"id": "cf1", "name": "Category", "type": "drop_down", "required": True, "type_config": {"options": [{"id": "a", "name": "Web"}]}}]},
        )
        self.assertEqual(metadata["list"]["name"], "Bugs")
        self.assertEqual(metadata["fields"][-1]["options"][0]["name"], "Web")
        self.assertEqual([item["name"] for item in metadata["priorities"]], ["Urgent", "High", "Normal", "Low"])
        standard = [field for field in metadata["fields"] if field["scope"] == "standard"]
        self.assertEqual([field["name"] for field in standard], ["Task Name", "Description", "Status", "Assignee", "Due Date", "Priority", "Tags"])
        self.assertTrue(standard[0]["required"])
        self.assertTrue(all(field["required"] is False for field in standard[1:]))

    def test_members_tags_and_user_custom_fields_use_api_values(self):
        service = self.service()
        metadata = service._normalize_metadata(
            {"id": "44", "name": "Bugs", "statuses": [{"status": "Open"}]},
            {"fields": [{"id": "owner", "name": "Product Owner", "type": "users", "required": True}]},
            {"teams": [{"id": "1", "members": [{"user": {"id": 99, "username": "QA Owner"}}]}]},
            {"tags": [{"name": "regression"}]}, "1",
        )
        owner = next(field for field in metadata["fields"] if field["id"] == "owner")
        self.assertEqual(owner["options"], [{"id": "99", "name": "QA Owner"}])
        self.assertEqual(metadata["tags"], [{"id": "regression", "name": "regression"}])
        self.assertEqual(service._destination_value(owner, "QA Owner"), "99")

    def test_direct_template_value_map_required_and_duplicate_multi_preview(self):
        service = self.service()
        rows = [
            {"tc_number": "TC001", "stable_id": "TC001", "scenario": "Payment fails", "steps": "Submit", "importance": "High"},
            {"tc_number": "TC002", "stable_id": "TC002", "scenario": "", "steps": "Retry", "importance": "Low"},
        ]
        preview = service.preview("CLICKUP", 7, rows)["rows"]
        self.assertEqual(preview[0]["fields"]["name"], "Payment fails")
        self.assertEqual(preview[0]["fields"]["description"], "TC001: Submit")
        self.assertEqual(preview[0]["fields"]["severity"], "Critical")
        self.assertTrue(preview[1]["duplicate"])
        self.assertIn("Task Name is required by ClickUp", preview[1]["validation"])
        self.assertIn("QA Bug Severity is required by ClickUp", preview[1]["validation"])

    def test_mapping_type_validation_uses_canonical_schema(self):
        service = self.service()
        self.assertEqual(service.validate_mapping_spec({"type": "DIRECT", "source": "scenario"}), [])
        self.assertEqual(service.validate_mapping_spec({"type": "DIRECT", "source": "test_case"}), [])
        self.assertEqual(service.validate_mapping_spec({"type": "DIRECT", "source": ""}), ["Select a QA AI Studio field."])
        self.assertEqual(service.validate_mapping_spec({"type": "TEMPLATE", "template": "{{tc_number}}: {{scenario}}"}), [])
        unknown = service.validate_mapping_spec({"type": "TEMPLATE", "template": "{{not_a_canonical_field}}"})
        self.assertIn("Unknown template token(s): not_a_canonical_field", unknown[0])
        self.assertEqual(service.validate_mapping_spec({"type": "VALUE_MAP", "source": "status", "value_map": {"Fail": "open"}}), [])
        self.assertTrue(service.validate_mapping_spec({"type": "VALUE_MAP", "source": "status", "value_map": {}}))

    def test_existing_value_map_json_remains_compatible(self):
        service = self.service()
        legacy = {"type": "VALUE_MAP", "source": "status", "value_map": {"Fail": "OPEN", "Pass": "CLOSED"}}
        self.assertEqual(service._mapped_value({"status": "Fail"}, legacy), "OPEN")
        self.assertEqual(service._mapped_value({"status": "pass"}, legacy), "CLOSED")

    def test_legacy_template_aliases_normalize_and_render(self):
        service = self.service()
        legacy = "{{requirement_scenario}} | {{test_cases}} | {{execution_type}}"
        self.assertEqual(TEMPLATE_TOKEN_ALIASES["requirement_scenario"], "scenario")
        self.assertEqual(normalize_template_tokens(legacy), "{{scenario}} | {{test_case}} | {{execution}}")
        self.assertEqual(service.validate_mapping_spec({"type": "TEMPLATE", "template": legacy}), [])
        rendered = service._mapped_value({"scenario": "Scenario", "test_case": "Details", "execution": "Manual"}, {"type": "TEMPLATE", "template": legacy})
        self.assertEqual(rendered, "Scenario | Details | Manual")
        self.assertEqual(service.validate_mapping_spec({"type": "TEMPLATE", "template": DEFAULT_CLICKUP_DESCRIPTION_TEMPLATE}), [])

    def test_default_mapping_preview_override_precedence(self):
        service = self.service()
        service.repository.config["settings"]["defaults"] = {"severity": "Normal"}
        service.repository.config["mappings"]["severity"] = {"type": "VALUE_MAP", "source": "importance", "value_map": {"High": "Critical"}}
        mapped = service.preview("CLICKUP", 7, [{"tc_number": "TC004", "scenario": "Mapped", "steps": "Run", "importance": "High"}])["rows"][0]
        self.assertEqual(mapped["fields"]["severity"], "Critical")
        overridden = service.preview("CLICKUP", 7, [{"tc_number": "TC004", "scenario": "Mapped", "steps": "Run", "importance": "High", "_publish_fields": {"severity": "Urgent"}}])["rows"][0]
        self.assertEqual(overridden["fields"]["severity"], "Urgent")
        self.assertEqual(service.repository.config["mappings"]["severity"]["type"], "VALUE_MAP")

    def test_value_map_missing_source_value_is_a_field_error(self):
        service = self.service()
        result = service.preview("CLICKUP", 7, [{"tc_number": "TC006", "scenario": "Mapped", "steps": "Run", "importance": "Unknown"}])["rows"][0]
        self.assertIn('QA Bug Severity: No VALUE MAP entry exists for source value "Unknown".', result["validation"])

    def test_destination_defaults_store_api_compatible_values(self):
        service = self.service()
        status = {"id": "status", "options": [{"id": "open", "name": "Open"}]}
        assignees = {"id": "assignees", "multiple": True, "options": [{"id": "99", "name": "QA Owner"}]}
        priority = {"id": "priority", "options": [{"id": "1", "name": "Urgent"}, {"id": "4", "name": "Low"}]}
        tags = {"id": "tags", "multiple": True, "options": [{"id": "regression", "name": "regression"}]}
        self.assertEqual(service._destination_value(status, "Open"), "open")
        self.assertEqual(service._destination_value(assignees, ["QA Owner"]), ["99"])
        self.assertEqual(service._destination_value(priority, "Low"), "4")
        self.assertEqual(service._destination_value(tags, ["regression"]), ["regression"])

    def test_required_configuration_gate_and_future_payload_contract(self):
        service = self.service()
        incomplete = dict(service.repository.config)
        incomplete["mappings"] = {"name": {"type": "DIRECT", "source": "scenario"}}
        self.assertEqual(service.configuration_readiness(incomplete), {"ready": False, "missing_required": ["QA Bug Severity"]})
        service.repository.config = incomplete
        with self.assertRaisesRegex(ValueError, "Missing required mappings: QA Bug Severity"):
            service.preview("CLICKUP", 7, [{"tc_number": "TC001", "scenario": "Failure"}])
        fields = {"name": "Failure", "priority": "2", "severity": "critical"}
        destination_fields = [
            {"id": "name", "scope": "standard"},
            {"id": "priority", "scope": "standard"},
            {"id": "severity", "scope": "custom"},
        ]
        payload = service.build_clickup_payload(fields, destination_fields)
        self.assertEqual(payload["name"], "Failure")
        self.assertEqual(payload["priority"], 2)
        self.assertEqual(payload["custom_fields"], [{"id": "severity", "value": "critical"}])
        self.assertTrue(payload["check_required_custom_fields"])
        self.assertNotIn("severity", {key: value for key, value in payload.items() if key != "custom_fields"})

    def test_final_wire_payload_converts_status_assignees_people_and_dropdowns(self):
        fields = {"name": "Bug", "status": "status-open-id", "assignees": ["89565972"], "priority": "3", "owner": "89565972", "category": "CRF - Module", "milestone_only": "89565972"}
        destination_fields = [
            {"id": "name", "scope": "standard", "type": "text"},
            {"id": "status", "scope": "standard", "type": "status", "options": [{"id": "status-open-id", "name": "Open"}]},
            {"id": "assignees", "scope": "standard", "type": "users"},
            {"id": "priority", "scope": "standard", "type": "priority"},
            {"id": "owner", "name": "Product Owner", "scope": "custom", "type": "users"},
            {"id": "category", "name": "Category", "scope": "custom", "type": "drop_down", "options": [{"id": "category-id", "name": "CRF - Module"}]},
            {"id": "milestone_only", "name": "Code Reviwer", "scope": "custom", "type": "users", "applied_objects": [{"object_type": 19, "object_id": 1}]},
        ]
        normalized = dict(fields); normalized["category"] = PublishingService._destination_value(destination_fields[5], normalized["category"])
        payload = PublishingService.build_clickup_payload(normalized, destination_fields)
        self.assertEqual(payload["status"], "Open")
        self.assertEqual(payload["assignees"], [89565972])
        self.assertEqual(payload["priority"], 3)
        custom = {item["id"]: item["value"] for item in payload["custom_fields"]}
        self.assertEqual(custom["owner"], {"add": ["89565972"], "rem": []})
        self.assertEqual(custom["category"], "category-id")
        self.assertNotIn("milestone_only", custom)
        self.assertEqual(PublishingService.validate_clickup_payload(payload, destination_fields), [])

    def test_final_payload_validation_rejects_old_wire_representations(self):
        destination_fields = [
            {"id": "name", "scope": "standard", "type": "text"},
            {"id": "status", "scope": "standard", "type": "status", "options": [{"id": "status-id", "name": "Open"}]},
            {"id": "owner", "name": "Product Owner", "scope": "custom", "type": "users"},
            {"id": "category", "name": "Category", "scope": "custom", "type": "drop_down", "options": [{"id": "category-id", "name": "CRF - Module"}]},
        ]
        errors = PublishingService.validate_clickup_payload({"name": "Bug", "status": "status-id", "assignees": ["89565972"], "custom_fields": [{"id": "owner", "value": "89565972"}, {"id": "category", "value": "CRF - Module"}]}, destination_fields)
        self.assertIn("Status must use a current ClickUp status name.", errors)
        self.assertIn("Assignee must use ClickUp numeric user IDs.", errors)
        self.assertTrue(any("Product Owner" in error for error in errors))
        self.assertTrue(any("Category" in error for error in errors))

    def test_http_400_body_is_sanitized_and_returned(self):
        service = self.service(); secret = "never-print-this-token"
        body = json.dumps({"ECODE": "FIELD_016", "err": f"Invalid Product Owner; token={secret}"}).encode()
        response = urllib.error.HTTPError("https://api.clickup.com/api/v2/list/44/task", 400, "Bad Request", {}, io.BytesIO(body))
        with patch("urllib.request.urlopen", side_effect=response):
            with self.assertRaises(ValueError) as raised:
                service._request_values({"base_url": "https://api.clickup.com/api/v2", "secret": secret}, "POST", "/list/44/task", {"name": "Bug"})
        message = str(raised.exception)
        self.assertIn("HTTP 400", message)
        self.assertIn("Code: FIELD_016", message)
        self.assertIn("Invalid Product Owner", message)
        self.assertNotIn(secret, message)

    def test_frontend_uses_structured_mapping_controls(self):
        html = (Path(__file__).resolve().parents[1] / "Frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("data-value-pair", html)
        self.assertIn("data-token-insert", html)
        self.assertIn("STANDARD TASK FIELDS", html)
        self.assertIn("CLICKUP FIELDS — ", html)
        self.assertIn("standard:", html)
        self.assertIn("custom:", html)
        self.assertIn("standardOrder=['name','description','status','assignees','due_date','priority','tags']", html)
        self.assertIn("field.scope==='custom';}).sort", html)
        self.assertIn("integrationDiscover('metadata',listId)", html)
        self.assertIn("CLICKUP FIELDS — ", html)
        self.assertNotIn("integrationMappingAdd", html)
        self.assertNotIn("data-field-kind", html)
        self.assertIn("Configuration: Incomplete", html)
        self.assertIn("Select a QA AI Studio field.", html)
        self.assertIn("data-preview-show-more", html)
        self.assertIn("data-map-show-more", html)
        self.assertIn("ClickUp Value</label>", html)
        self.assertIn("Source Test Case Field", html)
        self.assertIn("QA AI Studio Value", html)
        self.assertIn("Create Bug in ClickUp", html)
        self.assertIn("validate_destination:tcp.target==='CLICKUP'", html)
        self.assertIn(">Required</th>", html)
        self.assertNotIn("QA AI Studio Required", html)
        self.assertNotIn('id="integrationStatus"', html)
        self.assertNotIn('id="integrationPriority"', html)
        self.assertNotIn('id="integrationAssignee"', html)
        self.assertNotIn('id="integrationTags"', html)
        self.assertNotIn('id="integrationDueDateRule"', html)
        self.assertNotIn('id="integrationDueDateOffset"', html)
        self.assertNotIn("Value Map must be a JSON object.", html)

    def test_local_required_flag_is_ignored(self):
        service = self.service()
        service.repository.config["settings"]["destination_fields"].append({"id": "release", "name": "Release", "required": None, "options": []})
        service.repository.config["mappings"]["release"] = {"type": "DIRECT", "source": "comments", "required": True}
        result = service.preview("CLICKUP", 7, [{"tc_number": "TC005", "scenario": "S", "steps": "Run", "importance": "High", "comments": ""}])["rows"][0]
        self.assertNotIn("release is required by QA AI Studio", result["validation"])

    def test_transient_discovery_uses_secret_only_server_side(self):
        service = self.service()
        service.repository.get_config = lambda *_args, **_kwargs: {"base_url": "https://api.clickup.com/api/v2", "secret": "stored-token"}
        with patch.object(service, "_request_values", return_value={"teams": [{"id": "1", "name": "PSW"}]}) as request:
            result = service.discover_clickup_values({"configuration_id": 7}, "workspaces")
        self.assertEqual(result["items"][0]["name"], "PSW")
        self.assertEqual(request.call_args.args[0]["secret"], "stored-token")

    def test_mapping_and_destination_names_persist_while_blank_secret_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = PublishingRepository(TempDatabase(Path(folder) / "publishing.db"))
            saved = repository.save_config({"target": "CLICKUP", "name": "Bugs", "base_url": "https://api.clickup.com/api/v2", "secret": "private", "settings": {"workspace_id": "1", "workspace_name": "PSW", "list_id": "44", "list_name": "Bugs Tracking"}, "mappings": {"name": {"type": "DIRECT", "source": "scenario"}}})
            repository.save_config({"target": "CLICKUP", "name": "Bugs", "base_url": "https://api.clickup.com/api/v2", "secret": "", "settings": saved["settings"], "mappings": saved["mappings"]}, saved["id"])
            loaded = repository.get_config(saved["id"], include_secret=True)
            self.assertEqual(loaded["secret"], "private")
            self.assertEqual(loaded["settings"]["list_name"], "Bugs Tracking")
            self.assertEqual(loaded["mappings"]["name"]["type"], "DIRECT")

    def test_mocked_multi_publish_keeps_success_and_validation_failure_independent(self):
        service = self.service()
        rows = [
            {"id": 1, "tc_number": "TC001", "scenario": "Valid", "steps": "Submit", "importance": "High"},
            {"id": 3, "tc_number": "TC003", "scenario": "", "steps": "Retry", "importance": "Low"},
        ]
        live = {"fields": service.repository.config["settings"]["destination_fields"]}
        with patch.object(service, "discover_clickup_values", return_value=live), patch.object(service, "_request", return_value={"id": "CU-9", "custom_id": "BUG-9", "url": "https://app.clickup.com/t/CU-9"}):
            result = service.publish("CLICKUP", 7, 1, 1, rows, "tester")
        self.assertEqual((result["success"], result["failed"], result["skipped"]), (1, 1, 0))
        self.assertEqual([item["status"] for item in result["items"]], ["PUBLISHED", "FAILED"])
        self.assertEqual(result["items"][0]["external_custom_id"], "BUG-9")

    def test_publication_history_persists_custom_task_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = PublishingRepository(TempDatabase(Path(folder) / "publishing.db"))
            config = repository.save_config({"target": "CLICKUP", "name": "Bugs", "base_url": "https://api.clickup.com/api/v2", "secret": "private", "settings": {}, "mappings": {}})
            run_id, _run_uuid = repository.create_run("CLICKUP", config["id"], 1, 1, 1, "tester")
            repository.add_item(run_id, {"id": 1, "tc_number": "TC001"}, "CLICKUP", config["id"], {"name": "Bug"}, "PUBLISHED", "cu-1", "https://app.clickup.com/t/cu-1", external_custom_id="BUG-1")
            repository.finish_run(run_id, 1, 0, 0)
            item = repository.history()[0]["items"][0]
            self.assertEqual((item["external_id"], item["external_custom_id"], item["status"]), ("cu-1", "BUG-1", "PUBLISHED"))

    def test_latest_clickup_publication_restores_workspace_status_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = PublishingRepository(TempDatabase(Path(folder) / "publishing.db"))
            config = repository.save_config({"target": "CLICKUP", "name": "Bugs", "base_url": "https://api.clickup.com/api/v2", "secret": "private", "settings": {}, "mappings": {}})
            run_id, _run_uuid = repository.create_run("CLICKUP", config["id"], 1, 1, 1, "tester")
            repository.add_item(run_id, {"id": 1, "tc_number": "TC001"}, "CLICKUP", config["id"], {"name": "Bug"}, "PUBLISHED", "cu-1", "https://app.clickup.com/t/cu-1", external_custom_id="BUG-1")
            restored = repository.latest_clickup_publications(["TC001", "TC999"])
            self.assertEqual((restored["TC001"]["external_id"], restored["TC001"]["external_custom_id"]), ("cu-1", "BUG-1"))
            self.assertNotIn("TC999", restored)

    def test_mocked_create_task_partial_api_failure_does_not_rollback_success(self):
        service = self.service()
        rows = [
            {"id": 1, "tc_number": "TC010", "scenario": "First", "steps": "Run", "importance": "High"},
            {"id": 2, "tc_number": "TC011", "scenario": "Second", "steps": "Run", "importance": "High"},
        ]
        live = {"fields": service.repository.config["settings"]["destination_fields"]}
        responses = iter([{"id": "CU-10", "url": "https://app.clickup.com/t/CU-10"}, ValueError("ClickUp rate limit reached. Wait and try again.")])
        def create(_config, _method, _path, _payload=None):
            result = next(responses)
            if isinstance(result, Exception): raise result
            return result
        with patch.object(service, "discover_clickup_values", return_value=live), patch.object(service, "_request", side_effect=create):
            result = service.publish("CLICKUP", 7, 1, 1, rows, "tester")
        self.assertEqual((result["success"], result["failed"], result["status"]), (1, 1, "PARTIAL"))
        self.assertEqual([item["status"] for item in result["items"]], ["PUBLISHED", "FAILED"])
        self.assertEqual([entry[0][5] for entry in service.repository.items], ["PUBLISHED", "FAILED"])
        self.assertEqual(len(service.repository.items[1][0]), 6)


if __name__ == "__main__":
    unittest.main()
