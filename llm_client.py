"""OpenAI-compatible LLM client."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from openai import OpenAI, OpenAIError

from exceptions import LLMClientError

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        max_tokens: int,
        timeout: float = 180.0,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = OpenAI(base_url=api_base, api_key=api_key, timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        response_format: dict[str, str] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        start = time.perf_counter()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as e:
            logger.exception(
                "LLM chat failed model=%s",
                self._model,
            )
            raise LLMClientError(str(e)) from e

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        usage = response.usage
        if usage is not None:
            logger.info(
                "LLM chat ok model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s latency_ms=%.2f",
                self._model,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                elapsed_ms,
            )
        else:
            logger.info(
                "LLM chat ok model=%s (no usage) latency_ms=%.2f",
                self._model,
                elapsed_ms,
            )

        choice = response.choices[0]
        content = choice.message.content
        return content if content is not None else ""

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        raw = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "chat_json parse failed model=%s: %s raw_preview=%r",
                self._model,
                e,
                raw[:500] if raw else "",
            )
            return {}

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
    ) -> Any:
        """Stream chat completion, yields text chunks (str)."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
        }
        try:
            stream = self._client.chat.completions.create(**kwargs)
        except OpenAIError as e:
            logger.exception("LLM chat_stream failed model=%s", self._model)
            raise LLMClientError(str(e)) from e
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    def health_check(self) -> tuple[bool, str]:
        try:
            self._client.models.list()
        except OpenAIError as e:
            logger.warning("LLM health_check failed model=%s: %s", self._model, e)
            return False, str(e)
        return True, "ok"
