"""Shared vocabulary for the AI platform."""

MODES = ("auto", "local", "global", "hybrid")

# Tasks a model can be assigned to (Settings -> LLM -> Models / AI Routing).
TASKS = (
    "answer",           # Ask Knowledge (LOCAL / RAG)
    "global_answer",    # GLOBAL (internet context) answers
    "hybrid_answer",    # HYBRID answers
    "impact_analysis",  # Impact Analysis extraction / matching
    "test_case",        # test-case generation
    "api",              # API test generation
    "sql",              # SQL generation
    "bug",              # bug-report generation
    "automation",       # automation script generation
    "tool_planner",     # choosing which plugin/MCP tool to call
)

# private  = the prompt contains knowledge-base / internal QA data
# public   = the prompt contains only the user's question and public web text
# none     = fixed diagnostic prompt (connection / model test)
DATA_CLASSES = ("private", "public", "none")

PROVIDER_KINDS = ("local", "cloud")

# adapter id -> (default kind, default base url, needs api key)
ADAPTERS = {
    "ollama":       ("local", "http://localhost:11434", False),
    "lmstudio":     ("local", "http://localhost:1234/v1", False),
    "openai_compatible": ("local", "http://localhost:8080/v1", False),
    "openai":       ("cloud", "https://api.openai.com/v1", True),
    "anthropic":    ("cloud", "https://api.anthropic.com", True),
    "gemini":       ("cloud", "https://generativelanguage.googleapis.com", True),
    "openrouter":   ("cloud", "https://openrouter.ai/api/v1", True),
}

ADAPTER_LABELS = {
    "ollama": "Ollama (local)",
    "lmstudio": "LM Studio (local, OpenAI-compatible API)",
    "openai_compatible": "Other OpenAI-compatible server",
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
}

# Settings sections stored in llm_settings.
SECTIONS = ("routing", "rag", "tokens", "security")

DEFAULT_SETTINGS = {
    "routing": {
        "default_mode": "auto",
        "model_mode": "auto",
        "default_provider_id": None,
        "default_model": "",
        "task_routes": {},
        "fallback": {"enabled": False, "chain": [], "allow_cloud_fallback": False},
        "internet_search_enabled": True,
        # when GLOBAL (and HYBRID) may use live search:
        #   when_needed - only for current-information questions or an explicit web request
        #   always      - every GLOBAL question is searched (first-build behaviour)
        #   never       - the selected model answers from its own knowledge
        "global_search_policy": "when_needed",
        "hybrid_use_internet": True,
        "allow_manual_model_selection": True,
    },
    "rag": {
        "enhanced_pipeline": True,
        "top_k": 6,
        "candidate_pool": 24,
        "similarity_threshold": 0.30,
        "min_best_similarity": 0.38,
        "rerank_enabled": True,
        "context_chars": 9000,
        "max_chunks_per_document": 3,
        "citations_required": True,
        "grounding_check": True,
        "diagnostics_enabled": True,
        "global_context_chars": 4000,
        # AUTO routing probe for ambiguous questions (see evidence.assess_probe)
        "probe_min_relevance": 0.5,
        "probe_min_keyword_coverage": 0.6,
    },
    "tokens": {
        "input_budget_tokens": 3500,
        "output_budget_tokens": 1024,
        "context_optimization": True,
        "conversation_turns": 4,
        "summarize_history": True,
        "history_summary_chars": 600,
        "exact_cache": {"enabled": True, "ttl_s": 600, "max_items": 200},
        "semantic_cache": {"enabled": False, "threshold": 0.97, "ttl_s": 600},
        "retrieval_filtering": True,
        "usage_tracking": True,
        "retries": 1,
        "request_timeout_s": 300,
    },
    "security": {
        "cloud_data_sharing_enabled": False,
        "redact_cloud_prompts": True,
        "audit_enabled": True,
        "ai_tools_enabled": True,
        "mcp_allowed_commands": ["npx", "node", "python", "python3", "py", "uvx", "uv", "deno", "bun"],
        "tool_max_calls": 3,
    },
}

# Bounds used when validating settings written through the API.
SETTING_BOUNDS = {
    ("rag", "top_k"): (1, 20),
    ("rag", "candidate_pool"): (1, 100),
    ("rag", "similarity_threshold"): (0.0, 1.0),
    ("rag", "min_best_similarity"): (0.0, 1.0),
    ("rag", "context_chars"): (500, 40000),
    ("rag", "max_chunks_per_document"): (1, 20),
    ("rag", "global_context_chars"): (500, 20000),
    ("rag", "probe_min_relevance"): (0.0, 1.0),
    ("rag", "probe_min_keyword_coverage"): (0.0, 1.0),
    ("tokens", "input_budget_tokens"): (256, 200000),
    ("tokens", "output_budget_tokens"): (16, 32000),
    ("tokens", "conversation_turns"): (0, 20),
    ("tokens", "history_summary_chars"): (0, 4000),
    ("tokens", "retries"): (0, 3),
    ("tokens", "request_timeout_s"): (5, 1800),
    ("security", "tool_max_calls"): (1, 5),
}
