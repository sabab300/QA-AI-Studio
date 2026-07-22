"""
QA AI Studio
Enterprise Exception Framework
Version: 1.0
"""


class QAAIStudioException(Exception):
    """Base exception for QA AI Studio."""

    def __init__(self, message="QA AI Studio Error"):
        super().__init__(message)


# --------------------------------------------------
# Configuration
# --------------------------------------------------

class ConfigurationError(QAAIStudioException):
    """Configuration related errors."""
    pass


# --------------------------------------------------
# Database
# --------------------------------------------------

class DatabaseError(QAAIStudioException):
    """SQLite / Database related errors."""
    pass


# --------------------------------------------------
# Repository
# --------------------------------------------------

class RepositoryError(QAAIStudioException):
    """Repository related errors."""
    pass


# --------------------------------------------------
# Metadata
# --------------------------------------------------

class MetadataError(QAAIStudioException):
    """Metadata processing errors."""
    pass


# --------------------------------------------------
# File Extraction
# --------------------------------------------------

class ExtractionError(QAAIStudioException):
    """Text extraction errors."""
    pass


# --------------------------------------------------
# Knowledge Analysis
# --------------------------------------------------

class KnowledgeAnalysisError(QAAIStudioException):
    """Knowledge analysis errors."""
    pass


# --------------------------------------------------
# Embedding
# --------------------------------------------------

class EmbeddingError(QAAIStudioException):
    """Embedding generation errors."""
    pass


# --------------------------------------------------
# Vector Database
# --------------------------------------------------

class VectorStoreError(QAAIStudioException):
    """Vector database errors."""
    pass


# --------------------------------------------------
# AI Models
# --------------------------------------------------

class AIModelError(QAAIStudioException):
    """AI model related errors."""
    pass


# --------------------------------------------------
# Validation
# --------------------------------------------------

class ValidationError(QAAIStudioException):
    """Validation errors."""
    pass