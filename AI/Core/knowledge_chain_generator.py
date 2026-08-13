# AI/Core/knowledge_chain_generator.py
import json

class KnowledgeChainGenerator:
    """Fetches exact 8-level hierarchy UI metadata and formats Playwright/Selenium scripts."""

    def __init__(self, db_manager):
        self.db = db_manager

    def fetch_hierarchy_for_process(self, application: str, business_process: str) -> list:
        query = """
        SELECT application_name, business_process, variant_name, page_name, tab_name, 
               section_name, element_name, element_type, locator_primary, placeholder_text, workflow_step_order, page_url
        FROM url_knowledge_hierarchy
        WHERE application_name = ? AND business_process = ?
        ORDER BY workflow_step_order ASC
        """
        return self.db.fetchall(query, (application, business_process))

    def generate_playwright_python(self, application: str, business_process: str) -> str:
        records = self.fetch_hierarchy_for_process(application, business_process)
        if not records:
            return f"# No hierarchy knowledge found for {application} -> {business_process}"

        script_lines = [
            "import pytest",
            "from playwright.sync_api import Page, expect\n",
            f"def test_{application.lower()}_{business_process.lower().replace(' ', '_')}(page: Page):"
        ]

        current_url = None
        for rec in records:
            (app, proc, variant, page_name, tab, section, el_name, el_type, locator, placeholder, step_order, page_url) = rec

            if page_url != current_url:
                script_lines.append(f"    # Navigate to {page_name} ({variant})")
                script_lines.append(f"    page.goto('{page_url}')")
                current_url = page_url

            comment_context = f"    # [{tab or 'Main'}] -> [{section or 'General'}] -> {el_name}"
            script_lines.append(comment_context)

            if el_type in ["input", "textarea"]:
                script_lines.append(f"    page.fill('{locator}', 'Test Data')")
            elif el_type in ["button", "submit", "tab", "link"]:
                script_lines.append(f"    page.click('{locator}')")
            elif el_type == "select":
                script_lines.append(f"    page.select_option('{locator}', index=1)")

        return "\n".join(script_lines)