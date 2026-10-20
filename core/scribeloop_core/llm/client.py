"""OpenAI-compatible LLM client.

Endpoints (§10 decision log): llama.cpp embedded in the GPU worker (default),
an Ollama/llama.cpp server, or a cloud provider — all behind this one client.

The client is LLM-agnostic; prompt templates live in ``prompts/``. All summary
text produced here is UNTRUSTED (second-order injection, §5): consumers treat
it as data, never as user intent.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import OpenAI

from .schemas import Summary

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "local",
        api_key: str = "scribeloop-local",
        temperature: float = 0.2,
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.temperature = temperature

    def _chat(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise LLMError("empty completion")
        return content

    def summarize(self, transcript_md: str, prompt_name: str = "final_summary") -> Summary:
        """Structured summary from a transcript. Output validated by pydantic."""
        system = (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8")
        raw = self._chat(system, transcript_md)
        # Models wrap JSON in fences often enough to handle it uniformly.
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return Summary.model_validate_json(text)
        except Exception as e:
            raise LLMError(f"model returned invalid Summary JSON: {e}\n---\n{raw}") from e

    def health(self) -> bool:
        try:
            self._client.models.list()
            return True
        except Exception:
            return False
