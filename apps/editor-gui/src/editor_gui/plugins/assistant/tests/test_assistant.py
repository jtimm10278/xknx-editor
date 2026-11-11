"""The AI Assistant: which tools the model gets, the question loop, the providers and the settings."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import types as mcp_types

from editor_gui import settings as app_settings
from editor_gui.plugins.assistant import providers
from editor_gui.plugins.assistant.agent import (
    MAX_RESULT_CHARS,
    MAX_STEPS,
    Assistant,
    allowed_tool,
    result_text,
)
from editor_gui.plugins.assistant.plugin import AssistantPlugin
from editor_gui.plugins.assistant.providers import (
    ChatCompletionsConversation,
    ProviderError,
    ProviderSettings,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    open_conversation,
)
from editor_gui.plugins.assistant.ui import error_text
from editor_gui.plugins.base import API_VERSION, Logger, PluginAPI
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.mcp.tests.test_server import (
    _build,  # pyright: ignore[reportPrivateUsage]
)


def _tool(name: str, **hints: bool) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        input_schema={"type": "object", "properties": {}},
        annotations=mcp_types.ToolAnnotations(**hints),  # type: ignore[arg-type]
    )


# --- which tools ------------------------------------------------------------


def test_read_tools_always_allowed() -> None:
    tool = _tool("project_info", read_only_hint=True)
    assert allowed_tool(tool, allow_changes=False)
    assert allowed_tool(tool, allow_changes=True)


def test_project_edits_only_with_allow_changes() -> None:
    tool = _tool("project_rename_device", read_only_hint=False)
    assert not allowed_tool(tool, allow_changes=False)
    assert allowed_tool(tool, allow_changes=True)


@pytest.mark.parametrize(
    "name", ["backup_create", "home_assistant_add_group_addresses", "network_start"]
)
def test_other_write_tools_never(name: str) -> None:
    assert not allowed_tool(_tool(name, read_only_hint=False), allow_changes=True)


@pytest.mark.parametrize("name", ["project_open", "project_close"])
def test_opening_and_closing_stay_with_the_user(name: str) -> None:
    assert not allowed_tool(_tool(name, read_only_hint=False), allow_changes=True)


def test_destructive_open_world_settings_and_unmarked_never() -> None:
    assert not allowed_tool(
        _tool("project_remove_device", destructive_hint=True), allow_changes=True
    )
    assert not allowed_tool(
        _tool("catalog_docs_fetch", read_only_hint=True, open_world_hint=True),
        allow_changes=True,
    )
    assert not allowed_tool(
        _tool("settings_set", read_only_hint=False), allow_changes=True
    )
    unmarked = mcp_types.Tool(name="x", input_schema={"type": "object"})
    assert not allowed_tool(unmarked, allow_changes=True)


def test_real_server_offers_no_bus_or_internet_tools(tmp_path: Path) -> None:
    """Against the editor's own tools: nothing that programs, connects or downloads is offered."""
    import asyncio

    from fastmcp import Client

    server, _project = _build(tmp_path)

    async def names(allow: bool) -> set[str]:
        async with Client(server) as client:
            return {
                t.name
                for t in await client.list_tools()
                if allowed_tool(t, allow_changes=allow)
            }

    read_only = asyncio.run(names(False))
    changes = asyncio.run(names(True))
    assert "project_info" in read_only
    assert "project_rename_device" in changes - read_only
    for name in changes:
        assert name.startswith("project_") or name in read_only, name
    for banned in (
        "connection_connect",
        "project_program_device",
        "catalog_download_online_products",
        "project_export_knxproj",
        "project_remove_device",
        "settings_set",
        "project_close",
    ):
        assert banned not in changes


# --- results ----------------------------------------------------------------


def test_result_text_cut_when_long() -> None:
    class _Block:
        text = "x" * (MAX_RESULT_CHARS + 50)

    class _Result:
        def __init__(self) -> None:
            self.content = [_Block()]

    text = result_text(_Result())
    assert text.startswith("x" * 100)
    assert "50 more characters" in text


# --- the question loop --------------------------------------------------------


class _FakeConversation:
    def __init__(self, turns: list[Turn]) -> None:
        self.turns = turns
        self.questions: list[str] = []
        self.results: list[list[ToolResult]] = []

    def ask(self, question: str) -> Turn:
        self.questions.append(question)
        return self.turns.pop(0)

    def answer(self, results: Sequence[ToolResult]) -> Turn:
        self.results.append(list(results))
        return self.turns.pop(0)


