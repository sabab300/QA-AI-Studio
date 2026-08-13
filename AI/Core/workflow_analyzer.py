# AI/Core/workflow_analyzer.py
import json
from typing import List, Dict, Any

class WorkflowAnalyzer:
    """Analyzes UI discovery trajectories to build dependency graphs and sequence steps."""

    def __init__(self, db_manager):
        self.db = db_manager

    def extract_navigation_sequence(self, raw_discovered_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Organizes discovered UI elements into sequential workflow steps and maps dependencies."""
        sorted_steps = sorted(raw_discovered_nodes, key=lambda x: x.get("step_order", 0))
        
        sequence = []
        for index, node in enumerate(sorted_steps, start=1):
            sequence.append({
                "step": index,
                "page_name": node.get("page_name", f"Page {index}"),
                "url": node.get("url"),
                "action_elements": [
                    el for el in node.get("elements", []) 
                    if el.get("elementType") in ["button", "submit", "link"]
                ],
                "required_inputs": [
                    el for el in node.get("elements", []) 
                    if "required" in el.get("validationRules", [])
                ]
            })

        workflow_graph = {
            "business_process": sorted_steps[0].get("business_process", "Default Process") if sorted_steps else "Unknown",
            "start_url": sorted_steps[0].get("url") if sorted_steps else "",
            "end_url": sorted_steps[-1].get("url") if sorted_steps else "",
            "total_steps": len(sequence),
            "sequence": sequence
        }
        return workflow_graph

    def save_workflow(self, workflow_data: Dict[str, Any]):
        """Persists the identified workflow sequence into SQLite storage."""
        query = """
        INSERT INTO workflow_dependencies (id, business_process, start_url, end_url, step_sequence_json)
        VALUES (?, ?, ?, ?, ?)
        """
        import uuid
        self.db.execute(query, (
            str(uuid.uuid4()),
            workflow_data["business_process"],
            workflow_data["start_url"],
            workflow_data["end_url"],
            json.dumps(workflow_data["sequence"])
        ))