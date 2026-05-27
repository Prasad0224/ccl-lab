"""
Fundora Auto DD Engine — Configuration
=======================================
Loads environment variables from .env, detects available LLM providers,
and exposes typed settings for the entire application.

Usage:
    from config import settings, LLMMode

    if settings.llm_mode == LLMMode.BOTH:
        # Use Gemini for extraction, OpenAI for scoring
    elif settings.llm_mode == LLMMode.GEMINI_ONLY:
        # Use Gemini for everything
    ...
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("auto_dd.config")

# ---------------------------------------------------------------------------
# LLM Provider Detection
# ---------------------------------------------------------------------------

class LLMMode(str, Enum):
    """Available LLM provider configurations based on detected API keys."""
    BOTH = "both"                # Ideal: Gemini for extraction, OpenAI for scoring
    GEMINI_ONLY = "gemini_only"  # Fallback: Gemini handles all layers
    OPENAI_ONLY = "openai_only"  # Fallback: OpenAI handles all layers
    NONE = "none"                # No keys found — engine cannot run AI layers


# ---------------------------------------------------------------------------
# Application Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """
    Central configuration for the Auto DD engine.
    
    All values can be overridden via environment variables or a .env file.
    Pydantic-settings automatically loads from .env in the project root.
    """

    # --- LLM API Keys ---
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = False

    # --- Upload Limits ---
    max_file_size_mb: int = 50       # Per-file limit in MB
    max_total_upload_mb: int = 200   # Total upload limit in MB

    # --- Temp Storage ---
    upload_temp_dir: str = "tmp_uploads"

    # --- CORS ---
    # Reads from FRONTEND_URL env var; falls back to localhost:3000 for local dev.
    # Set FRONTEND_URL in production to restrict to the deployed frontend origin.
    frontend_url: str = "http://localhost:3000"

    # --- LLM Model Defaults ---
    gemini_model: str = "gemini-1.5-pro"
    openai_model: str = "gpt-4o"

    # --- Retry Configuration ---
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 1.0  # seconds, exponential backoff base

    @field_validator("gemini_api_key", "openai_api_key", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Optional[str]) -> Optional[str]:
        """Treat empty strings and placeholder values as None."""
        if v is None:
            return None
        v = v.strip()
        if not v or v.startswith("your-") or v == "":
            return None
        return v

    @property
    def has_gemini(self) -> bool:
        """Check if a valid Gemini API key is configured."""
        return self.gemini_api_key is not None

    @property
    def has_openai(self) -> bool:
        """Check if a valid OpenAI API key is configured."""
        return self.openai_api_key is not None

    @property
    def llm_mode(self) -> LLMMode:
        """Detect which LLM providers are available based on API keys."""
        if self.has_gemini and self.has_openai:
            return LLMMode.BOTH
        elif self.has_gemini:
            return LLMMode.GEMINI_ONLY
        elif self.has_openai:
            return LLMMode.OPENAI_ONLY
        else:
            return LLMMode.NONE

    @property
    def cors_origins(self) -> list[str]:
        """CORS allowed origins. Reads FRONTEND_URL env var; defaults to localhost:3000."""
        origins = [self.frontend_url]
        # Also allow the backend itself for health-check tooling
        if self.host != "0.0.0.0":
            origins.append(f"http://{self.host}:{self.port}")
        return origins

    @property
    def max_file_size_bytes(self) -> int:
        """Per-file upload limit in bytes."""
        return self.max_file_size_mb * 1024 * 1024

    @property
    def max_total_upload_bytes(self) -> int:
        """Total upload limit in bytes."""
        return self.max_total_upload_mb * 1024 * 1024

    @property
    def temp_dir(self) -> Path:
        """Resolved path to the temporary upload directory."""
        return Path(self.upload_temp_dir)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # Allow extra fields in .env without raising errors
        extra = "ignore"


# ---------------------------------------------------------------------------
# Singleton Settings Instance
# ---------------------------------------------------------------------------

def _load_settings() -> Settings:
    """
    Load settings from environment / .env file.
    
    Gracefully handles the case where no .env file exists (e.g., CI/CD or
    Docker environments where env vars are injected directly).
    """
    try:
        s = Settings()
    except Exception as e:
        logger.warning("Failed to load .env file, using defaults: %s", e)
        s = Settings(_env_file=None)

    # Log provider detection on startup
    mode = s.llm_mode
    if mode == LLMMode.BOTH:
        logger.info(
            "✅ LLM Config: BOTH providers detected. "
            "Gemini → Layer 2 (extraction), OpenAI → Layer 4/5 (scoring/narrative)."
        )
    elif mode == LLMMode.GEMINI_ONLY:
        logger.info(
            "⚠️  LLM Config: Gemini ONLY. All AI layers will use Gemini (%s).",
            s.gemini_model,
        )
    elif mode == LLMMode.OPENAI_ONLY:
        logger.info(
            "⚠️  LLM Config: OpenAI ONLY. All AI layers will use OpenAI (%s).",
            s.openai_model,
        )
    else:
        logger.error(
            "❌ LLM Config: NO API KEYS FOUND. AI layers (2, 4, 5) will fail. "
            "Add GEMINI_API_KEY and/or OPENAI_API_KEY to your .env file."
        )

    return s


settings = _load_settings()