def _assistant(
    tmp_path: Path, turns: list[Turn]
) -> tuple[Assistant, _FakeConversation, list[dict[str, Any]]]:
    server, _project = _build(tmp_path)
    fake = _FakeConversation(turns)
    opened: list[dict[str, Any]] = []

    def connect(
        settings: ProviderSettings, system: str, tools: Sequence[ToolSpec]
    ) -> _FakeConversation:
        opened.append({"system": system, "tools": [t.name for t in tools]})
        return fake

    assistant = Assistant(
        lambda: server,
        lambda: ProviderSettings("anthropic", "", "key"),
        Logger(LogService(), "assistant"),
        connect=connect,
    )
    return assistant, fake, opened


def test_question_answered_with_a_tool(tmp_path: Path) -> None:
    assistant, fake, opened = _assistant(
        tmp_path,
        [
            Turn(calls=[ToolCall("1", "project_status", {})], stop="tools"),
            Turn(text="No project is open."),
        ],
    )
    assert assistant.ask("What is open?", allow_changes=False)
    assistant.wait(30)
    roles = [(e.role, e.text) for e in assistant.entries()]
    assert roles == [
        ("user", "What is open?"),
        ("tool", "project_status"),
        ("assistant", "No project is open."),
    ]
    assert fake.questions == ["What is open?"]
    assert len(fake.results[0]) == 1 and not fake.results[0][0].is_error
    assert "project_rename_device" not in opened[0]["tools"]
    assert "You may only read" in opened[0]["system"]


def test_tool_not_offered_is_refused(tmp_path: Path) -> None:
    assistant, fake, _opened = _assistant(
        tmp_path,
        [
            Turn(calls=[ToolCall("1", "project_rename_device", {})], stop="tools"),
            Turn(text="I can't."),
        ],
    )
    assistant.ask("Rename it", allow_changes=False)
    assistant.wait(30)
    result = fake.results[0][0]
    assert result.is_error and "not available" in result.text


def test_allow_changes_switch_starts_a_new_conversation(tmp_path: Path) -> None:
    assistant, _fake, opened = _assistant(
        tmp_path, [Turn(text="one"), Turn(text="two")]
    )
    assistant.ask("a", allow_changes=False)
    assistant.wait(30)
    assistant.ask("b", allow_changes=True)
    assistant.wait(30)
    assert len(opened) == 2
    assert "project_rename_device" in opened[1]["tools"]
    assert ("note", "changes") in [(e.role, e.text) for e in assistant.entries()]


def test_refusal_and_cut_off_shown(tmp_path: Path) -> None:
    assistant, _fake, _opened = _assistant(
        tmp_path, [Turn(stop="refused"), Turn(text="half", stop="length")]
    )
    assistant.ask("a", allow_changes=False)
    assistant.wait(30)
    assistant.ask("b", allow_changes=False)
    assistant.wait(30)
    errors = [e.text for e in assistant.entries() if e.role == "error"]
    assert errors == ["refused", "length"]


def test_step_limit(tmp_path: Path) -> None:
    call = Turn(calls=[ToolCall("1", "project_status", {})], stop="tools")
    assistant, _fake, _opened = _assistant(tmp_path, [call] * (MAX_STEPS + 1))
    assistant.ask("loop", allow_changes=False)
    assistant.wait(60)
    last = assistant.entries()[-1]
    assert (last.role, last.text, last.detail) == ("error", "steps", str(MAX_STEPS))


def test_provider_error_reported(tmp_path: Path) -> None:
    server, _project = _build(tmp_path)

    def connect(*_args: Any) -> Any:
        raise ProviderError("auth")

    assistant = Assistant(
        lambda: server,
        lambda: ProviderSettings("anthropic", "", "key"),
        Logger(LogService(), "assistant"),
        connect=connect,
    )
    assistant.ask("hi", allow_changes=False)
    assistant.wait(30)
    assert assistant.entries()[-1].text == "auth"
    assert error_text("auth")


def test_empty_question_and_clear(tmp_path: Path) -> None:
    assistant, _fake, _opened = _assistant(tmp_path, [Turn(text="ok")])
    assert not assistant.ask("   ", allow_changes=False)
    assistant.ask("q", allow_changes=False)
    assistant.wait(30)
    assistant.clear()
    assert assistant.entries() == []


def test_every_error_code_has_text() -> None:
    for code in (
        "no_key",
        "no_model",
        "no_url",
        "auth",
        "rate",
        "status",
        "connect",
        "unexpected",
        "refused",
        "length",
        "internal",
    ):
        assert error_text(code) != code
    assert "7" in error_text("steps", "7")


# --- providers --------------------------------------------------------------


