"""
Fundora Auto DD Engine — LLM Orchestrator
==========================================
Manages all AI provider calls with automatic fallback between Gemini and OpenAI.

Routing Strategy:
    ┌─────────────────────┬─────────────┬──────────────┬──────────────┐
    │ Task                │ Both Keys   │ Gemini Only  │ OpenAI Only  │
    ├─────────────────────┼─────────────┼──────────────┼──────────────┤
    │ Layer 2 (Extraction)│ Gemini      │ Gemini       │ OpenAI       │
    │ Layer 4 (Scoring)   │ OpenAI      │ Gemini       │ OpenAI       │
    │ Layer 5 (Narrative) │ OpenAI      │ Gemini       │ OpenAI       │
    └─────────────────────┴─────────────┴──────────────┴──────────────┘

Features:
    - Automatic provider detection from config
    - Retry with exponential backoff (configurable)
    - Structured JSON parsing from LLM responses
    - Graceful degradation when no AI is available
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, Optional

from config import LLMMode, settings

logger = logging.getLogger("auto_dd.llm")


# ---------------------------------------------------------------------------
# LLM Orchestrator
# ---------------------------------------------------------------------------

class LLMOrchestrator:
    """
    Central LLM orchestrator with provider fallback and retry logic.
    
    Usage:
        orchestrator = LLMOrchestrator()
        result = await orchestrator.call_extraction_llm(prompt, context)
        result = await orchestrator.call_scoring_llm(prompt, context)
    """

    def __init__(self):
        self.mode = settings.llm_mode
        self.max_retries = settings.llm_max_retries
        self.base_delay = settings.llm_retry_base_delay
        self._gemini_model = None
        self._openai_client = None

        # Initialize available clients
        if settings.has_gemini:
            self._init_gemini()
        if settings.has_openai:
            self._init_openai()

        logger.info(
            "LLMOrchestrator initialized. Provider mode: %s", self.mode.value
        )

    def _init_gemini(self) -> None:
        """Initialize the Gemini client."""
        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.gemini_api_key)
            self._gemini_model = genai.GenerativeModel(settings.gemini_model)
            logger.info("Gemini client initialized (model: %s)", settings.gemini_model)
        except Exception as e:
            logger.error("Failed to initialize Gemini: %s", e)
            self._gemini_model = None

    def _init_openai(self) -> None:
        """Initialize the OpenAI client."""
        try:
            from openai import OpenAI

            self._openai_client = OpenAI(api_key=settings.openai_api_key)
            logger.info("OpenAI client initialized (model: %s)", settings.openai_model)
        except Exception as e:
            logger.error("Failed to initialize OpenAI: %s", e)
            self._openai_client = None

    # -------------------------------------------------------------------
    # Public API: Task-specific routing
    # -------------------------------------------------------------------

    async def call_extraction_llm(
        self,
        prompt: str,
        context: str = "",
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Layer 2: Document extraction & analysis.
        
        Prefers Gemini (large context window ideal for document intelligence).
        Falls back to OpenAI if Gemini is unavailable.
        
        Args:
            prompt: The formatted prompt template.
            context: Additional context to prepend.
            temperature: LLM temperature (low for factual extraction).
            
        Returns:
            Parsed JSON dict from the LLM response.
        """
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        if self._gemini_model is not None:
            return await self._call_with_retry(
                provider="gemini",
                prompt=full_prompt,
                temperature=temperature,
            )
        elif self._openai_client is not None:
            return await self._call_with_retry(
                provider="openai",
                prompt=full_prompt,
                temperature=temperature,
            )
        else:
            return self._no_provider_fallback("extraction")

    async def call_scoring_llm(
        self,
        prompt: str,
        context: str = "",
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """
        Layer 4/5: Scoring, narrative generation, and strict JSON output.
        
        Prefers OpenAI (gpt-4o excels at structured output and scoring).
        Falls back to Gemini if OpenAI is unavailable.
        
        Args:
            prompt: The formatted prompt template.
            context: Additional context to prepend.
            temperature: LLM temperature (slightly higher for narrative).
            
        Returns:
            Parsed JSON dict from the LLM response.
        """
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        if self._openai_client is not None:
            return await self._call_with_retry(
                provider="openai",
                prompt=full_prompt,
                temperature=temperature,
            )
        elif self._gemini_model is not None:
            # Enforce JSON formatting for Gemini fallback
            gemini_prompt = (
                full_prompt + 
                "\n\nCRITICAL: You are a senior financial analyst AI performing startup due diligence. "
                "Always respond with valid JSON only \u2014 no markdown formatting, no code fences, "
                "no commentary outside the JSON structure."
            )
            return await self._call_with_retry(
                provider="gemini",
                prompt=gemini_prompt,
                temperature=temperature,
            )
        else:
            return self._no_provider_fallback("scoring")

    async def call_verification_llm(
        self,
        prompt: str,
        context: str = "",
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Layer 2: Revenue verification & anomaly detection.
        
        Same routing as extraction (prefers Gemini for large context).
        
        Args:
            prompt: The formatted prompt template.
            context: Additional context to prepend.
            temperature: LLM temperature (very low for factual verification).
            
        Returns:
            Parsed JSON dict from the LLM response.
        """
        return await self.call_extraction_llm(prompt, context, temperature)

    # -------------------------------------------------------------------
    # Provider-specific call implementations
    # -------------------------------------------------------------------

    async def _call_gemini(
        self,
        prompt: str,
        temperature: float = 0.1,
    ) -> str:
        """
        Call the Gemini API and return the raw text response.
        
        Uses GenerativeModel.generate_content() which handles the full
        Gemini API lifecycle.
        """
        import google.generativeai as genai

        generation_config = genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=4096,
            response_mime_type="application/json",
        )

        def _sync_call():
            return self._gemini_model.generate_content(
                prompt,
                generation_config=generation_config,
            )

        try:
            # Wrap synchronous SDK call in an async executor thread with a hard 60s timeout
            response = await asyncio.wait_for(
                asyncio.to_thread(_sync_call),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Gemini API call timed out after 60 seconds")

        # Handle response — Gemini may return multiple candidates
        if response.parts:
            return response.text
        elif response.prompt_feedback:
            raise ValueError(
                f"Gemini blocked the prompt: {response.prompt_feedback}"
            )
        else:
            raise ValueError("Gemini returned an empty response")

    async def _call_openai(
        self,
        prompt: str,
        temperature: float = 0.2,
    ) -> str:
        """
        Call the OpenAI API and return the raw text response.
        
        Uses the chat completions endpoint with a system message for context
        and the prompt as the user message.
        """
        def _sync_call():
            return self._openai_client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior financial analyst AI performing startup "
                            "due diligence. Always respond with valid JSON only — no "
                            "markdown formatting, no code fences, no commentary outside "
                            "the JSON structure."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )

        try:
            # Wrap synchronous SDK call in an async executor thread with a hard 60s timeout
            response = await asyncio.wait_for(
                asyncio.to_thread(_sync_call),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            raise TimeoutError("OpenAI API call timed out after 60 seconds")

        return response.choices[0].message.content

    # -------------------------------------------------------------------
    # Retry Logic
    # -------------------------------------------------------------------

    async def _call_with_retry(
        self,
        provider: str,
        prompt: str,
        temperature: float,
    ) -> Dict[str, Any]:
        """
        Call an LLM provider with exponential backoff retry logic.
        
        Handles:
            - Rate limiting (429 errors)
            - Transient server errors (500/503)
            - JSON parsing failures (retry with cleaner prompt)
        
        Args:
            provider: "gemini" or "openai"
            prompt: The full prompt text.
            temperature: LLM temperature parameter.
            
        Returns:
            Parsed JSON dict from the LLM response.
            
        Raises:
            RuntimeError: If all retries are exhausted.
        """
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                # Make the API call
                if provider == "gemini":
                    raw_response = await self._call_gemini(prompt, temperature)
                else:
                    raw_response = await self._call_openai(prompt, temperature)

                # Parse JSON from response
                parsed = self._parse_json_response(raw_response)

                logger.info(
                    "LLM call succeeded (provider=%s, attempt=%d/%d)",
                    provider, attempt, self.max_retries,
                )
                return parsed

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    "JSON parse error from %s (attempt %d/%d): %s",
                    provider, attempt, self.max_retries, e,
                )
                # On JSON errors, add a hint to the prompt for the retry
                if attempt < self.max_retries:
                    prompt = (
                        prompt + "\n\nCRITICAL: Your previous response was not valid JSON. "
                        "Respond with ONLY a valid JSON object. No markdown, no code fences."
                    )

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Check for rate limiting
                is_rate_limit = any(
                    term in error_str
                    for term in ["rate_limit", "429", "quota", "too many requests"]
                )

                if is_rate_limit:
                    delay = self.base_delay * (2 ** (attempt - 1))  # Exponential backoff
                    logger.warning(
                        "Rate limited by %s. Retrying in %.1fs (attempt %d/%d)",
                        provider, delay, attempt, self.max_retries,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "LLM call failed (provider=%s, attempt=%d/%d): %s",
                        provider, attempt, self.max_retries, e,
                    )
                    if attempt < self.max_retries:
                        time.sleep(self.base_delay)

        # All retries exhausted
        error_msg = (
            f"LLM call failed after {self.max_retries} attempts "
            f"(provider={provider}): {last_error}"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # -------------------------------------------------------------------
    # JSON Parsing
    # -------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(raw_response: str) -> Dict[str, Any]:
        """
        Extract and parse JSON from an LLM response.
        
        Handles common LLM output quirks:
            - JSON wrapped in markdown code fences (```json ... ```)
            - Leading/trailing whitespace and newlines
            - Partial JSON with trailing commas
        """
        if not raw_response:
            raise json.JSONDecodeError("Empty response", "", 0)

        text = raw_response.strip()

        # Remove markdown code fences if present
        # Match ```json ... ``` or ``` ... ```
        code_block_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?\s*```",
            text,
            re.DOTALL,
        )
        if code_block_match:
            text = code_block_match.group(1).strip()

        # Try direct parsing first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object boundaries
        # Look for the outermost { ... }
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            json_candidate = text[brace_start:brace_end + 1]
            try:
                return json.loads(json_candidate)
            except json.JSONDecodeError:
                # Try fixing trailing commas
                fixed = re.sub(r",\s*([}\]])", r"\1", json_candidate)
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass

        # Nothing worked
        raise json.JSONDecodeError(
            f"Could not extract valid JSON from LLM response (length={len(raw_response)})",
            raw_response[:200],
            0,
        )

    # -------------------------------------------------------------------
    # Fallback when no provider is available
    # -------------------------------------------------------------------

    @staticmethod
    def _no_provider_fallback(task_name: str) -> Dict[str, Any]:
        """
        Return a structured error when no LLM provider is available.
        
        This allows the pipeline to continue with degraded results
        rather than crashing entirely.
        """
        logger.error(
            "No LLM provider available for '%s'. "
            "Add GEMINI_API_KEY or OPENAI_API_KEY to .env.",
            task_name,
        )
        return {
            "_error": True,
            "_message": (
                f"No AI provider configured for {task_name}. "
                "AI-dependent layers (2, 4, 5) cannot run. "
                "Add GEMINI_API_KEY and/or OPENAI_API_KEY to your .env file."
            ),
            "_task": task_name,
        }


# ---------------------------------------------------------------------------
# Singleton Instance
# ---------------------------------------------------------------------------

# Lazy initialization — created on first import
_orchestrator: Optional[LLMOrchestrator] = None


def get_orchestrator() -> LLMOrchestrator:
    """
    Get or create the singleton LLMOrchestrator instance.
    
    Lazy initialization avoids import-time API key validation failures
    during testing or when running without .env.
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = LLMOrchestrator()
    return _orchestrator
