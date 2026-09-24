from __future__ import annotations

import asyncio
import contextlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests
import urllib3

from .env import get_bool_env, get_env, get_float_env
from .json_utils import extract_json_object

try:
    import httpx
except Exception:  # pragma: no cover - handled by _agents_sdk_available().
    httpx = None  # type: ignore[assignment]

try:
    from agents import (
        Agent,
        AsyncOpenAI,
        GuardrailFunctionOutput,
        ModelSettings,
        OpenAIChatCompletionsModel,
        RunConfig,
        Runner,
        input_guardrail,
        output_guardrail,
        set_tracing_disabled,
    )
except Exception:  # pragma: no cover - production fallback when dependency is not installed yet.
    Agent = None  # type: ignore[assignment]
    AsyncOpenAI = None  # type: ignore[assignment]
    GuardrailFunctionOutput = None  # type: ignore[assignment]
    ModelSettings = None  # type: ignore[assignment]
    OpenAIChatCompletionsModel = None  # type: ignore[assignment]
    RunConfig = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    input_guardrail = None  # type: ignore[assignment]
    output_guardrail = None  # type: ignore[assignment]
    set_tracing_disabled = None  # type: ignore[assignment]


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass
class CompletionUsage:
    runtime: str = ""
    model: str = ""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_chars: int = 0
    output_chars: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@contextlib.contextmanager
def without_proxy_env() -> Iterator[None]:
    saved = {key: os.environ[key] for key in PROXY_ENV_KEYS if key in os.environ}
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.update(saved)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _agents_sdk_available() -> bool:
    return bool(httpx and Agent and AsyncOpenAI and OpenAIChatCompletionsModel and Runner)


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = get_env(name)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _configured_api_key() -> str:
    return get_env("POLYDATA_AGENT_API_KEY") or get_env("API_KEY") or get_env("OPENAI_API_KEY")


