"""Text embedding with provider fallback chain.

Primary:   Google Gemini ``text-embedding-004`` (free tier generous, OpenAI-compat endpoint)
Fallback:  Alibaba DashScope ``text-embedding-v4``

Provider is read from ``models.yaml::embedding.provider`` (passed via
:class:`EmbeddingConfig`). If the primary provider's API key is missing
or the call fails, automatically falls back to the secondary provider.

Security:
- API keys are read from environment, never logged.
- ``--show-key`` style debug paths intentionally not exposed.
- Errors are sanitized before logging (no key leakage).
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

import httpx
import numpy as np

from ..schemas import TweetEmbedded, TweetRaw

logger = logging.getLogger(__name__)

GOOGLE_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/openai/embeddings"
DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"

# Per-provider batch limits & default models
PROVIDER_DEFAULTS = {
    "google": {
        "url": GOOGLE_EMBED_URL,
        "key_env": "GOOGLE_API_KEY",
        "model": "gemini-embedding-001",   # 3072 dim, MRL-supported
        "dimensions": 3072,
        "batch_size": 100,   # Google supports larger batches
    },
    "dashscope": {
        "url": DASHSCOPE_EMBED_URL,
        "key_env": "DASHSCOPE_API_KEY",
        "model": "text-embedding-v4",
        "dimensions": 1024,
        "batch_size": 10,
    },
}

# Fallback chain order: try primary, then this
FALLBACK_CHAIN = ["google", "dashscope"]


def _sanitize_error(text: str, key: str | None) -> str:
    """Strip the API key from any error message before it hits logs."""
    if not text:
        return ""
    if key:
        text = text.replace(key, "<REDACTED_KEY>")
    return text[:500]  # cap length too


class Embedder:
    """Batch-embed tweets with multi-provider fallback.

    Args:
        provider: 'google' or 'dashscope'. Determines primary path.
        model:    override default model for the provider.
        dimensions: only honored if provider supports custom dim (DashScope does;
                    Google's text-embedding-004 is fixed at 768).
        api_key:  override env-derived key (mostly for testing).
        enable_fallback: if True, on primary failure try other providers.
    """

    def __init__(
        self,
        provider: str = "google",
        model: str | None = None,
        dimensions: int | None = None,
        api_key: str | None = None,
        enable_fallback: bool = True,
    ):
        if provider not in PROVIDER_DEFAULTS:
            raise ValueError(f"Unknown embedding provider: {provider}")
        self.provider = provider
        defaults = PROVIDER_DEFAULTS[provider]
        self.model = model or defaults["model"]
        self.dimensions = dimensions or defaults["dimensions"]
        self._api_key_override = api_key
        self.enable_fallback = enable_fallback

    # ---------------------------------------------------------------- public

    def embed_tweets(self, tweets: list[TweetRaw]) -> list[TweetEmbedded]:
        if not tweets:
            return []

        texts = [t.text for t in tweets]
        all_embeddings = self._batch_embed_with_fallback(texts)

        results = [TweetEmbedded(tweet=t, embedding=emb)
                   for t, emb in zip(tweets, all_embeddings)]
        logger.info(
            "Embedded %d tweets via %s/%s (%d dim)",
            len(results), self.provider, self.model, len(all_embeddings[0]) if all_embeddings else 0,
        )
        return results

    # --------------------------------------------------------------- internals

    def _batch_embed_with_fallback(self, texts: Sequence[str]) -> list[list[float]]:
        """Try primary provider; on failure, walk the fallback chain."""
        chain = [self.provider]
        if self.enable_fallback:
            chain.extend(p for p in FALLBACK_CHAIN if p != self.provider)

        last_error: Exception | None = None
        for provider in chain:
            try:
                logger.info("Embedding via %s", provider)
                return self._batch_embed(texts, provider)
            except Exception as e:
                logger.warning("Embedding via %s failed: %s", provider,
                               _sanitize_error(str(e), self._get_key(provider)))
                last_error = e
                continue
        raise RuntimeError(f"All embedding providers failed. Last: {last_error}")

    def _batch_embed(self, texts: Sequence[str], provider: str) -> list[list[float]]:
        cfg = PROVIDER_DEFAULTS[provider]
        bs = cfg["batch_size"]
        all_embs: list[list[float]] = []
        for i in range(0, len(texts), bs):
            batch = texts[i : i + bs]
            embs = self._call_api(batch, provider)
            all_embs.extend(embs)
        return all_embs

    def _get_key(self, provider: str) -> str | None:
        if self._api_key_override and provider == self.provider:
            return self._api_key_override
        env_key = PROVIDER_DEFAULTS[provider]["key_env"]
        return os.environ.get(env_key)

    def _call_api(self, texts: list[str], provider: str) -> list[list[float]]:
        cfg = PROVIDER_DEFAULTS[provider]
        api_key = self._get_key(provider)
        if not api_key:
            raise RuntimeError(f"Missing API key env: {cfg['key_env']}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Both Google (OpenAI-compat) and DashScope use the same payload shape.
        payload: dict = {
            "model": cfg["model"] if provider != self.provider else self.model,
            "input": texts,
            "encoding_format": "float",
        }
        # DashScope honors dimensions param; Google text-embedding-004 ignores
        if provider == "dashscope":
            payload["dimensions"] = self.dimensions

        try:
            resp = httpx.post(
                cfg["url"], json=payload, headers=headers, timeout=30,
            )
        except httpx.RequestError as e:
            raise RuntimeError(f"{provider} request failed: {_sanitize_error(str(e), api_key)}")

        if resp.status_code != 200:
            # Sanitize error text before logging
            err_body = _sanitize_error(resp.text, api_key)
            logger.error("%s embedding error %d: %s", provider, resp.status_code, err_body)
            raise RuntimeError(f"{provider} HTTP {resp.status_code}: {err_body}")

        data = resp.json()
        embeddings_data = sorted(data["data"], key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in embeddings_data]

    # ------------------------------------------------------- legacy / utility

    @staticmethod
    def cosine_similarity_matrix(embeddings: list[list[float]]) -> np.ndarray:
        """Compute pairwise cosine similarity matrix."""
        arr = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normalized = arr / norms
        return normalized @ normalized.T