def test_incomplete_settings_refused() -> None:
    with pytest.raises(ProviderError) as no_key:
        open_conversation(ProviderSettings("anthropic", "", ""), "", [])
    assert no_key.value.code == "no_key"
    with pytest.raises(ProviderError) as no_model:
        open_conversation(ProviderSettings("openai", "", "k"), "", [])
    assert no_model.value.code == "no_model"
    with pytest.raises(ProviderError) as no_url:
        open_conversation(ProviderSettings("custom", "m", "k"), "", [])
    assert no_url.value.code == "no_url"


def test_chat_completions_round_trip() -> None:
    sent: list[dict[str, Any]] = []
    replies = [
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "project_info",
                                    "arguments": '{"a": 1}',
                                },
                            }
                        ],
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Done."},
                }
            ]
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.x.ai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer k"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))

    conversation = ChatCompletionsConversation(
        ProviderSettings("xai", "grok-x", "k"),
        "system text",
        [ToolSpec("project_info", "Info", {"type": "object"})],
        transport=httpx.MockTransport(handler),
    )
    turn = conversation.ask("hi")
    assert turn.stop == "tools"
    assert turn.calls == [ToolCall("c1", "project_info", {"a": 1})]
    assert sent[0]["model"] == "grok-x"
    assert sent[0]["messages"][0] == {"role": "system", "content": "system text"}
    assert sent[0]["tools"][0]["function"]["name"] == "project_info"
    turn = conversation.answer([ToolResult(turn.calls[0], "result")])
    assert (turn.text, turn.stop) == ("Done.", "done")
    assert sent[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "result",
    }


@pytest.mark.parametrize(
    ("status", "code"), [(401, "auth"), (429, "rate"), (500, "status")]
)
def test_chat_completions_errors(status: int, code: str) -> None:
    conversation = ChatCompletionsConversation(
        ProviderSettings("openai", "m", "k"),
        "",
        [],
        transport=httpx.MockTransport(lambda _r: httpx.Response(status, text="no")),
    )
    with pytest.raises(ProviderError) as error:
        conversation.ask("hi")
    assert error.value.code == code


def test_anthropic_request_and_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    from anthropic.types.beta import BetaMessage

    calls: list[dict[str, Any]] = []
    message = BetaMessage.model_validate(
        {
            "id": "m1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [
                {"type": "text", "text": "Looking."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "project_info",
                    "input": {"x": 2},
                },
            ],
        }
    )

    conversation = providers.AnthropicConversation(
        ProviderSettings("anthropic", "", "k"),
        "system text",
        [ToolSpec("b_tool", "", {}), ToolSpec("a_tool", "", {})],
    )

    def create(**kwargs: Any) -> BetaMessage:
        calls.append(kwargs)
        return message

    monkeypatch.setattr(
        conversation._client.beta.messages,  # pyright: ignore[reportPrivateUsage]
        "create",
        create,
    )
    turn = conversation.ask("hi")
    assert turn.text == "Looking."
    assert turn.calls == [ToolCall("t1", "project_info", {"x": 2})]
    request = calls[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert [t["name"] for t in request["tools"]] == ["a_tool", "b_tool"]
    conversation.answer([ToolResult(turn.calls[0], "r", is_error=True)])
    sent = calls[1]["messages"]
    assert sent[1]["role"] == "assistant" and sent[1]["content"][1]["id"] == "t1"
    assert sent[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "r",
        "is_error": True,
    }


# --- the plugin's settings ----------------------------------------------------


def _plugin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AssistantPlugin:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    log = LogService()
    api = PluginAPI(
        api_version=API_VERSION,
        project=None,  # type: ignore[arg-type]
        catalog=None,  # type: ignore[arg-type]
        connection=None,  # type: ignore[arg-type]
        log=log,
    )
    return AssistantPlugin(api, lambda: None)  # type: ignore[arg-type, return-value]


def test_settings_stored_and_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin = _plugin(monkeypatch, tmp_path)
    assert not plugin.ready()
    by_key = {s.key: s for s in plugin.settings()}
    assert by_key["assistant_api_key"].secret
    by_key["assistant_provider"].set("xai")
    by_key["assistant_model"].set(" grok-x ")
    by_key["assistant_api_key"].set("k")
    settings = plugin.provider_settings()
    assert (settings.provider, settings.model, settings.api_key) == (
        "xai",
        "grok-x",
        "k",
    )
    assert plugin.ready()
    # Another provider: the model name no longer fits.
    by_key["assistant_provider"].set("openai")
    assert plugin.provider_settings().model == ""
    with pytest.raises(ValueError):
        by_key["assistant_provider"].set("nope")


def test_default_provider_is_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin = _plugin(monkeypatch, tmp_path)
    settings = plugin.provider_settings()
    assert settings.provider == "anthropic"
    assert settings.resolved_model() == "claude-opus-5-5"
