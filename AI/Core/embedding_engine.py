"""
QA AI Studio
Embedding Engine
Version: 2.0
"""

import os

from sentence_transformers import SentenceTransformer

from Config.settings import (
    HF_HOME,
    EMBEDDING_MODEL
)

from Core.logger import Logger


class EmbeddingEngine:

    _model = None
    _cache_folder = None

    def __init__(self):

        self.logger = Logger.get_logger()

        if EmbeddingEngine._model is None:

            project_root = os.path.dirname(
                os.path.dirname(__file__)
            )

            hf_cache = os.path.join(
                project_root,
                HF_HOME
            )

            os.makedirs(
                hf_cache,
                exist_ok=True
            )

            os.environ["HF_HOME"] = hf_cache

            self.logger.info(
                f"Loading embedding model: {EMBEDDING_MODEL}"
            )

            EmbeddingEngine._model = SentenceTransformer(
                EMBEDDING_MODEL,
                cache_folder=hf_cache
            )

            EmbeddingEngine._cache_folder = hf_cache

            self.logger.info(
                "Embedding model loaded successfully."
            )

        self.model = EmbeddingEngine._model
        self.cache_folder = EmbeddingEngine._cache_folder

    # --------------------------------------------------
    # Generate Embedding
    # --------------------------------------------------

    def generate_embedding(
        self,
        text
    ):

        try:

            return self.model.encode(
                text,
                convert_to_numpy=True
            )

        except Exception as error:

            self.logger.error(
                f"Embedding generation failed: {error}"
            )

            return None

    # --------------------------------------------------
    # Generate Multiple Embeddings
    # --------------------------------------------------

    def generate_embeddings(
        self,
        texts
    ):

        try:

            return self.model.encode(
                texts,
                convert_to_numpy=True
            )

        except Exception as error:

            self.logger.error(
                f"Batch embedding failed: {error}"
            )

            return None

    # --------------------------------------------------
    # Model Information
    # --------------------------------------------------

    def get_model_name(self):

        return EMBEDDING_MODEL

    # --------------------------------------------------
    # Cache Information
    # --------------------------------------------------

    def get_cache_folder(self):

        return self.cache_folder