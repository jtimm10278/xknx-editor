"""The Assistant's work: one question at a time, answered by the chosen AI model with the editor's
own MCP tools, called in-process (nothing is opened to the network for them).

Which tools the model gets is decided here, never by the model:

- tools that only read: always;
- tools that change the open project (undoable ``project_*`` "write" tools): only with "Allow
  changes" ticked. Other write tools (backups, the catalog, keys, connection, Home Assistant) and
  opening, closing or importing/exporting a project stay with the user;
- never tools that remove or replace data, write files, or reach the bus, the network or the
  internet ("destructive" or open-world), and never the editor's settings.

A question runs on a worker thread (:meth:`Assistant.ask`); the panel reads :meth:`Assistant.entries`
each frame. The tools themselves run their work on the UI thread, as for any MCP client.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from editor_gui.plugins.assistant.providers import (
    Conversation,
    ProviderError,
    ProviderSettings,
    ToolCall,
    ToolResult,
    ToolSpec,
    open_conversation,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from mcp import types as mcp_types

    from editor_gui.plugins.base import Logger

# Model calls per question: enough for a thorough look through a project, a stop for a loop.
MAX_STEPS = 30
# A tool result longer than this is cut (the model is told), so one big list cannot fill the
# context or the bill.
MAX_RESULT_CHARS = 30_000
# Tools no assistant gets, whatever their kind: the editor's settings (an AI must not change its own
# limits, the connection or the MCP server).
_NEVER = frozenset({"settings_set"})
# Project "write" tools that are not an undoable edit of the open project.
_NOT_A_PROJECT_EDIT = frozenset(
    {
        "project_open",
        "project_close",
        "project_import_triton_prj",
        "project_export_triton_prj",
    }
)

PREAMBLE = (
    "You are the AI Assistant built into XKNX Editor. You answer the user's questions about the KNX "
    "project open in the editor, using the editor's tools below.\n"
    "- Look things up with the tools before answering; never guess addresses, names or values. If "
    "the tools do not show something, say so.\n"
    "- Answer in the language the user writes in, briefly and plainly. Name devices by individual "
    "address and name, group addresses by address and name.\n"
    "- You have no access to the KNX bus: you cannot program devices, read live values or send "
    "telegrams. Say what is configured in the project, not what a device is doing now.\n"
    "{changes}\n"
    "The editor's own instructions for its tools follow.\n\n"
)
_READ_ONLY = (
    "- You may only read: changes to the project are switched off. If the user asks for a change, "
    "say how to do it, or that they can tick Allow changes."
)
_CHANGES = (
    "- Changes to the project are allowed. Make only the changes the user asked for, say each one "
    "you made, and remind the user that Edit > Undo takes it back."
)

Role = Literal["user", "assistant", "tool", "error", "note"]


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of the conversation as the panel shows it."""

    role: Role
    # The text; for "error" and "note" a code the panel translates (see the panel).
    text: str
    # An error's details (the provider's message), shown after the translated text.
    detail: str = ""


def allowed_tool(tool: mcp_types.Tool, *, allow_changes: bool) -> bool:
    """Whether the assistant may offer ``tool`` to the model (see the module docstring)."""
    if tool.name in _NEVER:
        return False
    hints = tool.annotations
    if hints is None:
        return False
    if hints.open_world_hint or hints.destructive_hint:
        return False
    if hints.read_only_hint:
        return True
    return (
        allow_changes
        and tool.name.startswith("project_")
        and tool.name not in _NOT_A_PROJECT_EDIT
    )


def tool_spec(tool: mcp_types.Tool) -> ToolSpec:
    return ToolSpec(tool.name, tool.description or "", dict(tool.input_schema))


def result_text(result: Any) -> str:
    """The text of an MCP tool result (structured content as JSON when there is no text)."""
    parts: list[str] = []
    blocks: list[Any] = list(getattr(result, "content", None) or [])
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    if not parts and getattr(result, "structured_content", None) is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    text = "\n".join(parts)
    if len(text) > MAX_RESULT_CHARS:
        text = (
            text[:MAX_RESULT_CHARS]
            + f"\n[cut: {len(text) - MAX_RESULT_CHARS} more characters; ask for less, e.g. with a "
            "filter]"
        )
    return text


