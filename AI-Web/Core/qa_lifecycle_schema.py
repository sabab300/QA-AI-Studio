"""Additive version 1. Caller executes each statement in one transaction."""
MIGRATION_1=[
 'CREATE TABLE IF NOT EXISTS qa_lifecycle_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)',
 '''CREATE TABLE qa_requirements(id TEXT PRIMARY KEY,logical_key TEXT NOT NULL,source_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE RESTRICT,
 document_version_id INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE RESTRICT,source_reference TEXT NOT NULL,description TEXT NOT NULL,
 anchor INTEGER NOT NULL,quote_hash TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('Needs Review','Confirmed','Retired')),
 created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(source_id,anchor,quote_hash))''',
 '''CREATE TABLE qa_requirement_links(requirement_id TEXT NOT NULL REFERENCES qa_requirements(id) ON DELETE RESTRICT,
 test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,status TEXT NOT NULL CHECK(status IN ('PROPOSED','CONFIRMED','REJECTED')),
 rationale TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),updated_at TEXT NOT NULL,PRIMARY KEY(requirement_id,test_case_id))''',
 '''CREATE TABLE qa_review_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
 state TEXT NOT NULL CHECK(state IN ('Draft','In Review','Approved','Ready for Execution','Retired')),content_hash TEXT NOT NULL,case_snapshot_json TEXT NOT NULL,
 reviewer_id INTEGER NOT NULL REFERENCES users(id),comments TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_asset_links(id TEXT PRIMARY KEY,test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('Manual','Playwright','API','SQL')),asset_reference TEXT NOT NULL,asset_version TEXT NOT NULL,
 validation_state TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 sql_script_id INTEGER REFERENCES sql_scripts(id) ON DELETE RESTRICT,playwright_revision_id INTEGER REFERENCES playwright_script_revisions(id) ON DELETE RESTRICT,
 UNIQUE(test_case_id,kind,asset_reference,asset_version))''',
 '''CREATE TABLE qa_execution_annotations(run_id INTEGER PRIMARY KEY REFERENCES automation_runs(id) ON DELETE RESTRICT,
 test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,test_case_version_id INTEGER REFERENCES test_case_versions(id) ON DELETE RESTRICT,
 document_version_id INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE RESTRICT,asset_link_id TEXT REFERENCES qa_asset_links(id) ON DELETE RESTRICT,
 environment TEXT NOT NULL,result_json TEXT NOT NULL,evidence_json TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_defects(id TEXT PRIMARY KEY,test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
 failed_run_id INTEGER NOT NULL REFERENCES qa_execution_annotations(run_id) ON DELETE RESTRICT,title TEXT NOT NULL,description TEXT NOT NULL,
 severity TEXT NOT NULL,priority TEXT NOT NULL,state TEXT NOT NULL,assigned_to INTEGER REFERENCES users(id),resolution TEXT NOT NULL DEFAULT '',
 clickup_id TEXT NOT NULL DEFAULT '',created_by INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_retests(id TEXT PRIMARY KEY,defect_id TEXT NOT NULL REFERENCES qa_defects(id) ON DELETE RESTRICT,
 run_id INTEGER NOT NULL UNIQUE REFERENCES qa_execution_annotations(run_id) ON DELETE RESTRICT,actor_id INTEGER NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_lifecycle_events(id INTEGER PRIMARY KEY AUTOINCREMENT,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,
 action TEXT NOT NULL,actor_id INTEGER NOT NULL REFERENCES users(id),detail_json TEXT NOT NULL,created_at TEXT NOT NULL)''',
 'CREATE INDEX idx_qa_requirements_owner ON qa_requirements(document_version_id,source_id)',
 'CREATE INDEX idx_qa_review_case ON qa_review_decisions(test_case_id,id)',
 '''CREATE TRIGGER immutable_requirement_evidence BEFORE UPDATE ON qa_requirements
 WHEN NEW.id IS NOT OLD.id OR NEW.source_id IS NOT OLD.source_id OR NEW.document_version_id IS NOT OLD.document_version_id
 OR NEW.description IS NOT OLD.description OR NEW.quote_hash IS NOT OLD.quote_hash OR NEW.anchor IS NOT OLD.anchor
 OR NEW.logical_key IS NOT OLD.logical_key OR NEW.source_reference IS NOT OLD.source_reference
 BEGIN SELECT RAISE(ABORT,'Requirement source evidence is immutable'); END''',
]
MIGRATION_1.extend([
 "CREATE TRIGGER immutable_requirement_delete BEFORE DELETE ON qa_requirements BEGIN SELECT RAISE(ABORT,'Requirement history is immutable'); END",
 '''CREATE TRIGGER requirement_owner_insert BEFORE INSERT ON qa_requirements
 WHEN NEW.document_version_id IS NOT (SELECT document_version_id FROM knowledge_items WHERE id=NEW.source_id)
 BEGIN SELECT RAISE(ABORT,'Requirement source owner mismatch'); END''',
 '''CREATE TRIGGER requirement_link_owner_insert BEFORE INSERT ON qa_requirement_links
 WHEN NEW.status!='REJECTED' AND (SELECT document_version_id FROM qa_requirements WHERE id=NEW.requirement_id) IS NOT (SELECT document_version_id FROM test_cases WHERE id=NEW.test_case_id)
 BEGIN SELECT RAISE(ABORT,'Requirement Test Case owner mismatch'); END''',
 '''CREATE TRIGGER requirement_link_owner_update BEFORE UPDATE ON qa_requirement_links
 WHEN NEW.status!='REJECTED' AND (SELECT document_version_id FROM qa_requirements WHERE id=NEW.requirement_id) IS NOT (SELECT document_version_id FROM test_cases WHERE id=NEW.test_case_id)
 BEGIN SELECT RAISE(ABORT,'Requirement Test Case owner mismatch'); END''',
])
for table in ('qa_review_decisions','qa_execution_annotations','qa_retests','qa_lifecycle_events','qa_asset_links'):
 for action in ('UPDATE','DELETE'):
  MIGRATION_1.append(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Lifecycle history is immutable'); END")

# A separate additive migration is required for installations already on v1.
MIGRATION_2 = [
 '''CREATE TRIGGER immutable_terminal_run_result BEFORE UPDATE ON automation_runs
 WHEN OLD.status IN ('Passed','Failed','Error','Cancelled','Stopped') AND (
 NEW.status IS NOT OLD.status OR NEW.outcome IS NOT OLD.outcome OR NEW.success IS NOT OLD.success
 OR NEW.return_code IS NOT OLD.return_code OR NEW.duration_seconds IS NOT OLD.duration_seconds
 OR NEW.error_message IS NOT OLD.error_message OR NEW.final_message IS NOT OLD.final_message
 OR NEW.runtime_activity_json IS NOT OLD.runtime_activity_json OR NEW.stdout IS NOT OLD.stdout
 OR NEW.stderr IS NOT OLD.stderr OR NEW.script_path IS NOT OLD.script_path
 OR NEW.screenshot_path IS NOT OLD.screenshot_path OR NEW.started_at IS NOT OLD.started_at
 OR NEW.finished_at IS NOT OLD.finished_at)
 BEGIN SELECT RAISE(ABORT,'Terminal execution result is immutable; create a new rerun'); END''',
 '''CREATE TABLE qa_execution_asset_usages(
 id INTEGER PRIMARY KEY AUTOINCREMENT,run_id INTEGER NOT NULL REFERENCES automation_runs(id) ON DELETE RESTRICT,
 asset_identity_json TEXT NOT NULL,reason TEXT NOT NULL,step_id INTEGER,created_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_execution_pins(
 run_id INTEGER PRIMARY KEY REFERENCES automation_runs(id) ON DELETE RESTRICT,
 test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
 test_case_version_id INTEGER REFERENCES test_case_versions(id) ON DELETE RESTRICT,
 document_version_id INTEGER REFERENCES document_versions(id) ON DELETE RESTRICT,
 specification_json TEXT NOT NULL,asset_identity_json TEXT NOT NULL,
 review_state TEXT NOT NULL,created_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_ai_recommendations(
 id TEXT PRIMARY KEY, test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
 requirement_id TEXT NOT NULL REFERENCES qa_requirements(id) ON DELETE RESTRICT,
 provider_reference TEXT NOT NULL, rationale TEXT NOT NULL,
 content_hash TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)''',
 '''CREATE TABLE qa_ai_recommendation_decisions(
 recommendation_id TEXT PRIMARY KEY REFERENCES qa_ai_recommendations(id) ON DELETE RESTRICT,
 decision TEXT NOT NULL CHECK(decision IN ('APPLIED','REJECTED')),
 comments TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)''',
]
for table in ('qa_ai_recommendations','qa_ai_recommendation_decisions','qa_execution_pins','qa_execution_asset_usages'):
 for action in ('UPDATE','DELETE'):
  MIGRATION_2.append(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'AI recommendation history is immutable'); END")

# Compatibility applies only to pre-installation cases, never to newly created
# cases without review. This registry is NOT an approval or execution record.
MIGRATION_3 = [
 '''CREATE TABLE qa_legacy_execution_cases(test_case_id INTEGER PRIMARY KEY
 REFERENCES test_cases(id) ON DELETE CASCADE,registered_at TEXT NOT NULL)''',
 "INSERT INTO qa_legacy_execution_cases SELECT id,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM test_cases",
 '''CREATE TRIGGER legacy_registry_no_insert BEFORE INSERT ON qa_legacy_execution_cases
 BEGIN SELECT RAISE(ABORT,'Legacy compatibility membership is migration-owned'); END''',
 '''CREATE TRIGGER legacy_registry_no_update BEFORE UPDATE ON qa_legacy_execution_cases
 BEGIN SELECT RAISE(ABORT,'Legacy compatibility membership is immutable'); END''',
]
