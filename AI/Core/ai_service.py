"""
AI Configuration
Enterprise QA AI Agent
"""

# =====================================================
# AI MODE
#
# cloud   = ChatGPT / Gemini / Claude
# local   = Ollama
# hybrid  = Local + Cloud
# disabled= Rule Engine only
# =====================================================

AI_MODE = "disabled"

# =====================================================
# Cloud Provider
# =====================================================

CLOUD_PROVIDER = "chatgpt"

# =====================================================
# Local Provider
# =====================================================

LOCAL_PROVIDER = "ollama"

# =====================================================
# Local Knowledge
# =====================================================

USE_LOCAL_KNOWLEDGE = True

# =====================================================
# Allow sending repository context
# =====================================================

ALLOW_CLOUD_CONTEXT = False