"""Ollama/DeepSeek 统一模型接口与结构化输出校验。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMUnavailable(RuntimeError):
    """模型服务不可用或没有有效配置。"""


@dataclass
class ResearchPrompt:
    question: str
    context: dict[str, Any] = field(default_factory=dict)
    source_requirements: list[str] = field(default_factory=list)


@dataclass
class EvidenceResponse:
    facts: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    synthesis: list[str] = field(default_factory=list)
    raw_text: str = ""


class LLMProvider(Protocol):
    name: str
    model: str

    def research_evidence(self, request: ResearchPrompt) -> EvidenceResponse: ...

    def synthesize_report(self, request: ResearchPrompt) -> EvidenceResponse: ...


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S | re.I)
    if fenced:
        cleaned = fenced.group(1)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回 JSON 对象")
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型返回的 JSON 顶层必须是对象")
    return payload


def validate_evidence(payload: dict[str, Any], raw_text: str = "") -> EvidenceResponse:
    facts: list[dict[str, Any]] = []
    invalid = []
    for item in payload.get("facts", []) or []:
        if not isinstance(item, dict):
            invalid.append("事实项不是对象")
            continue
        source_url = item.get("source_url")
        claim = item.get("claim")
        if not isinstance(source_url, str) or not source_url.strip() or not isinstance(claim, str) or not claim.strip():
            invalid.append("事实缺少 claim 或 source_url，已丢弃")
            continue
        facts.append({
            "claim": claim.strip(),
            "value": item.get("value"),
            "source_url": source_url.strip(),
            "as_of": item.get("as_of"),
            "official": bool(item.get("official", False)),
            "confidence": item.get("confidence"),
        })
    missing = [str(item) for item in (payload.get("missing_items", []) or [])]
    missing.extend(invalid)
    return EvidenceResponse(
        facts=facts,
        conflicts=list(payload.get("conflicts", []) or []),
        missing_items=missing,
        synthesis=[str(item) for item in (payload.get("synthesis", []) or [])],
        raw_text=raw_text,
    )


class _JsonChatProvider:
    name = ""

    def __init__(self, model: str, timeout: int = 90) -> None:
        self.model = model
        self.timeout = timeout

    def _complete(self, prompt: ResearchPrompt) -> EvidenceResponse:
        raw_text = self._call(prompt)
        return validate_evidence(_extract_json(raw_text), raw_text)

    def research_evidence(self, request: ResearchPrompt) -> EvidenceResponse:
        return self._complete(request)

    def synthesize_report(self, request: ResearchPrompt) -> EvidenceResponse:
        return self._complete(request)

    def _call(self, prompt: ResearchPrompt) -> str:
        raise NotImplementedError

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是投前研究证据整理器。只返回合法 JSON，不要 Markdown。"
            "事实必须包含可核验 source_url；没有可靠来源就放入 missing_items，不能猜测。"
            "不要重新计算输入中的定量指标，不要给出交易指令。"
        )

    @classmethod
    def _user_prompt(cls, prompt: ResearchPrompt) -> str:
        return json.dumps({
            "question": prompt.question,
            "context": prompt.context,
            "source_requirements": prompt.source_requirements,
            "output_schema": {
                "facts": [{
                    "claim": "字符串",
                    "value": "事实值",
                    "source_url": "可核验 URL",
                    "as_of": "数据截至日期",
                    "official": True,
                    "confidence": 0.0,
                }],
                "conflicts": [],
                "missing_items": [],
                "synthesis": [],
            },
        }, ensure_ascii=False)


class OllamaProvider(_JsonChatProvider):
    name = "ollama"

    def __init__(self, model: str = "deepseek-r1:14b", base_url: str | None = None, timeout: int = 90) -> None:
        super().__init__(model, timeout)
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")

    def _call(self, prompt: ResearchPrompt) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(prompt)},
            ],
        }
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise LLMUnavailable(f"Ollama 调用失败：{exc}") from exc
        return str(payload.get("message", {}).get("content", ""))


class DeepSeekProvider(_JsonChatProvider):
    name = "deepseek"

    def __init__(self, model: str = "deepseek-chat", api_key: str | None = None, base_url: str | None = None, timeout: int = 90) -> None:
        super().__init__(model, timeout)
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")

    def _call(self, prompt: ResearchPrompt) -> str:
        if not self.api_key:
            raise LLMUnavailable("未配置 DEEPSEEK_API_KEY")
        body = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(prompt)},
            ],
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise LLMUnavailable(f"DeepSeek 调用失败：{exc}") from exc
        choices = payload.get("choices") or []
        return str(choices[0].get("message", {}).get("content", "")) if choices else ""


def build_llm_provider(name: str, model: str | None = None) -> LLMProvider:
    normalized = name.strip().lower()
    if normalized == "ollama":
        return OllamaProvider(model=model or "deepseek-r1:14b")
    if normalized == "deepseek":
        return DeepSeekProvider(model=model or "deepseek-chat")
    raise ValueError(f"不支持的模型 Provider：{name}")
