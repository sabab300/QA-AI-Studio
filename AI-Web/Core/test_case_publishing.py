"""Test Case publishing workspace, configuration, export and audit services."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from Core.automation_execution_repository import AutomationExecutionRepository
from Core.qa_engineering_version_service import QaEngineeringVersionService
from Core.test_case_workbook import COLUMNS, PUBLISH_COLUMNS, REQUIRED_FIELDS, normalize_status, normalize_template_tokens, normalize_test_case, workbook_bytes
from Database.db_manager import DatabaseManager


TARGETS = {"TESTMANAGER", "CLICKUP"}
MASK = "********"
logger = logging.getLogger(__name__)


class _SecretBox:
    """Authenticated local encryption using the installation signing key.

    This avoids plaintext integration tokens without introducing a new runtime
    dependency. A random nonce and HMAC-derived stream encrypt each value; the
    independent authentication tag rejects tampering before decryption.
    """
    def __init__(self):
        key_path = Path(__file__).resolve().parents[1] / "Database" / "web_secret.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if not key_path.exists():
            key_path.write_bytes(secrets.token_bytes(32))
        self.key = hashlib.sha256(key_path.read_bytes() + b"publishing-config-v1").digest()

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        nonce = secrets.token_bytes(16); raw = value.encode("utf-8")
        stream = b""; counter = 0
        while len(stream) < len(raw):
            stream += hmac.new(self.key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest(); counter += 1
        cipher = bytes(a ^ b for a, b in zip(raw, stream))
        tag = hmac.new(self.key, nonce + cipher, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value: return ""
        payload = base64.urlsafe_b64decode(value.encode("ascii")); nonce, tag, cipher = payload[:16], payload[16:48], payload[48:]
        if not hmac.compare_digest(tag, hmac.new(self.key, nonce + cipher, hashlib.sha256).digest()):
            raise ValueError("Stored integration secret could not be authenticated.")
        stream = b""; counter = 0
        while len(stream) < len(cipher):
            stream += hmac.new(self.key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest(); counter += 1
        return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


class PublishingRepository:
    def __init__(self, db=None):
        self.db = db or DatabaseManager(); self.secrets = _SecretBox(); self.ensure_schema()

    @staticmethod
    def _dict_factory(cursor, row): return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    def ensure_schema(self):
        conn = self.db.get_connection()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS integration_configurations(
              id INTEGER PRIMARY KEY AUTOINCREMENT,target TEXT NOT NULL,name TEXT NOT NULL,
              base_url TEXT NOT NULL DEFAULT '',auth_type TEXT NOT NULL DEFAULT 'TOKEN',secret_blob TEXT NOT NULL DEFAULT '',
              settings_json TEXT NOT NULL DEFAULT '{}',mappings_json TEXT NOT NULL DEFAULT '{}',
              is_default INTEGER NOT NULL DEFAULT 0,created_date TEXT NOT NULL,modified_date TEXT NOT NULL,
              UNIQUE(target,name));
            CREATE TABLE IF NOT EXISTS publication_runs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,run_uuid TEXT NOT NULL UNIQUE,target TEXT NOT NULL,
              configuration_id INTEGER NOT NULL,source_version_id INTEGER NOT NULL,source_file_id INTEGER NOT NULL,
              selected_count INTEGER NOT NULL,success_count INTEGER NOT NULL DEFAULT 0,failed_count INTEGER NOT NULL DEFAULT 0,
              skipped_count INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,published_by TEXT,created_date TEXT NOT NULL,
              completed_date TEXT,FOREIGN KEY(configuration_id) REFERENCES integration_configurations(id));
            CREATE TABLE IF NOT EXISTS publication_items(
              id INTEGER PRIMARY KEY AUTOINCREMENT,publication_run_id INTEGER NOT NULL,test_case_id INTEGER,
              stable_test_case_id TEXT NOT NULL,target TEXT NOT NULL,publication_type TEXT NOT NULL,
              configuration_id INTEGER NOT NULL,payload_hash TEXT NOT NULL,external_id TEXT,external_url TEXT,
              status TEXT NOT NULL,error_message TEXT,published_date TEXT,
              FOREIGN KEY(publication_run_id) REFERENCES publication_runs(id));
            CREATE INDEX IF NOT EXISTS idx_publication_duplicate ON publication_items(stable_test_case_id,target,configuration_id,status);
            """)
            columns={row[1] for row in conn.execute("PRAGMA table_info(publication_items)").fetchall()}
            if "external_custom_id" not in columns: conn.execute("ALTER TABLE publication_items ADD COLUMN external_custom_id TEXT")
            conn.commit()
        finally: conn.close()

    def list_configs(self, target=None):
        conn=self.db.get_connection(); conn.row_factory=self._dict_factory
        rows=conn.execute("SELECT * FROM integration_configurations WHERE (? IS NULL OR target=?) ORDER BY target,is_default DESC,name",(target,target)).fetchall(); conn.close()
        return [self._public_config(row) for row in rows]

    def get_config(self, config_id, include_secret=False):
        conn=self.db.get_connection(); conn.row_factory=self._dict_factory
        row=conn.execute("SELECT * FROM integration_configurations WHERE id=?",(int(config_id),)).fetchone(); conn.close()
        if not row: return None
        result=self._public_config(row)
        if include_secret: result["secret"]=self.secrets.decrypt(row.get("secret_blob") or "")
        return result

    def _public_config(self,row):
        result=dict(row); result["settings"]=json.loads(result.pop("settings_json") or "{}"); result["mappings"]=json.loads(result.pop("mappings_json") or "{}")
        result["secret_is_set"]=bool(result.pop("secret_blob", "")); result["secret"]=""; result["is_default"]=bool(result["is_default"])
        return result

    def save_config(self, values, config_id=None):
        target=str(values.get("target") or "").upper(); name=str(values.get("name") or "").strip()
        if target not in TARGETS or not name: raise ValueError("Target and configuration name are required.")
        base_url=str(values.get("base_url") or "").strip().rstrip("/")
        if base_url and not base_url.lower().startswith("https://"): raise ValueError("Integration Base URL must use HTTPS.")
        existing=self.get_config(config_id,include_secret=True) if config_id else None
        secret=str(values.get("secret") or ""); secret=secret if secret else (existing or {}).get("secret","")
        now=datetime.now().isoformat(); is_default=1 if values.get("is_default") else 0
        conn=self.db.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if is_default: conn.execute("UPDATE integration_configurations SET is_default=0 WHERE target=?",(target,))
            payload=(target,name,base_url,str(values.get("auth_type") or "TOKEN"),self.secrets.encrypt(secret),json.dumps(values.get("settings") or {}),json.dumps(values.get("mappings") or {}),is_default,now)
            if config_id:
                cursor=conn.execute("UPDATE integration_configurations SET target=?,name=?,base_url=?,auth_type=?,secret_blob=?,settings_json=?,mappings_json=?,is_default=?,modified_date=? WHERE id=?",payload+(int(config_id),))
                if cursor.rowcount != 1: raise ValueError("Integration configuration was not found.")
                result_id=int(config_id)
            else:
                cursor=conn.execute("INSERT INTO integration_configurations(target,name,base_url,auth_type,secret_blob,settings_json,mappings_json,is_default,created_date,modified_date) VALUES(?,?,?,?,?,?,?,?,?,?)",payload+(now,)); result_id=cursor.lastrowid
            conn.commit(); return self.get_config(result_id)
        except sqlite3.IntegrityError as error:
            conn.rollback(); raise ValueError("A configuration with this name already exists for the target.") from error
        except Exception: conn.rollback(); raise
        finally: conn.close()

    def duplicate_config(self, config_id, name):
        source=self.get_config(config_id,include_secret=True)
        if not source: raise ValueError("Integration configuration was not found.")
        source.update({"name":name,"is_default":False}); return self.save_config(source)

    def delete_config(self, config_id):
        conn=self.db.get_connection()
        try:
            if conn.execute("SELECT COUNT(*) FROM publication_runs WHERE configuration_id=?",(int(config_id),)).fetchone()[0]:
                raise ValueError("Configuration has publishing history and cannot be deleted.")
            if conn.execute("DELETE FROM integration_configurations WHERE id=?",(int(config_id),)).rowcount != 1: raise ValueError("Integration configuration was not found.")
            conn.commit()
        except Exception: conn.rollback(); raise
        finally: conn.close()

    def duplicate_for(self, stable_id, target, config_id):
        conn=self.db.get_connection(); conn.row_factory=self._dict_factory
        row=conn.execute("SELECT external_id,external_url,published_date FROM publication_items WHERE stable_test_case_id=? AND target=? AND configuration_id=? AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(stable_id,target,int(config_id))).fetchone(); conn.close(); return row

    def latest_clickup_publications(self, stable_ids):
        stable_ids=[str(value) for value in stable_ids if value not in (None, "")]
        if not stable_ids: return {}
        placeholders=",".join("?" for _ in stable_ids); conn=self.db.get_connection(); conn.row_factory=self._dict_factory
        rows=conn.execute(
            f"SELECT stable_test_case_id,external_id,external_custom_id,external_url,published_date FROM publication_items WHERE target='CLICKUP' AND status='PUBLISHED' AND stable_test_case_id IN ({placeholders}) ORDER BY id DESC",
            stable_ids,
        ).fetchall(); conn.close()
        result={}
        for row in rows: result.setdefault(row["stable_test_case_id"],row)
        return result

    def with_publication_state(self, rows):
        """Read both destinations independently from successful publication history."""
        identifiers = list(dict.fromkeys(str(row.get("tc_id") or row.get("tc_number") or row.get("id")) for row in rows))
        states = {}
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        try:
            for offset in range(0, len(identifiers), 500):
                batch = identifiers[offset:offset + 500]
                marks = ",".join("?" for _ in batch)
                records = conn.execute(
                    f"SELECT stable_test_case_id,target,external_id,external_custom_id FROM publication_items "
                    f"WHERE status='PUBLISHED' AND target IN ('CLICKUP','TESTMANAGER') "
                    f"AND stable_test_case_id IN ({marks}) ORDER BY id DESC", batch,
                ).fetchall()
                for record in records:
                    identifier = str(record.get("external_id") or record.get("external_custom_id") or "").strip()
                    if not identifier:
                        continue
                    state = states.setdefault(record["stable_test_case_id"], {
                        "publishing_status": "Published", "clickup_id": "", "testmanager_id": "",
                    })
                    field = "clickup_id" if record["target"] == "CLICKUP" else "testmanager_id"
                    if not state[field] and identifier:
                        state[field] = identifier
        finally:
            conn.close()
        return [dict(row, **states.get(str(row.get("tc_id") or row.get("tc_number") or row.get("id")), {
            "publishing_status": "Not Published", "clickup_id": "", "testmanager_id": "",
        })) for row in rows]

    def create_run(self,target,config_id,version_id,file_id,count,username):
        run_uuid=str(uuid.uuid4()); now=datetime.now().isoformat(); conn=self.db.get_connection()
        cursor=conn.execute("INSERT INTO publication_runs(run_uuid,target,configuration_id,source_version_id,source_file_id,selected_count,status,published_by,created_date) VALUES(?,?,?,?,?,?,? ,?,?)",(run_uuid,target,int(config_id),int(version_id),int(file_id),count,"RUNNING",username,now)); conn.commit(); run_id=cursor.lastrowid; conn.close(); return run_id,run_uuid

    def add_item(self,run_id,row,target,config_id,payload,status,external_id="",external_url="",error="",external_custom_id=""):
        stable=str(row.get("tc_id") or row.get("tc_number") or row.get("id")); digest=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest(); now=datetime.now().isoformat()
        conn=self.db.get_connection(); conn.execute("INSERT INTO publication_items(publication_run_id,test_case_id,stable_test_case_id,target,publication_type,configuration_id,payload_hash,external_id,external_custom_id,external_url,status,error_message,published_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(run_id,row.get("id") if str(row.get("id","")).isdigit() else None,stable,target,"BUG" if target=="CLICKUP" else "TEST_CASE",int(config_id),digest,external_id,external_custom_id,external_url,status,error or None,now if status=="PUBLISHED" else None)); conn.commit(); conn.close()

    def finish_run(self,run_id,success,failed,skipped):
        status="COMPLETED" if not failed else ("PARTIAL" if success or skipped else "FAILED"); conn=self.db.get_connection(); conn.execute("UPDATE publication_runs SET success_count=?,failed_count=?,skipped_count=?,status=?,completed_date=? WHERE id=?",(success,failed,skipped,status,datetime.now().isoformat(),run_id)); conn.commit(); conn.close(); return status

    def history(self):
        conn=self.db.get_connection(); conn.row_factory=self._dict_factory
        runs=conn.execute("SELECT r.*,c.name configuration_name FROM publication_runs r JOIN integration_configurations c ON c.id=r.configuration_id ORDER BY r.id DESC LIMIT 100").fetchall()
        for run in runs: run["items"]=conn.execute("SELECT stable_test_case_id,external_id,external_custom_id,external_url,status,error_message,published_date FROM publication_items WHERE publication_run_id=? ORDER BY id",(run["id"],)).fetchall()
        conn.close(); return runs


class PublishingService:
    def __init__(self): self.repository=PublishingRepository(); self.versions=QaEngineeringVersionService()

    def load_workspace(self,version_id,file_id):
        loaded=self.versions.load_version(version_id,file_id); rows=[dict(row) for row in loaded["test_cases"]]
        latest=AutomationExecutionRepository().latest_runs_for_test_cases([row.get("id") for row in rows])
        stable_ids=[str(row.get("tc_id") or row.get("tc_number") or row.get("id")) for row in rows]
        published=self.repository.latest_clickup_publications(stable_ids)
        for row in rows:
            run=latest.get(row.get("id")) or {}; method=str(row.get("execution") or row.get("automation_type") or "").strip()
            if method not in {"Playwright","API","SQL"}: method="Manual"
            raw=row.get("status") or row.get("last_result") or run.get("outcome") or run.get("status") or ""
            status=normalize_status({"status":raw})
            stable_id=str(row.get("tc_id") or row.get("tc_number") or row.get("id")); publication=published.get(stable_id)
            row.update({"stable_id":stable_id,"execution_type":method,"test_case_status":status,"execution_method":method,"execution_result":status,"execution":method,"status":status,"last_execution":run.get("finished_at") or run.get("created_date") or row.get("last_run_date") or "","run_id":run.get("run_uuid") or "","publishing_status":"Published" if publication else "Not Published","published_external_id":(publication or {}).get("external_id") or "","published_external_custom_id":(publication or {}).get("external_custom_id") or "","published_external_url":(publication or {}).get("external_url") or ""})
        return {"version":loaded["version"],"file":loaded["file"],"columns":[{"field":field,"label":label,"required":field in REQUIRED_FIELDS} for field,label in COLUMNS],"test_cases":self.repository.with_publication_state(rows)}

    @staticmethod
    def validate_rows(rows):
        errors=[]; seen=set()
        for index,raw in enumerate(rows,start=1):
            row=normalize_test_case(raw); missing=[dict(COLUMNS)[field] for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip()]
            stable=str(row.get("tc_number") or "").upper()
            if stable in seen: missing.append("duplicate Test Case ID")
            seen.add(stable)
            if missing: errors.append({"stable_id":raw.get("stable_id"),"row":index,"errors":missing})
        return errors

    def preview(self,target,config_id,rows,validate_destination=False):
        target=target.upper(); config=self.repository.get_config(config_id)
        if not config or config["target"] != target: raise ValueError("Saved target configuration was not found.")
        if target == "CLICKUP" and validate_destination:
            settings=config.get("settings") or {}; list_id=str(settings.get("list_id") or "")
            if not list_id: raise ValueError("The saved ClickUp destination List is missing.")
            live=self.discover_clickup_values({"configuration_id":config_id,"base_url":config.get("base_url"),"workspace_id":settings.get("workspace_id"),"space_id":settings.get("space_id")},"metadata",list_id)
            config=dict(config); config["settings"]=dict(settings); config["settings"]["destination_fields"]=live.get("fields") or []
        readiness=self.configuration_readiness(config)
        if target == "CLICKUP" and not readiness["ready"]:
            raise ValueError("ClickUp configuration is incomplete. Missing required mappings: "+", ".join(readiness["missing_required"]))
        mappings=config.get("mappings") or {}; defaults=(config.get("settings") or {}).get("defaults") or {}; prepared=[]
        for row in rows:
            destination_fields=(config.get("settings") or {}).get("destination_fields",[])
            destination_ids={field.get("id") for field in destination_fields}
            fields={field.get("id"):field.get("default") for field in destination_fields if field.get("id") and field.get("default") not in (None,"")}
            fields.update({key:value for key,value in defaults.items() if key in destination_ids and value not in (None,"",[])})
            if defaults.get("due_date_rule") == "OFFSET" and "due_date" not in fields:
                fields["due_date"]=(datetime.now()+timedelta(days=max(0,int(defaults.get("due_date_offset") or 0)))).date().isoformat()
            validation=[]
            for destination,spec in mappings.items():
                spec=spec if isinstance(spec,dict) else {"source":spec}
                mapping_errors=self.validate_mapping_spec(spec)
                destination_meta=next((field for field in destination_fields if str(field.get("id"))==str(destination)),{})
                destination_name=str(destination_meta.get("name") or destination)
                validation.extend(f"{destination_name}: {message}" for message in mapping_errors)
                fields[destination]=self._mapped_value(row,spec,defaults.get(destination,"")) if not mapping_errors else ""
                if not mapping_errors and str(spec.get("type") or "").upper()=="VALUE_MAP":
                    source_value=row.get(str(spec.get("source") or "")); value_map=spec.get("value_map") or {}
                    matched=str(source_value) in value_map or str(source_value).casefold() in {str(key).casefold() for key in value_map}
                    if source_value not in (None,"") and not matched and spec.get("default") in (None,""):
                        validation.append(f'{destination_name}: No VALUE MAP entry exists for source value "{source_value}".')
                if not mapping_errors and str(spec.get("type") or "").upper()=="TEMPLATE" and re.search(r"{{\s*[^{}]+\s*}}",str(fields.get(destination) or "")):
                    validation.append(f"{destination_name}: Template contains unresolved tokens.")
            # A reviewed preview may send explicit destination-field edits.
            # They never alter the canonical workbook/test-case row.
            if isinstance(row.get("_publish_fields"), dict):
                fields.update(row["_publish_fields"])
            for destination in destination_fields:
                field_id=str(destination.get("id") or "")
                if field_id in fields:
                    fields[field_id]=self._destination_value(destination,fields[field_id])
            custom_item_id=int((config.get("settings") or {}).get("custom_item_id") or 0)
            for destination in (config.get("settings") or {}).get("destination_fields",[]):
                field_id=str(destination.get("id") or "")
                value=fields.get(field_id)
                if destination.get("scope") == "custom" and not self._field_applies(destination,custom_item_id): continue
                if destination.get("required") and field_id and (value in (None,"") or value == []):
                    message=f"{destination.get('name') or field_id} is required by ClickUp"
                    if message not in validation: validation.append(message)
                allowed={str(option.get("id")) for option in destination.get("options",[]) if option.get("id") is not None}
                values=value if isinstance(value,list) else [value]
                if allowed and any(item not in (None,"") and str(item) not in allowed for item in values):
                    validation.append(f"{destination.get('name') or field_id} contains a value unavailable in this ClickUp destination")
            if target=="CLICKUP":
                fields.setdefault("name",f"[{row.get('tc_number') or row.get('tc_id')}] {row.get('test_case') or row.get('scenario')}")
                fields.setdefault("description",self._bug_description(row))
                if not str(fields.get("name") or "").strip(): validation.append("name is required")
            duplicate=self.repository.duplicate_for(str(row.get("tc_id") or row.get("tc_number") or row.get("id")),target,config_id)
            if validate_destination and duplicate: validation.append(f"Already published to ClickUp as {duplicate.get('external_id') or 'an existing task'}.")
            if target == "CLICKUP" and validate_destination:
                validation.extend(self.validate_clickup_payload(self.build_clickup_payload(fields,destination_fields,custom_item_id),destination_fields))
            prepared.append({"stable_id":row.get("stable_id") or row.get("tc_id") or row.get("tc_number"),"test_case":row,"fields":fields,"duplicate":duplicate,"validation":validation})
        return {"target":target,"configuration":config,"rows":prepared,"destination_validated":bool(validate_destination)}

    def configuration_readiness(self,config):
        """Return whether every API-confirmed required destination has a valid mapping."""
        mappings=config.get("mappings") or {}; missing=[]
        custom_item_id=int((config.get("settings") or {}).get("custom_item_id") or 0)
        for field in (config.get("settings") or {}).get("destination_fields",[]):
            if field.get("scope") == "custom" and not self._field_applies(field,custom_item_id): continue
            field_id=str(field.get("id") or "")
            required=field_id == "name" or field.get("required") is True
            spec=mappings.get(field_id)
            if required and (not isinstance(spec,dict) or self.validate_mapping_spec(spec)):
                missing.append(str(field.get("name") or field_id))
        return {"ready":not missing,"missing_required":missing}

    @staticmethod
    def _field_applies(field,custom_item_id=0):
        applied=field.get("applied_objects") or []
        return not applied or any(int(item.get("object_id") or -1)==int(custom_item_id) for item in applied if int(item.get("object_type") or 0)==19)

    @classmethod
    def build_clickup_payload(cls,fields,destination_fields,custom_item_id=0):
        """Build the future Create Task contract without issuing a request."""
        standard={"name","description","status","assignees","due_date","priority","tags"}
        payload={key:fields[key] for key in standard if key in fields and fields[key] not in (None,"",[])}
        metadata={str(field.get("id")):field for field in destination_fields}
        if "status" in payload:
            status_meta=metadata.get("status",{}); status_value=payload["status"]
            payload["status"]=next((str(option.get("name")) for option in status_meta.get("options",[]) if str(option.get("id"))==str(status_value)),str(status_value))
        if "assignees" in payload:
            values=payload["assignees"] if isinstance(payload["assignees"],list) else [payload["assignees"]]
            payload["assignees"]=[int(value) for value in values if str(value).isdigit()]
        if "priority" in payload and str(payload["priority"]).isdigit(): payload["priority"]=int(payload["priority"])
        if "due_date" in payload and isinstance(payload["due_date"],str):
            try: payload["due_date"]=int(datetime.fromisoformat(payload["due_date"]).replace(tzinfo=timezone.utc).timestamp()*1000)
            except ValueError: pass
        payload["custom_fields"]=[]
        for field in destination_fields:
            field_id=str(field.get("id") or ""); value=fields.get(field_id)
            if field.get("scope") != "custom" or not cls._field_applies(field,custom_item_id) or field_id not in fields or value in (None,"",[]): continue
            if str(field.get("type") or "").lower() in {"users","user"}:
                users=value if isinstance(value,list) else [value]
                value={"add":[str(user) for user in users if str(user)],"rem":[]}
            payload["custom_fields"].append({"id":field_id,"value":value})
        payload["check_required_custom_fields"]=True
        if custom_item_id: payload["custom_item_id"]=int(custom_item_id)
        return payload

    @staticmethod
    def validate_clickup_payload(payload,destination_fields):
        """Validate the exact wire representation used by Create Task."""
        errors=[]; metadata={str(field.get("id")):field for field in destination_fields}
        if not isinstance(payload.get("name"),str) or not payload["name"].strip(): errors.append("Task Name must resolve to text.")
        if "status" in payload:
            names={str(item.get("name")) for item in metadata.get("status",{}).get("options",[])}
            if names and str(payload["status"]) not in names: errors.append("Status must use a current ClickUp status name.")
        if "assignees" in payload and (not isinstance(payload["assignees"],list) or any(not isinstance(value,int) for value in payload["assignees"])):
            errors.append("Assignee must use ClickUp numeric user IDs.")
        for item in payload.get("custom_fields",[]):
            field=metadata.get(str(item.get("id")),{}); field_type=str(field.get("type") or "").lower(); value=item.get("value"); name=field.get("name") or item.get("id")
            if field_type in {"users","user"}:
                if not isinstance(value,dict) or not isinstance(value.get("add"),list) or not isinstance(value.get("rem"),list) or not value.get("add"):
                    errors.append(f"{name} must use the ClickUp People field add/rem representation.")
            elif field_type == "drop_down":
                allowed={str(option.get("id")) for option in field.get("options",[])}
                if allowed and str(value) not in allowed: errors.append(f"{name} must use a current ClickUp option ID.")
        return errors

    @staticmethod
    def _safe_payload_summary(list_id,payload,destination_fields):
        metadata={str(field.get("id")):field for field in destination_fields}
        custom=[]
        for item in payload.get("custom_fields",[]):
            value=item.get("value"); custom.append({"id":item.get("id"),"name":metadata.get(str(item.get("id")),{}).get("name"),"type":metadata.get(str(item.get("id")),{}).get("type"),"value_type":type(value).__name__,"value":value})
        return {"list_id":str(list_id),"name":payload.get("name"),"status":payload.get("status"),"assignees":payload.get("assignees"),"due_date":payload.get("due_date"),"priority":payload.get("priority"),"tags":payload.get("tags"),"custom_fields":custom,"check_required_custom_fields":payload.get("check_required_custom_fields")}

    @staticmethod
    def validate_mapping_spec(spec):
        """Validate the persisted mapping format using canonical Test Case fields."""
        spec=spec if isinstance(spec,dict) else {"source":spec}
        mapping_type=str(spec.get("type") or "DIRECT").upper()
        canonical={field for field,_label in COLUMNS}
        if mapping_type == "DEFAULT":
            return [] if spec.get("default") not in (None,"",[]) else ["DEFAULT requires a value"]
        if mapping_type == "DIRECT":
            source=str(spec.get("source") or "")
            return [] if source in canonical else ["Select a QA AI Studio field."]
        if mapping_type == "TEMPLATE":
            template=normalize_template_tokens(spec.get("template") or "")
            if not template.strip(): return ["TEMPLATE requires template text"]
            unknown=sorted(set(re.findall(r"{{\s*([^{}]+?)\s*}}",template))-canonical)
            return ["Unknown template token(s): "+", ".join(unknown)] if unknown else []
        if mapping_type == "VALUE_MAP":
            source=str(spec.get("source") or "")
            value_map=spec.get("value_map")
            errors=[]
            if source not in canonical: errors.append("VALUE_MAP requires a canonical source field")
            if not isinstance(value_map,dict) or not value_map or any(not str(key).strip() or value in (None,"") for key,value in (value_map or {}).items()): errors.append("VALUE_MAP requires at least one complete source-to-destination pair")
            return errors
        return [f"Unsupported mapping type: {mapping_type}"]

    @staticmethod
    def _destination_value(field,value):
        """Resolve display labels to API-compatible values and preserve valid IDs."""
        options=field.get("options") or []
        if field.get("multiple") or field.get("id") == "tags":
            values=value if isinstance(value,list) else [part.strip() for part in str(value or "").split(",") if part.strip()]
            return [next((option.get("id") for option in options if str(option.get("id"))==str(item) or str(option.get("name","")).casefold()==str(item).casefold()),item) for item in values]
        if options and value not in (None,""):
            return next((option.get("id") for option in options if str(option.get("id"))==str(value) or str(option.get("name","")).casefold()==str(value).casefold()),value)
        return value

    @staticmethod
    def _mapped_value(row, spec, fallback=""):
        """Apply one persisted mapping without mutating the source Test Case."""
        mapping_type=str(spec.get("type") or "DIRECT").upper()
        source=str(spec.get("source") or "")
        if mapping_type == "TEMPLATE":
            template=normalize_template_tokens(spec.get("template") or "")
            return re.sub(r"{{\s*([A-Za-z0-9_]+)\s*}}", lambda match: str(row.get(match.group(1)) or ""), template)
        if mapping_type == "DEFAULT": return spec.get("default",fallback)
        value=row.get(source) if source else spec.get("default",fallback)
        if mapping_type == "VALUE_MAP":
            value_map=spec.get("value_map") if isinstance(spec.get("value_map"),dict) else {}
            if str(value) in value_map: return value_map[str(value)]
            folded={str(key).casefold():mapped for key,mapped in value_map.items()}
            return folded.get(str(value).casefold(),spec.get("default",fallback))
        return value if value not in (None, "") else spec.get("default",fallback)

    @staticmethod
    def _bug_description(row):
        fields=(("Test Case ID",row.get("tc_id") or row.get("tc_number")),("Module",row.get("module")),("Execution Type",row.get("execution_type") or row.get("execution_method")),("Status",row.get("test_case_status") or row.get("execution_result")),("Environment",row.get("environment_name")),("Preconditions",row.get("pre_conditions")),("Steps to Reproduce",row.get("steps")),("Expected Result",row.get("expected_result")),("Actual Result",row.get("actual_result")),("Run ID",row.get("run_id")))
        return "\n\n".join(f"**{label}:**\n{value}" for label,value in fields if value)

    def export(self,rows,target="",config_id=None):
        errors=self.validate_rows(rows)
        if errors: raise ValueError("Publishing rows are invalid: "+json.dumps(errors[:10]))
        if target.upper()=="TESTMANAGER" and config_id:
            preview=self.preview("TESTMANAGER",config_id,rows); mappings=preview["configuration"].get("mappings") or {}
            if mappings:
                book=Workbook(); sheet=book.active; sheet.title="Test Cases"
                destinations=list(mappings); sheet.append(destinations)
                for item in preview["rows"]: sheet.append([item["fields"].get(key,"") for key in destinations])
                stream=BytesIO(); book.save(stream); return stream.getvalue()
        return workbook_bytes(self.repository.with_publication_state(rows), columns=PUBLISH_COLUMNS)

    def _request(self,config,method,path,payload=None):
        raw=self.repository.get_config(config["id"],include_secret=True)
        return self._request_values(raw,method,path,payload)

    def _request_values(self,raw,method,path,payload=None):
        url=(raw.get("base_url") or "https://api.clickup.com/api/v2").rstrip("/")+path
        if not url.lower().startswith("https://"): raise ValueError("External integration URL must use HTTPS.")
        data=json.dumps(payload).encode() if payload is not None else None; headers={"Accept":"application/json","Authorization":raw.get("secret") or ""}
        if data is not None: headers["Content-Type"]="application/json"
        request=urllib.request.Request(url,data=data,headers=headers,method=method)
        try:
            with urllib.request.urlopen(request,timeout=30) as response: return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            body=error.read().decode("utf-8",errors="replace")[:4000]
            try:
                parsed=json.loads(body); code=parsed.get("ECODE") or parsed.get("code") or parsed.get("error_code"); message=parsed.get("err") or parsed.get("message") or parsed.get("error") or body
            except (json.JSONDecodeError,AttributeError): code=None; message=body
            secret=str(raw.get("secret") or "")
            safe_message=str(message or "No response message supplied.").replace(secret,MASK) if secret else str(message or "No response message supplied.")
            safe_message=re.sub(r"(?i)(authorization|token|secret_blob|secret)\s*[:=]\s*[^\s,;]+",r"\1="+MASK,safe_message)[:1000]
            messages={401:"ClickUp authentication failed. Verify the saved token.",403:"ClickUp denied access to the selected destination.",404:"The selected ClickUp destination no longer exists or is inaccessible.",429:"ClickUp rate limit reached. Wait and try again."}
            prefix=messages.get(error.code,"ClickUp Create Task failed")
            detail=f"{prefix}: HTTP {error.code}"+(f"; Code: {code}" if code else "")+f"; Message: {safe_message}"
            raise ValueError(detail) from error
        except (urllib.error.URLError,TimeoutError) as error: raise ValueError(f"Destination connection failed: {error.reason if hasattr(error,'reason') else error}") from error

    def test_connection(self,config_id):
        config=self.repository.get_config(config_id)
        if not config: raise ValueError("Configuration was not found.")
        if config["target"]=="TESTMANAGER": return {"ok":False,"supported":False,"detail":"TestManager upload/API contract is not configured. Final XLSX export is available."}
        user=self._request(config,"GET","/user"); return {"ok":True,"supported":True,"detail":f"Connected as {user.get('user',{}).get('username') or 'ClickUp user'}."}

    def transient_clickup_config(self, values):
        """Build an in-memory ClickUp config; a stored secret may be reused by ID."""
        saved=self.repository.get_config(values.get("configuration_id"),include_secret=True) if values.get("configuration_id") else None
        secret=str(values.get("secret") or (saved or {}).get("secret") or "")
        base_url=str(values.get("base_url") or (saved or {}).get("base_url") or "https://api.clickup.com/api/v2").strip().rstrip("/")
        if not secret: raise ValueError("A ClickUp token is required.")
        return {"base_url":base_url,"secret":secret}

    def test_clickup_values(self, values):
        user=self._request_values(self.transient_clickup_config(values),"GET","/user")
        return {"ok":True,"supported":True,"detail":f"Connected as {user.get('user',{}).get('username') or 'ClickUp user'}."}

    def discover_clickup(self,config_id,kind,parent_id=""):
        config=self.repository.get_config(config_id)
        if not config or config["target"]!="CLICKUP": raise ValueError("ClickUp configuration was not found.")
        routes={"workspaces":"/team","spaces":f"/team/{parent_id}/space?archived=false","folders":f"/space/{parent_id}/folder?archived=false","lists":f"/folder/{parent_id}/list?archived=false","folderless_lists":f"/space/{parent_id}/list?archived=false","fields":f"/list/{parent_id}/field?include_applied_objects=true"}
        if kind not in routes or (kind!="workspaces" and not parent_id): raise ValueError("A valid discovery type and parent ID are required.")
        return self._normalize_clickup_discovery(kind,self._request(config,"GET",routes[kind]))

    def discover_clickup_values(self,values,kind,parent_id=""):
        routes={"workspaces":"/team","spaces":f"/team/{parent_id}/space?archived=false","folders":f"/space/{parent_id}/folder?archived=false","lists":f"/folder/{parent_id}/list?archived=false","folderless_lists":f"/space/{parent_id}/list?archived=false","metadata":f"/list/{parent_id}","fields":f"/list/{parent_id}/field?include_applied_objects=true"}
        if kind not in routes or (kind!="workspaces" and not parent_id): raise ValueError("A valid discovery type and parent ID are required.")
        config=self.transient_clickup_config(values)
        if kind == "metadata":
            details=self._request_values(config,"GET",routes[kind])
            fields=self._request_values(config,"GET",routes["fields"])
            teams=self._request_values(config,"GET","/team") if values.get("workspace_id") else {}
            try: tags=self._request_values(config,"GET",f"/space/{values.get('space_id')}/tag") if values.get("space_id") else {}
            except ValueError: tags={}
            return self._normalize_metadata(details,fields,teams,tags,str(values.get("workspace_id") or ""))
        return self._normalize_clickup_discovery(kind,self._request_values(config,"GET",routes[kind]))

    @staticmethod
    def _normalize_clickup_discovery(kind,data):
        keys={"workspaces":"teams","spaces":"spaces","folders":"folders","lists":"lists","folderless_lists":"lists","fields":"fields"}
        rows=data.get(keys[kind],[]) if isinstance(data,dict) else []
        return {"kind":kind,"items":[{"id":str(row.get("id") or ""),"name":str(row.get("name") or "").strip()} for row in rows if row.get("id") and row.get("name")]}

    @staticmethod
    def _normalize_metadata(details,field_response,team_response=None,tag_response=None,workspace_id=""):
        statuses=[{"id":str(item.get("id") or item.get("status") or ""),"name":str(item.get("status") or item.get("name") or ""),"type":item.get("type")} for item in (details.get("statuses") or [])]
        members=[]
        for team in (team_response or {}).get("teams",[]):
            if workspace_id and str(team.get("id")) != workspace_id: continue
            for member in team.get("members",[]) or []:
                user=member.get("user") or {}; user_id=user.get("id")
                if user_id is not None: members.append({"id":str(user_id),"name":str(user.get("username") or user.get("email") or user_id)})
        tags=[{"id":str(tag.get("name") or ""),"name":str(tag.get("name") or "")} for tag in (tag_response or {}).get("tags",[]) if tag.get("name")]
        fields=[]
        for field in field_response.get("fields",[]) if isinstance(field_response,dict) else []:
            type_config=field.get("type_config") if isinstance(field.get("type_config"),dict) else {}
            options=[]
            for option in type_config.get("options",[]) or []:
                options.append({"id":str(option.get("id") or option.get("orderindex") or option.get("name") or ""),"name":str(option.get("name") or option.get("label") or option.get("value") or "")})
            field_type=str(field.get("type") or "text")
            if field_type in {"users","user"} and not options: options=members
            reported_required=bool(field.get("required")) if "required" in field else None
            fields.append({"id":str(field.get("id") or ""),"name":str(field.get("name") or ""),"type":field_type,"scope":"custom","required":reported_required,"default":field.get("default_value"),"options":options,"applied_objects":field.get("applied_objects") or []})
        priorities=[{"id":"1","name":"Urgent"},{"id":"2","name":"High"},{"id":"3","name":"Normal"},{"id":"4","name":"Low"}]
        standard=[{"id":"name","name":"Task Name","type":"text","scope":"standard","required":True,"default":None,"options":[]},{"id":"description","name":"Description","type":"text","scope":"standard","required":False,"default":None,"options":[]},{"id":"status","name":"Status","type":"status","scope":"standard","required":False,"default":None,"options":statuses},{"id":"assignees","name":"Assignee","type":"users","scope":"standard","multiple":True,"required":False,"default":None,"options":members},{"id":"due_date","name":"Due Date","type":"date","scope":"standard","required":False,"default":None,"options":[]},{"id":"priority","name":"Priority","type":"priority","scope":"standard","required":False,"default":None,"options":priorities},{"id":"tags","name":"Tags","type":"tags","scope":"standard","multiple":True,"required":False,"default":None,"options":tags}]
        return {"list":{"id":str(details.get("id") or ""),"name":str(details.get("name") or "")},"statuses":statuses,"members":members,"tags":tags,"priorities":priorities,"fields":standard+fields}

    def publish(self,target,config_id,version_id,file_id,rows,username,allow_republish=False):
        target=target.upper(); config=self.repository.get_config(config_id)
        if not config or config["target"]!=target: raise ValueError("Saved target configuration was not found.")
        if target=="TESTMANAGER": raise ValueError("TestManager publishing is inactive until its real API/import contract is configured. Export the final XLSX instead.")
        preview=self.preview(target,config_id,rows,validate_destination=(target=="CLICKUP")); run_id,run_uuid=self.repository.create_run(target,config_id,version_id,file_id,len(rows),username); success=failed=skipped=0; results=[]
        preview_settings=preview["configuration"].get("settings") or {}; destination_fields=preview_settings.get("destination_fields",[]); custom_item_id=int(preview_settings.get("custom_item_id") or 0)
        for item in preview["rows"]:
            row=item["test_case"]; payload=self.build_clickup_payload(item["fields"],destination_fields,custom_item_id)
            if item["validation"]:
                failed+=1; error="; ".join(item["validation"]); self.repository.add_item(run_id,row,target,config_id,payload,"FAILED",error=error); results.append({"stable_id":item["stable_id"],"status":"FAILED","error":error}); continue
            if item["duplicate"] and not allow_republish:
                skipped+=1; self.repository.add_item(run_id,row,target,config_id,payload,"SKIPPED",error="Already published"); results.append({"stable_id":item["stable_id"],"status":"SKIPPED","duplicate":item["duplicate"]}); continue
            if not payload.get("name") or not (config.get("settings") or {}).get("list_id"):
                failed+=1; error="ClickUp task name and configured List ID are required."; self.repository.add_item(run_id,row,target,config_id,payload,"FAILED",error=error); results.append({"stable_id":item["stable_id"],"status":"FAILED","error":error}); continue
            try:
                logger.info("ClickUp Create Task sanitized payload: %s",json.dumps(self._safe_payload_summary(config['settings']['list_id'],payload,destination_fields),default=str))
                created=self._request(config,"POST",f"/list/{config['settings']['list_id']}/task",payload); external_id=str(created.get("id") or ""); external_custom_id=str(created.get("custom_id") or ""); external_url=str(created.get("url") or "")
                if not external_id: raise ValueError("ClickUp did not return a task ID.")
                success+=1; self.repository.add_item(run_id,row,target,config_id,payload,"PUBLISHED",external_id,external_url,external_custom_id=external_custom_id); results.append({"stable_id":item["stable_id"],"status":"PUBLISHED","external_id":external_id,"external_custom_id":external_custom_id,"external_url":external_url})
            except ValueError as error:
                failed+=1; self.repository.add_item(run_id,row,target,config_id,payload,"FAILED",error=str(error)); results.append({"stable_id":item["stable_id"],"status":"FAILED","error":str(error)})
        status=self.repository.finish_run(run_id,success,failed,skipped); return {"run_uuid":run_uuid,"status":status,"success":success,"failed":failed,"skipped":skipped,"items":results}
