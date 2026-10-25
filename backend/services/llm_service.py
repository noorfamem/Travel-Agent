"""LLM access (OpenAI-compatible chat API: Groq by default, or OpenAI) with Pydantic-validated structured output."""
from __future__ import annotations

import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..config.settings import Settings
from ..utils.errors import ErrorKind, LLMOutputError, ProviderError
from ..utils.http_client import HttpClient
from ..utils.logging_config import get_logger

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)


def extract_json(text: str) -> str:
    """Pull the JSON object out of a model reply (handles code fences and chatter)."""
    t = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise LLMOutputError("no JSON object found in model output")
    return t[start:end + 1]


class LLMService:
    def __init__(self, settings: Settings, http: HttpClient):
        self.s, self.http = settings, http
        self.calls = 0

    @property
    def configured(self) -> bool:
        return self.s.llm_enabled

    @property
    def provider_name(self) -> str:
        return f"{self.s.resolved_llm_provider}:{self.s.resolved_llm_model}"

    def complete(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 1400, temperature: float = 0.2) -> str:
        if not self.configured:
            raise ProviderError("LLM", ErrorKind.NOT_CONFIGURED, "GROQ_API_KEY missing")
        models_to_try = [self.s.resolved_llm_model]
        if self.s.resolved_llm_provider == "groq":
            for m in ("openai/gpt-oss-20b", "qwen/qwen3.8-27b"):
                if m not in models_to_try:
                    models_to_try.append(m)
        last_error = None
        for model_name in models_to_try:
            body = {"model": model_name, "temperature": temperature, "max_tokens": max_tokens, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            try:
                data = self.http.request_json("LLM", "POST", f"{self.s.resolved_llm_base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {self.s.llm_key}"}, timeout=60, retries=1)
                self.calls += 1
                try:
                    msg = data["choices"][0]["message"]
                    return str(msg.get("content") or msg.get("reasoning") or "")
                except (KeyError, IndexError, TypeError):
                    raise ProviderError("LLM", ErrorKind.INVALID_RESPONSE, "missing choices[0].message.content")
            except ProviderError as e:
                last_error = e
                if e.kind in (ErrorKind.BAD_REQUEST, ErrorKind.INVALID_RESPONSE):
                    log.warning("Model %s failed (%s), trying next model...", model_name, e.user_message())
                    continue
                raise
        if last_error:
            raise last_error
        raise ProviderError("LLM", ErrorKind.INVALID_RESPONSE, "No LLM response")

    def complete_model(self, model_cls: Type[T], system: str, user: str, repairs: int = 1, max_tokens: int = 1600) -> T:
        """Ask for JSON, validate with Pydantic, attempt one repair round. Never trusts raw model JSON."""
        prompt, last_err = user, ""
        for attempt in range(repairs + 1):
            raw = self.complete(system, prompt, json_mode=True, max_tokens=max_tokens)
            try:
                return model_cls.model_validate_json(extract_json(raw))
            except (LLMOutputError, ValidationError, ValueError) as exc:
                last_err = str(exc)[:300]
                log.warning("LLM output invalid (attempt %d): %s", attempt + 1, last_err[:120])
                prompt = user + "\n\nYour previous reply was not valid for the required schema (" + last_err + "). Reply again with ONLY one valid JSON object that matches the schema."
        raise LLMOutputError(f"model output failed validation: {last_err}")