def _chat_completions_root(api_base: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if base.endswith(suffix):
        return base[: -len(suffix)]
    return base


def _host_label(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path
    except Exception:
        return "unknown"


def _input_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


if input_guardrail and GuardrailFunctionOutput:

    @input_guardrail(name="polydata_input_budget", run_in_parallel=False)
    def _input_budget_guardrail(_ctx: Any, _agent: Any, agent_input: Any) -> Any:
        text = _input_text(agent_input)
        max_chars = max(1_000, int(get_env("POLYDATA_AGENT_RUN_INPUT_MAX_CHARS", "32000") or "32000"))
        return GuardrailFunctionOutput(
            output_info={"input_chars": len(text), "max_chars": max_chars},
            tripwire_triggered=len(text) > max_chars,
        )

else:
    _input_budget_guardrail = None


if output_guardrail and GuardrailFunctionOutput:

    @output_guardrail(name="polydata_json_output")
    def _json_output_guardrail(_ctx: Any, _agent: Any, agent_output: Any) -> Any:
        text = str(agent_output or "")
        ok = True
        error = ""
        try:
            extract_json_object(text)
        except Exception as exc:
            ok = False
            error = str(exc)
        return GuardrailFunctionOutput(
            output_info={"valid_json": ok, "error": error[:180]},
            tripwire_triggered=not ok,
        )

else:
    _json_output_guardrail = None


class OpenAICompatibleClient:
    def __init__(self) -> None:
        self.api_key = _configured_api_key()
        self.api_base = get_env("POLYDATA_AGENT_API_BASE", "http://127.0.0.1:8317/v1/chat/completions")
        self.model = get_env("POLYDATA_AGENT_MODEL", "gpt-5.3-chat")
        self.timeout = get_float_env("POLYDATA_AGENT_TIMEOUT_SECONDS", 45.0)
        self.verify_ssl = get_bool_env("POLYDATA_AGENT_SSL_VERIFY", False)
        self.runtime = get_env("POLYDATA_AGENT_RUNTIME", "agents").strip().lower() or "agents"
        self.last_usage = CompletionUsage(model=self.model)
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_base and self.model)

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 900,
        workflow_name: str = "polydata-agent",
    ) -> str:
        if not self.configured:
            raise RuntimeError("API_KEY or model endpoint is not configured")
        if self.runtime != "chat" and _agents_sdk_available():
            return self._complete_json_with_agents_sdk(messages, max_tokens=max_tokens, workflow_name=workflow_name)
        return self._complete_json_legacy(messages, max_tokens=max_tokens, workflow_name=workflow_name)

    def _complete_json_legacy(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        workflow_name: str,
    ) -> str:
        token_key = "max_completion_tokens" if self.model.lower().startswith("gpt-5") else "max_tokens"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            token_key: max_tokens,
        }
        if "gpt-5.3" not in self.model.lower():
            body["temperature"] = 0.2
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with without_proxy_env():
            response = requests.post(
                self.api_base,
                headers=headers,
                json=body,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(str(payload.get("error")))
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response had no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM response content was empty")
        self.last_usage = CompletionUsage(
            runtime="chat",
            model=str(payload.get("model") or self.model),
            requests=1,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            input_chars=sum(len(item.get("content") or "") for item in messages if isinstance(item, dict)),
            output_chars=len(content),
            raw=usage,
        )
        self._append_usage_log(workflow_name)
        return content

    def _complete_json_with_agents_sdk(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        workflow_name: str,
    ) -> str:
        system_prompt, user_prompt = self._split_messages(messages)
        input_chars = len(system_prompt) + len(user_prompt)
        max_chars = max(1_000, int(get_env("POLYDATA_AGENT_RUN_INPUT_MAX_CHARS", "32000") or "32000"))
        if input_chars > max_chars:
            raise RuntimeError(f"agent input exceeds limit: {input_chars} > {max_chars} chars")
        result = self._run_coro(
            self._run_agent(system_prompt, user_prompt, max_tokens=max_tokens, workflow_name=workflow_name)
        )
        output = str(result.final_output or "").strip()
        if not output:
            raise RuntimeError("Agents SDK response content was empty")
        usage = self._collect_agents_usage(result, input_chars=input_chars, output_chars=len(output))
        self.last_usage = usage
        self._append_usage_log(workflow_name)
        return output

    async def _run_agent(self, system_prompt: str, user_prompt: str, *, max_tokens: int, workflow_name: str) -> Any:
        if not _agents_sdk_available():
            raise RuntimeError("openai-agents SDK is not installed")
        if set_tracing_disabled:
            with without_proxy_env():
                set_tracing_disabled(_truthy_env("POLYDATA_AGENT_TRACING_DISABLED", True))
        base_url = _chat_completions_root(self.api_base)
        http_client = httpx.AsyncClient(
            verify=self.verify_ssl,
            trust_env=False,
            timeout=self.timeout,
        )
        client = AsyncOpenAI(api_key=self.api_key, base_url=base_url, timeout=self.timeout, http_client=http_client)
        try:
            model_settings = self._model_settings(max_tokens)
            model = OpenAIChatCompletionsModel(
                model=self.model,
                openai_client=client,
                strict_feature_validation=False,
            )
            guardrails = []
            if _input_budget_guardrail is not None:
                guardrails.append(_input_budget_guardrail)
            output_guardrails = []
            if _json_output_guardrail is not None:
                output_guardrails.append(_json_output_guardrail)
            agent = Agent(
                name=workflow_name,
                instructions=system_prompt,
                model=model,
                model_settings=model_settings,
                tools=[],
                handoffs=[],
                input_guardrails=guardrails,
                output_guardrails=output_guardrails,
            )
            run_config = RunConfig(
                workflow_name=workflow_name,
                tracing_disabled=_truthy_env("POLYDATA_AGENT_TRACING_DISABLED", True),
                trace_include_sensitive_data=False,
                trace_metadata={
                    "service": "polydata-agent",
                    "runtime": "openai-agents-sdk",
                    "model": self.model,
                    "api_base_host": _host_label(base_url),
                },
            )
            with without_proxy_env():
                return await Runner.run(agent, user_prompt, max_turns=1, run_config=run_config)
        finally:
            await http_client.aclose()

    def _model_settings(self, max_tokens: int) -> Any:
        if self.model.lower().startswith("gpt-5"):
            return ModelSettings(
                temperature=None,
                max_tokens=None,
                extra_body={"max_completion_tokens": max_tokens},
            )
        return ModelSettings(temperature=0.2, max_tokens=max_tokens)

    def _split_messages(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "")
            if role == "system":
                system_parts.append(content)
            else:
                user_parts.append(content)
        return "\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip()

    def _run_coro(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()

    def _collect_agents_usage(self, result: Any, *, input_chars: int, output_chars: int) -> CompletionUsage:
        usage = CompletionUsage(runtime="agents", model=self.model, input_chars=input_chars, output_chars=output_chars)
        raw_entries: list[dict[str, Any]] = []
        for response in getattr(result, "raw_responses", []) or []:
            raw_usage = getattr(response, "usage", None)
            if raw_usage is None:
                continue
            usage.requests += int(getattr(raw_usage, "requests", 0) or 0)
            usage.input_tokens += int(getattr(raw_usage, "input_tokens", 0) or 0)
            usage.output_tokens += int(getattr(raw_usage, "output_tokens", 0) or 0)
            usage.total_tokens += int(getattr(raw_usage, "total_tokens", 0) or 0)
            raw_entries.append({
                "requests": int(getattr(raw_usage, "requests", 0) or 0),
                "input_tokens": int(getattr(raw_usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(raw_usage, "output_tokens", 0) or 0),
                "total_tokens": int(getattr(raw_usage, "total_tokens", 0) or 0),
            })
        if usage.requests == 0 and raw_entries:
            usage.requests = len(raw_entries)
        usage.raw = {"responses": raw_entries}
        return usage

    def _append_usage_log(self, workflow_name: str) -> None:
        raw_path = get_env("POLYDATA_AGENT_USAGE_LOG_PATH")
        path = Path(raw_path).expanduser() if raw_path else Path.home() / ".cache" / "polydata" / "agent-usage.jsonl"
        row = {
            "timestamp": _utc_now_iso(),
            "workflow": workflow_name,
            "runtime": self.last_usage.runtime,
            "model": self.last_usage.model,
            "apiBaseHost": _host_label(self.api_base),
            "requests": self.last_usage.requests,
            "inputTokens": self.last_usage.input_tokens,
            "outputTokens": self.last_usage.output_tokens,
            "totalTokens": self.last_usage.total_tokens,
            "inputChars": self.last_usage.input_chars,
            "outputChars": self.last_usage.output_chars,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return
