"""
QA AI Studio -- AI Platform (Task 4)

Provider gateway, routing, RAG pipeline, token budgeting, caching and
plugin/MCP tool layer for the AI Assistant.

Architecture rule (Task 4, Phase 2): the answer-generating LLM is an
independent, swappable layer.  Nothing in this package writes to, rebuilds
or re-embeds the knowledge repository / ChromaDB collection; switching the
LLM only changes which provider receives the already-retrieved context.

Runtime configuration lives in its own SQLite file
(Database/ai_platform.db) so that no table is added to, or migrated in,
metadata.db.
"""

from Core.ai_platform.constants import (  # noqa: F401
    DATA_CLASSES, MODES, TASKS, PROVIDER_KINDS, ADAPTERS,
)
