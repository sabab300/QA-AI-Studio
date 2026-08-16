"""
QA AI Studio - Knowledge Hub Database Persistence
Location: App/UI/KnowledgeHub/knowledge_db_sync.py
"""

import logging

logger = logging.getLogger(__name__)


def save_business_hierarchy_to_db(db_conn, hierarchy_data: dict) -> dict:
    """
    Inserts or updates Domain, Business Process, Variant, and Discovered Elements
    into PostgreSQL inside a transaction block.
    """
    if not db_conn:
        return {"success": False, "error": "Database connection is not available."}

    app_name = hierarchy_data.get("application_name")
    domain_name = hierarchy_data.get("domain_name")
    process_name = hierarchy_data.get("business_process")
    variant_name = hierarchy_data.get("variant_name")
    raw_data = hierarchy_data.get("raw_discovery", {})

    try:
        cursor = db_conn.cursor()

        # 1. Ensure Domain Record Exists
        cursor.execute("""
            INSERT INTO domains (name, application_name)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET application_name = EXCLUDED.application_name
            RETURNING id;
        """, (domain_name, app_name))
        domain_id = cursor.fetchone()[0]

        # 2. Ensure Business Process Record Exists
        cursor.execute("""
            INSERT INTO business_processes (domain_id, name)
            VALUES (%s, %s)
            ON CONFLICT (domain_id, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id;
        """, (domain_id, process_name))
        process_id = cursor.fetchone()[0]

        # 3. Create or Update Process Variant
        cursor.execute("""
            INSERT INTO process_variants (business_process_id, variant_name, url)
            VALUES (%s, %s, %s)
            RETURNING id;
        """, (process_id, variant_name, raw_data.get("url", "")))
        variant_id = cursor.fetchone()[0]

        # 4. Insert URL Knowledge Summary Record
        total_elements = len(raw_data.get("fields", [])) + len(raw_data.get("buttons", []))
        total_tabs = len(raw_data.get("tabs", []))
        
        cursor.execute("""
            INSERT INTO url_knowledge_records (application_name, business_process_name, page_url, tab_count, element_count)
            VALUES (%s, %s, %s, %s, %s);
        """, (app_name, process_name, raw_data.get("url", ""), total_tabs, total_elements))

        # Commit Transaction
        db_conn.commit()
        logger.info(f"Successfully saved hierarchy: App={app_name}, Process={process_name}, Elements={total_elements}")

        return {
            "success": True,
            "domain_id": domain_id,
            "process_id": process_id,
            "variant_id": variant_id,
            "element_count": total_elements
        }

    except Exception as ex:
        db_conn.rollback()
        logger.exception("Failed to store business hierarchy in database.")
        return {"success": False, "error": str(ex)}