class Assistant:
    """One conversation with the chosen model; questions run one at a time on a worker thread."""

    def __init__(
        self,
        server: Callable[[], FastMCP],
        settings: Callable[[], ProviderSettings],
        log: Logger,
        *,
        connect: Callable[..., Conversation] = open_conversation,
    ) -> None:
        self._server = server
        self._settings = settings
        self._log = log
        self._connect = connect
        self._lock = threading.Lock()
        self._entries: list[Entry] = []
        self._conversation: Conversation | None = None
        self._conversation_changes = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.generation = 0

    # --- what the panel reads ---------------------------------------------

    def entries(self) -> list[Entry]:
        with self._lock:
            return list(self._entries)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _add(self, role: Role, text: str, detail: str = "") -> None:
        with self._lock:
            self._entries.append(Entry(role, text, detail))
            self.generation += 1

    # --- what the panel does ----------------------------------------------

    def clear(self) -> None:
        """Forget the conversation (a new one starts with the next question)."""
        if self.busy:
            return
        with self._lock:
            self._entries.clear()
            self._conversation = None
            self.generation += 1

    def stop(self) -> None:
        """Stop after the step that runs now (a model call or tool cannot be cut off)."""
        self._stop.set()

    def ask(self, question: str, *, allow_changes: bool) -> bool:
        """Start answering ``question``; False while another one runs or when it is empty."""
        question = question.strip()
        if not question or self.busy:
            return False
        self._stop.clear()
        self._add("user", question)
        self._thread = threading.Thread(
            target=self._run,
            args=(question, allow_changes),
            daemon=True,
            name="xknxeditor-assistant",
        )
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the question that runs (tests)."""
        if self._thread is not None:
            self._thread.join(timeout)

    # --- the work (worker thread) -----------------------------------------

    def _run(self, question: str, allow_changes: bool) -> None:
        try:
            asyncio.run(self._answer(question, allow_changes))
        except ProviderError as exc:
            self._conversation = None
            self._add("error", exc.code, exc.detail)
        except Exception as exc:  # the panel must hear of any failure
            self._conversation = None
            self._log.error("assistant failed", error=f"{type(exc).__name__}: {exc}")
            self._add("error", "internal", f"{type(exc).__name__}: {exc}")

    async def _answer(self, question: str, allow_changes: bool) -> None:
        from fastmcp import Client

        server = self._server()
        async with Client(server) as client:
            tools = [
                t
                for t in await client.list_tools()
                if allowed_tool(t, allow_changes=allow_changes)
            ]
            allowed = {t.name for t in tools}
            if (
                self._conversation is None
                or self._conversation_changes != allow_changes
            ):
                # The tools and the rules are fixed for a conversation: a change of the tick starts
                # a new one (the model would otherwise remember tools it no longer has).
                if self._conversation is not None:
                    self._add("note", "changes")
                system = PREAMBLE.format(
                    changes=_CHANGES if allow_changes else _READ_ONLY
                ) + (getattr(server, "instructions", None) or "")
                self._conversation = self._connect(
                    self._settings(), system, [tool_spec(t) for t in tools]
                )
                self._conversation_changes = allow_changes
            conversation = self._conversation
            self._log.info(
                "assistant question",
                tools=len(tools),
                allow_changes=allow_changes,
            )
            # Off the event loop, which keeps serving the in-process tool session meanwhile.
            turn = await asyncio.to_thread(conversation.ask, question)
            for _ in range(MAX_STEPS):
                if turn.text:
                    self._add("assistant", turn.text)
                if turn.stop == "refused":
                    self._add("error", "refused")
                    return
                if turn.stop == "length":
                    self._add("error", "length")
                    return
                if not turn.calls:
                    return
                if self._stop.is_set():
                    # The model waits for its calls' results: the next question starts afresh.
                    self._conversation = None
                    self._add("note", "stopped")
                    return
                results = [
                    await self._call(client, call, allowed) for call in turn.calls
                ]
                turn = await asyncio.to_thread(conversation.answer, results)
            self._conversation = None
            self._add("error", "steps", str(MAX_STEPS))

    async def _call(self, client: Any, call: ToolCall, allowed: set[str]) -> ToolResult:
        self._add("tool", call.name)
        if call.name not in allowed:
            return ToolResult(call, f"the tool {call.name} is not available", True)
        try:
            result = await client.call_tool(
                call.name, call.arguments, raise_on_error=False
            )
        except Exception as exc:
            return ToolResult(call, f"{type(exc).__name__}: {exc}", True)
        return ToolResult(
            call, result_text(result), bool(getattr(result, "is_error", False))
        )
