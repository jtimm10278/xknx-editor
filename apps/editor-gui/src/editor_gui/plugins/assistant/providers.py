"""The AI providers the Assistant talks to: Anthropic (Claude) through its SDK, and the OpenAI chat
completions API that OpenAI, xAI (Grok) and many others offer.

A :class:`Conversation` keeps the provider's own message list. :meth:`Conversation.ask` adds the
user's question and :meth:`Conversation.answer` the results of the tools the model asked for; both
return the model's next :class:`Turn`: its text and the tool calls it wants made.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import httpx

ProviderKey = Literal["anthropic", "openai", "xai", "custom"]

# Each provider's API address and the model offered when none is set. OpenAI and xAI change their
# model names often, so the user names one (the hint shows where to find it).
PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("https://api.anthropic.com", "claude-opus-5-5"),
    "openai": ("https://api.openai.com/v1", ""),
    "xai": ("https://api.x.ai/v1", ""),
    "custom": ("", ""),
}

# Answer length per model call: room for a long explanation, short enough for a plain request.
MAX_TOKENS = 16000
# Claude models that take the server-side refusal fallback (another model answers a request the
# first one declines).
_FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}
)


class ProviderError(Exception):
    """The provider could not be asked. ``code`` says why (the panel shows it translated):
    ``no_key``, ``no_model``, ``no_url``, ``auth``, ``rate``, ``status``, ``connect`` or
    ``unexpected``; ``detail`` is the provider's own message, if any."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    call: ToolCall
    text: str
    is_error: bool = False


@dataclass(slots=True)
class Turn:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list[ToolCall])
    # "done", "tools" (calls to make), "refused", "length" (the answer was cut off).
    stop: str = "done"


class Conversation(Protocol):
    def ask(self, question: str) -> Turn: ...
    def answer(self, results: Sequence[ToolResult]) -> Turn: ...


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    provider: str
    model: str
    api_key: str
    base_url: str = ""

    def resolved_url(self) -> str:
        return (self.base_url or PROVIDERS.get(self.provider, ("", ""))[0]).rstrip("/")

    def resolved_model(self) -> str:
        return self.model or PROVIDERS.get(self.provider, ("", ""))[1]


def open_conversation(
    settings: ProviderSettings,
    system: str,
    tools: Sequence[ToolSpec],
    *,
    transport: httpx.BaseTransport | None = None,
) -> Conversation:
    """A new conversation with the provider ``settings`` name (raises :class:`ProviderError` when
    they are incomplete)."""
    if not settings.api_key:
        raise ProviderError("no_key")
    if not settings.resolved_model():
        raise ProviderError("no_model")
    if not settings.resolved_url():
        raise ProviderError("no_url")
    if settings.provider == "anthropic":
        return AnthropicConversation(settings, system, tools)
    return ChatCompletionsConversation(settings, system, tools, transport=transport)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments as a dict: parsed from JSON when the provider sends a string."""
    if isinstance(raw, dict):
        return dict(raw)  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(value, dict):
            return dict(value)  # pyright: ignore[reportUnknownArgumentType]
    return {}


class AnthropicConversation:
    """Claude through the Anthropic SDK (Messages API with tools)."""

    def __init__(
        self,
        settings: ProviderSettings,
        system: str,
        tools: Sequence[ToolSpec],
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=settings.api_key, base_url=settings.resolved_url(), max_retries=2
        )
        self._model = settings.resolved_model()
        self._system = system
        # Sorted, so the request starts the same every time and the prompt cache holds.
        self._tools: list[dict[str, Any]] = [
            {"name": t.name, "description": t.description, "input_schema": t.schema}
            for t in sorted(tools, key=lambda t: t.name)
        ]
        self._messages: list[dict[str, Any]] = []

    def ask(self, question: str) -> Turn:
        self._messages.append({"role": "user", "content": question})
        return self._call()

    def answer(self, results: Sequence[ToolResult]) -> Turn:
        # Every result of the turn in one message, failures included (marked as errors).
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.text,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )
        return self._call()

    def _call(self) -> Turn:
        anthropic = self._anthropic
        omit = anthropic.omit
        # A request the model declines is answered by the fallback model the API picks.
        fallback = self._model in _FALLBACK_MODELS
        try:
            created = self._client.beta.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=self._system,
                tools=self._tools,  # pyright: ignore[reportArgumentType]
                messages=self._messages,  # pyright: ignore[reportArgumentType]
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"] if fallback else omit,
                fallbacks="default" if fallback else omit,
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderError("auth") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("rate") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("status", f"{exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("connect", str(exc)) from exc
        # Typed loosely: the content blocks differ between SDK versions.
        response = cast(Any, created)
        # The whole content goes back next time (thinking blocks included, unchanged).
        self._messages.append(
            {"role": "assistant", "content": [b.model_dump() for b in response.content]}
        )
        turn = Turn()
        texts: list[str] = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                turn.calls.append(
                    ToolCall(block.id, block.name, _parse_arguments(block.input))
                )
        turn.text = "\n\n".join(t for t in texts if t)
        if response.stop_reason == "refusal":
            turn.stop = "refused"
        elif response.stop_reason == "max_tokens":
            turn.stop = "length"
        elif turn.calls:
            turn.stop = "tools"
        return turn


class ChatCompletionsConversation:
    """The OpenAI chat completions API with function tools (OpenAI, xAI and compatible ones)."""

    def __init__(
        self,
        settings: ProviderSettings,
        system: str,
        tools: Sequence[ToolSpec],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = settings.resolved_url() + "/chat/completions"
        self._model = settings.resolved_model()
        self._http = httpx.Client(
            timeout=httpx.Timeout(600.0, connect=20.0),
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )
        self._tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in sorted(tools, key=lambda t: t.name)
        ]
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    def ask(self, question: str) -> Turn:
        self._messages.append({"role": "user", "content": question})
        return self._call()

    def answer(self, results: Sequence[ToolResult]) -> Turn:
        for r in results:
            text = f"Error: {r.text}" if r.is_error else r.text
            self._messages.append(
                {"role": "tool", "tool_call_id": r.call.id, "content": text}
            )
        return self._call()

    def _call(self) -> Turn:
        body: dict[str, Any] = {"model": self._model, "messages": self._messages}
        if self._tools:
            body["tools"] = self._tools
        try:
            response = self._http.post(self._url, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError("connect", str(exc)) from exc
        if response.status_code in (401, 403):
            raise ProviderError("auth")
        if response.status_code == 429:
            raise ProviderError("rate")
        if not response.is_success:
            raise ProviderError(
                "status", f"{response.status_code}: {response.text[:300]}"
            )
        try:
            choice = cast(dict[str, Any], response.json()["choices"][0])
            message = cast(dict[str, Any], choice["message"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("unexpected") from exc
        self._messages.append(message)
        turn = Turn(text=str(message.get("content") or ""))
        calls = cast(list[dict[str, Any]], message.get("tool_calls") or [])
        for call in calls:
            function = cast(dict[str, Any], call.get("function") or {})
            turn.calls.append(
                ToolCall(
                    str(call.get("id", "")),
                    str(function.get("name", "")),
                    _parse_arguments(function.get("arguments")),
                )
            )
        reason = choice.get("finish_reason")
        if reason == "length":
            turn.stop = "length"
        elif reason == "content_filter":
            turn.stop = "refused"
        elif turn.calls:
            turn.stop = "tools"
        return turn
