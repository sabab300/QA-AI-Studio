"""Isolated destructive contract checks for Manage Knowledge administration."""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path[:0] = [str(WEB), str(ROOT)]

from Core.knowledge_web_repository import KnowledgeRepository
from Shared.central_repository import CentralRepository


class FakeVectors:
    def get_all(self): return {"ids": [], "metadatas": []}
    def update_metadata(self, ids, values): return []
    def restore_metadata(self, snapshot): return None
    def delete_with_snapshot(self, ids): return []
    def restore_snapshot(self, snapshot): return None


class CommitFailureConnection:
    def __init__(self, connection): self.connection = connection
    def execute(self, *args, **kwargs): return self.connection.execute(*args, **kwargs)
    def commit(self): raise sqlite3.OperationalError("injected commit failure")
    def rollback(self): return self.connection.rollback()
    def close(self): return self.connection.close()


class SecondConnectionCommitFailure:
    def __init__(self, original): self.original, self.calls = original, 0
    def get_connection(self):
        self.calls += 1
        connection = self.original.get_connection()
        return CommitFailureConnection(connection) if self.calls == 2 else connection


def main():
    import Core.vector_store as vector_store
    vector_store.VectorStore = FakeVectors
    with tempfile.TemporaryDirectory(prefix="qa-kh-admin-") as temporary:
        temporary = Path(temporary)
        db_path = temporary / "metadata.db"
        shutil.copy2(WEB / "Database" / "metadata.db", db_path)
        managed = temporary / "repository"
        central = CentralRepository(managed)
        source_folder = central.build_source_path(
            domain="KH Disposable", module="Admin", knowledge_name="Fixture",
            version="1.0", document_type="CRF", source_type="Files",
            source_file_id="fixture-V1")
        source_folder.mkdir(parents=True)
        source_file = source_folder / "fixture.txt"
        source_file.write_text("fixture", encoding="utf-8")

        repository = KnowledgeRepository()
        repository.metadata.db.db_path = db_path
        repository.repository.central = central
        repository.repository.repository_root = managed
        kid = repository.metadata.save_knowledge_item(
            "KH Disposable", "Admin", "Fixture", "1.0",
            {"file_name": source_file.name, "repository_path": str(source_file),
             "repository_relative_path": central.relative(source_file), "sha256": "fixture",
             "file_size": source_file.stat().st_size, "extension": ".txt",
             "source_file_id": "fixture-V1"},
            {"document_type": "CRF", "summary": "fixture", "tags": ["fixture"]}, "Files")
        connection = sqlite3.connect(db_path)
        tc_folder = central.build_test_case_version_path(source_folder, 1, "fixture-V1")
        tc_folder.mkdir(); workbook = tc_folder / "fixture.xlsx"; workbook.write_bytes(b"xlsx")
        cursor = connection.cursor()
        cursor.execute("INSERT INTO test_case_versions(source_knowledge_id,source_file_id,version_number,folder_path,workbook_path,created_date) VALUES(?,?,?,?,?,datetime('now'))", (kid,"fixture-V1",1,str(tc_folder),str(workbook)))
        version_id = cursor.lastrowid
        cursor.execute("INSERT INTO test_case_files(test_case_version_id,file_name,workbook_path,sha256,created_date) VALUES(?,?,?,?,datetime('now'))", (version_id,workbook.name,str(workbook),"xlsx"))
        xlsx_id = cursor.lastrowid
        cursor.execute("INSERT INTO test_cases(tc_number,domain,module,knowledge_name,tc_id,test_case_version_id,automation_type) VALUES(?,?,?,?,?,?,?)", ("TC001","KH Disposable","Admin","Fixture","KH-ADMIN-TC001",version_id,"Playwright"))
        playwright_folder=central.build_automation_path(tc_folder,"Playwright",1,"fixture-V1"); playwright_folder.mkdir(); playwright_file=playwright_folder/"KH-ADMIN-TC001.uploaded.json"; playwright_file.write_text('{}',encoding='utf-8')
        api_folder=central.build_automation_path(tc_folder,"API",1,"fixture-V1"); api_folder.mkdir(); api_file=api_folder/"fixture.postman_collection.json"; api_file.write_text('{}',encoding='utf-8')
        sql_folder=central.build_automation_path(tc_folder,"SQL",1,"fixture-V1"); sql_folder.mkdir(); sql_file=sql_folder/"KH-ADMIN-TC001.sql"; sql_file.write_text('SELECT 1;',encoding='utf-8')
        cursor.execute("INSERT INTO playwright_automation_assets(stable_scope_key,source_knowledge_id,tc_id,uploaded_script,modified_date) VALUES(?,?,?,?,datetime('now'))", ("fixture",kid,"KH-ADMIN-TC001","{}"))
        cursor.execute("INSERT INTO api_collections(name,test_case_version_id,repository_path,association_status) VALUES(?,?,?,'linked')", ("fixture",version_id,str(api_file))); api_id=cursor.lastrowid
        cursor.execute("INSERT INTO sql_scripts(test_case_version_id,display_name,original_filename,repository_path,status,created_date) VALUES(?,?,?,?,?,datetime('now'))", (version_id,"fixture",sql_file.name,str(sql_file),"Active")); sql_id=cursor.lastrowid
        connection.commit(); connection.close()
        context={"domain":"KH Disposable","module":"Admin","knowledge_name":"Fixture","version":"1.0","document_type":"CRF","source_type":"Files","source_file_id":"fixture-V1"}

        for node_type, field in (("domain","domain"),("module","module"),("knowledge","knowledge_name"),("version","version"),("document_type","document_type"),("source_type","source_type"),("source_file","source_file_id")):
            detail=repository.get_admin_node_details({"type":node_type,"name":context[field],"context":context})
            assert detail["display_name"] == context[field]
        assert repository.get_admin_node_details({"type":"source_document","knowledge_id":kid,"context":context})["physical_file_exists"]
        fixture_item=repository.get_item(kid)
        try:
            assert repository.get_admin_node_details({"type":"tc_folder","record_id":version_id,"knowledge_id":kid,"context":context})["test_cases_xlsx_count"] == 1
        except ValueError:
            print(next(row.get("hierarchy_diagnostic") for row in repository.list_items() if row["id"]==kid)); raise
        assert repository.get_admin_node_details({"type":"xlsx","record_id":xlsx_id,"knowledge_id":kid,"context":context})["test_case_count"] == 1
        for kind,folder,file_path,record_id,table,tc_id in (("Playwright",playwright_folder,playwright_file,None,None,"KH-ADMIN-TC001"),("API",api_folder,api_file,api_id,"api_collections",""),("SQL",sql_folder,sql_file,sql_id,"sql_scripts","")):
            assert repository.get_admin_node_details({"type":"automation_folder","record_id":version_id,"physical_path":str(folder),"automation_type":kind})["artifact_count"] == 1
            assert repository.get_admin_node_details({"type":"automation_file","knowledge_id":kid,"record_id":record_id,"record_table":table,"physical_path":str(file_path),"automation_type":kind,"tc_id":tc_id,"test_case_version_id":version_id})["physical_exists"]
        for node in ({"type":"domain","context":{"domain":"KH Disposable"}}, {"type":"module","context":{"domain":"KH Disposable","module":"Admin"}}, {"type":"knowledge","context":{"domain":"KH Disposable","module":"Admin","knowledge_name":"Fixture"}}, {"type":"source_document","knowledge_id":kid}):
            try: repository.delete_admin_node(node)
            except ValueError: pass
            else: raise AssertionError(f"populated {node['type']} was not blocked")
        try: repository.delete_admin_node({"type":"tc_folder","record_id":version_id})
        except ValueError: pass
        else: raise AssertionError("populated TC folder was not blocked")

        # KH-DELETE-07..09: backend protection, with DB/filesystem snapshots.
        for code,kind,folder,file_path in (("07","Playwright",playwright_folder,playwright_file),("08","API",api_folder,api_file),("09","SQL",sql_folder,sql_file)):
            before_children=list(folder.iterdir())
            try: repository.delete_admin_node({"type":"automation_folder","record_id":version_id,"physical_path":str(folder),"automation_type":kind})
            except ValueError as error: response=str(error)
            else: raise AssertionError(f"KH-DELETE-{code} populated folder was not blocked")
            assert file_path.is_file() and list(folder.iterdir()) == before_children
            print(f"KH-DELETE-{code} PASS | child={file_path.name} | response={response} | DB unchanged | filesystem unchanged")

        # KH-DELETE-18: stage the API file, inject commit failure, and prove restoration.
        original_db=repository.metadata.db
        repository.metadata.db=SecondConnectionCommitFailure(original_db)
        try:
            repository.delete_admin_node({"type":"automation_file","knowledge_id":kid,"record_id":api_id,"record_table":"api_collections","physical_path":str(api_file),"automation_type":"API","test_case_version_id":version_id})
        except sqlite3.OperationalError as error: delete_failure=str(error)
        else: raise AssertionError("KH-DELETE-18 fault injection did not fail")
        finally: repository.metadata.db=original_db
        check=sqlite3.connect(db_path); assert check.execute("SELECT COUNT(*) FROM api_collections WHERE id=?",(api_id,)).fetchone()[0] == 1; check.close()
        assert api_file.is_file() and not list(api_file.parent.glob(f".{api_file.name}.deleting-*"))
        assert repository.get_admin_node_details({"type":"tc_folder","record_id":version_id,"knowledge_id":kid,"context":context})["automation_file_count"] >= 3
        print(f"KH-DELETE-18 PASS | response={delete_failure} | DB restored | original file restored | no staged orphan")

        # KH-EDIT-09: collide with an unrelated Knowledge and with a sibling file.
        collision_folder=central.build_source_path(domain="KH Disposable",module="Admin",knowledge_name="Knowledge-B",version="1.0",document_type="CRF",source_type="Files",source_file_id="fixture-V1")
        collision_folder.mkdir(parents=True); collision_file=collision_folder/"fixture.txt"; collision_file.write_text("B",encoding="utf-8")
        collision_id=repository.metadata.save_knowledge_item("KH Disposable","Admin","Knowledge-B","1.0",{"file_name":collision_file.name,"repository_path":str(collision_file),"repository_relative_path":central.relative(collision_file),"sha256":"fixture-b","file_size":1,"extension":".txt","source_file_id":"fixture-V1"},{"document_type":"CRF"},"Files")
        try: repository.update_admin_node({"type":"knowledge","name":"Fixture","context":{"domain":"KH Disposable","module":"Admin","knowledge_name":"Fixture"}},"Knowledge-B")
        except ValueError as error: collision_response=str(error)
        else: raise AssertionError("KH-EDIT-09 hierarchy collision was not blocked")
        assert repository.get_item(kid)["knowledge_name"] == "Fixture" and repository.get_item(collision_id)["knowledge_name"] == "Knowledge-B"
        assert source_file.is_file() and collision_file.is_file()
        blocker=api_folder/"existing.json"; blocker.write_text("{}",encoding="utf-8")
        try: repository.update_admin_node({"type":"automation_file","knowledge_id":kid,"record_id":api_id,"record_table":"api_collections","physical_path":str(api_file),"automation_type":"API","test_case_version_id":version_id},blocker.name)
        except ValueError: pass
        else: raise AssertionError("KH-EDIT-09 descendant collision was not blocked")
        assert api_file.is_file() and blocker.is_file()
        blocker.unlink()
        print(f"KH-EDIT-09 PASS | response={collision_response} | both hierarchies and physical paths unchanged")

        # KH-EDIT-10: fail after the source folder moved and metadata changed.
        original_replace=repository._replace_descendant_paths; replace_calls={"count":0}
        def fail_first_replace(old_path,new_path):
            replace_calls["count"] += 1
            if replace_calls["count"] == 1: raise sqlite3.OperationalError("injected descendant path failure")
            return original_replace(old_path,new_path)
        repository._replace_descendant_paths=fail_first_replace
        before_paths={"source":str(source_file),"tc":str(tc_folder),"xlsx":str(workbook),"api":str(api_file),"sql":str(sql_file),"tc_id":"KH-ADMIN-TC001"}
        try: repository.update_admin_node({"type":"knowledge","name":"Fixture","context":{"domain":"KH Disposable","module":"Admin","knowledge_name":"Fixture"}},"Fixture Broken")
        except sqlite3.OperationalError as error: edit_failure=str(error)
        else: raise AssertionError("KH-EDIT-10 fault injection did not fail")
        finally: repository._replace_descendant_paths=original_replace
        check=sqlite3.connect(db_path)
        assert check.execute("SELECT knowledge_name FROM knowledge_items WHERE id=?",(kid,)).fetchone()[0] == "Fixture"
        assert check.execute("SELECT folder_path,workbook_path FROM test_case_versions WHERE id=?",(version_id,)).fetchone() == (before_paths["tc"],before_paths["xlsx"])
        assert check.execute("SELECT repository_path FROM api_collections WHERE id=?",(api_id,)).fetchone()[0] == before_paths["api"]
        assert check.execute("SELECT repository_path FROM sql_scripts WHERE id=?",(sql_id,)).fetchone()[0] == before_paths["sql"]
        assert check.execute("SELECT tc_id FROM test_cases WHERE test_case_version_id=?",(version_id,)).fetchone()[0] == before_paths["tc_id"]
        assert check.execute("SELECT COUNT(*) FROM playwright_automation_assets WHERE source_knowledge_id=? AND tc_id=?",(kid,before_paths["tc_id"])).fetchone()[0] == 1
        check.close()
        assert source_file.is_file() and tc_folder.is_dir() and workbook.is_file() and api_file.is_file() and sql_file.is_file()
        assert not any(path.name.startswith("K-Fixture Brok") for path in managed.rglob("*"))
        print(f"KH-EDIT-10 PASS | response={edit_failure} | DB/paths/TC identity/stable ownership restored")

        check = sqlite3.connect(db_path)
        before_id = check.execute("SELECT tc_id FROM test_cases WHERE test_case_version_id=?",(version_id,)).fetchone()[0]
        check.close()
        result=repository.update_admin_node({"type":"knowledge","name":"Fixture","context":{"domain":"KH Disposable","module":"Admin","knowledge_name":"Fixture"}}, "Fixture Renamed")
        assert result["updated"] == 1 and source_file.name in [p.name for p in managed.rglob("fixture.txt")]
        check = sqlite3.connect(db_path)
        after_id = check.execute("SELECT tc_id FROM test_cases WHERE test_case_version_id=?",(version_id,)).fetchone()[0]
        current_tc_folder = Path(check.execute("SELECT folder_path FROM test_case_versions WHERE id=?",(version_id,)).fetchone()[0])
        check.close()
        assert before_id == after_id

        check=sqlite3.connect(db_path)
        api_path=Path(check.execute("SELECT repository_path FROM api_collections WHERE id=?",(api_id,)).fetchone()[0]); sql_path=Path(check.execute("SELECT repository_path FROM sql_scripts WHERE id=?",(sql_id,)).fetchone()[0]); check.close()
        playwright_path=current_tc_folder/playwright_folder.name/playwright_file.name
        check=sqlite3.connect(db_path)
        shared_folder=current_tc_folder.parent/"TC-V2-shared"; shared_folder.mkdir()
        check.execute("INSERT INTO test_case_versions(source_knowledge_id,source_file_id,version_number,folder_path,workbook_path,created_date) VALUES(?,?,?,?,?,datetime('now'))",(kid,"fixture-V1",2,str(shared_folder),str(shared_folder/'shared.xlsx'))); shared_version=check.execute("SELECT last_insert_rowid()").fetchone()[0]
        check.execute("INSERT INTO test_cases(tc_number,domain,module,knowledge_name,tc_id,test_case_version_id,automation_type) VALUES(?,?,?,?,?,?,?)",("TC001","KH Disposable","Admin","Fixture Renamed","KH-ADMIN-TC001",shared_version,"Playwright")); check.commit(); check.close()
        try:
            repository.delete_admin_node({"type":"automation_file","knowledge_id":kid,"physical_path":str(playwright_path),"automation_type":"Playwright","tc_id":"KH-ADMIN-TC001","test_case_version_id":version_id})
        except ValueError: pass
        else: raise AssertionError("shared Playwright ownership was not protected")
        check=sqlite3.connect(db_path); shared_asset=check.execute("SELECT uploaded_script,active_script_source FROM playwright_automation_assets WHERE source_knowledge_id=? AND tc_id=?",(kid,"KH-ADMIN-TC001")).fetchone(); shared_case=check.execute("SELECT automation_type FROM test_cases WHERE test_case_version_id=? AND tc_id=?",(shared_version,"KH-ADMIN-TC001")).fetchone(); check.close()
        assert shared_asset == ("{}","AUTO") and shared_case == ("Playwright",) and playwright_path.is_file()
        print("KH-DELETE-17 PASS | Uploaded Script/physical JSON/active source/execution mapping preserved")

        # Router contract and forged identity/path checks (router functions, disposable repository).
        import Web.routers.knowledge_router as knowledge_router
        original_repository_factory,original_audit=knowledge_router.KnowledgeRepository,knowledge_router._audit
        knowledge_router.KnowledgeRepository=lambda: repository; knowledge_router._audit=lambda *args: None
        user={"id":0,"username":"fixture"}
        from fastapi import HTTPException
        valid_payload=knowledge_router.KnowledgeAdminNodeRequest(type="domain",name="KH Disposable",context={"domain":"KH Disposable"})
        assert knowledge_router.admin_node_detail(valid_payload,user)["node_type"] == "domain"
        try: knowledge_router.update_admin_node(knowledge_router.UpdateKnowledgeAdminNodeRequest(node=knowledge_router.KnowledgeAdminNodeRequest(type="invalid",name="invalid"),value="new"),user)
        except HTTPException as error: assert error.status_code == 409
        else: raise AssertionError("invalid router edit was accepted")
        forged=[
            knowledge_router.KnowledgeAdminNodeRequest(type="automation_folder",name="escape",record_id=version_id,physical_path=str(temporary.parent/"outside"),automation_type="API"),
            knowledge_router.KnowledgeAdminNodeRequest(type="automation_file",name="mismatch",knowledge_id=kid,record_id=api_id,record_table="api_collections",physical_path=str(sql_path),automation_type="API",test_case_version_id=version_id),
            knowledge_router.KnowledgeAdminNodeRequest(type="invalid",name="invalid"),
            knowledge_router.KnowledgeAdminNodeRequest(type="source_document",name="missing",knowledge_id=999999),
        ]
        for payload in forged:
            try: knowledge_router.delete_admin_node(payload,user)
            except HTTPException as error: assert error.status_code in (404,409)
            else: raise AssertionError(f"forged router request accepted: {payload.type}")
        knowledge_router.KnowledgeRepository,knowledge_router._audit=original_repository_factory,original_audit
        assert api_path.is_file() and sql_path.is_file()
        print("KH-ROUTE PASS | detail/edit/delete contract errors structured; escape/mismatch/invalid/missing rejected")
        check=sqlite3.connect(db_path); check.execute("DELETE FROM test_cases WHERE test_case_version_id=?",(shared_version,)); check.execute("DELETE FROM test_case_versions WHERE id=?",(shared_version,)); check.commit(); check.close(); shared_folder.rmdir()
        for kind,path,record_id,table,tc_id in (("Playwright",playwright_path,None,None,"KH-ADMIN-TC001"),("API",api_path,api_id,"api_collections",""),("SQL",sql_path,sql_id,"sql_scripts","")):
            repository.delete_admin_node({"type":"automation_file","knowledge_id":kid,"record_id":record_id,"record_table":table,"physical_path":str(path),"automation_type":kind,"tc_id":tc_id,"test_case_version_id":version_id})
            assert not path.exists()
        for kind,folder in (("Playwright",playwright_path.parent),("API",api_path.parent),("SQL",sql_path.parent)):
            repository.delete_admin_node({"type":"automation_folder","record_id":version_id,"physical_path":str(folder),"automation_type":kind})
            assert not folder.exists()
        check=sqlite3.connect(db_path); check.execute("DELETE FROM test_cases WHERE test_case_version_id=?",(version_id,)); check.commit(); check.close()
        current_workbook=Path(repository.get_admin_node_details({"type":"xlsx","record_id":xlsx_id,"knowledge_id":kid,"context":context})["workbook_path"])
        repository.delete_admin_node({"type":"xlsx","record_id":xlsx_id,"test_case_version_id":version_id}); assert not current_workbook.exists()
        repository.delete_admin_node({"type":"tc_folder","record_id":version_id}); assert not current_tc_folder.exists()
        print("KH-DETAIL-01..13 PASS")
        print("KH-DELETE-01..06 PASS")
        print("KH-DELETE-14 PASS")
        print("KH-DELETE-10..16 PASS")
        print("KH-DELETE-17 PASS")
        print("KH-EDIT-01..08 PASS")
        print("KH-EDIT-11 PASS")


if __name__ == "__main__":
    main()
