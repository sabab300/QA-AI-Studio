"""
==========================================================
QA AI Studio
Global Configuration
Version: 2.1
==========================================================

Central configuration for the Enterprise QA AI Studio.

Do NOT hardcode configuration values anywhere else.
Always import them from this module.
"""

# ==========================================================
# AI MODE
# ==========================================================

AI_MODE = "local"

# ==========================================================
# AI PROVIDERS
# ==========================================================

LOCAL_PROVIDER = "ollama"

CLOUD_PROVIDER = "chatgpt"

# ==========================================================
# LOCAL AI
# ==========================================================

OLLAMA_URL = "http://localhost:11434/api/generate"

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

LLM_MODEL = "qwen2.5:7b"

LLM_TEMPERATURE = 0.2

LLM_MAX_TOKENS = 2048

LLM_TIMEOUT = 300

# ==========================================================
# AI MODELS
# ==========================================================

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

MULTILINGUAL_MODEL = "intfloat/multilingual-e5-base"

# ==========================================================
# PERFORMANCE
# ==========================================================

DEFAULT_TOP_K = 5

DEFAULT_MAX_TOKENS = 2048

DEFAULT_TEMPERATURE = 0.2

DEFAULT_TIMEOUT = 300


# AI Performance Profile
#
# quality  = Maximum accuracy (more context, slower)
# balanced = Recommended production mode
# fast     = Faster response, less context

AI_PERFORMANCE_MODE = "balanced"

# ==========================================================
# TEST CASE GENERATOR
# ==========================================================

DEFAULT_TEST_CASE_COUNT = 10

MAX_TEST_CASE_COUNT = 20

# ==========================================================
# RAG / KNOWLEDGE
# ==========================================================

USE_LOCAL_KNOWLEDGE = True

ALLOW_CLOUD_CONTEXT = False

TOP_K_RESULTS = 5

MAX_CONTEXT_CHUNKS = 3

MAX_CONTEXT_CHARACTERS = 7000

# ==========================================================
# CHROMADB
# ==========================================================

CHROMA_COLLECTION = "psw_knowledge"

# ==========================================================
# DATABASE
# ==========================================================

DATABASE_NAME = "metadata.db"

# ==========================================================
# REPOSITORY
# ==========================================================

REPOSITORY_FOLDER = "Repository"

EMBED_ONLY_NEW_FILES = True

DUPLICATE_CHECK_SHA256 = True

# ==========================================================
# MEMORY
# ==========================================================

ENABLE_MEMORY = True

MAX_MEMORY_RECORDS = 1000

# ==========================================================
# LOGGING
# ==========================================================

ENABLE_LOGGING = True

LOG_LEVEL = "INFO"

# ==========================================================
# LLM PERFORMANCE PROFILE
# ==========================================================

LLM_PROFILES = {

    "quality": {

        "max_tokens": 2048,

        "temperature": 0.2

    },

    "balanced": {

        "max_tokens": 768,

        "temperature": 0.2

    },

    "fast": {

        "max_tokens": 384,

        "temperature": 0.1

    }

}

# Development:
# fast

# Production:
# balanced
# quality

LLM_PROFILE = "fast"

PROMPT_PROFILE = LLM_PROFILE

# ==========================================================
# Hugging Face
# ==========================================================

HF_HOME = "Models/huggingface"