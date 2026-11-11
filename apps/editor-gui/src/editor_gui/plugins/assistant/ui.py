"""The AI Assistant panel: the conversation, a question field, Send/Stop, Allow Changes."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.assistant.agent import Assistant, Entry
from editor_gui.plugins.assistant.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dpi import px
from editor_gui.widgets.fonts import bold
from editor_gui.widgets.markdown import COMPACT, render_markdown
from editor_gui.widgets.spinner import loading


def error_text(code: str, detail: str = "") -> str:
    """An error entry's text in the interface language, the provider's own details after it."""
    texts: dict[str, Callable[[], str]] = {
        "no_key": lambda: S.ERROR_NO_KEY,
        "no_model": lambda: S.ERROR_NO_MODEL,
        "no_url": lambda: S.ERROR_NO_URL,
        "auth": lambda: S.ERROR_AUTH,
        "rate": lambda: S.ERROR_RATE,
        "status": lambda: S.ERROR_STATUS,
        "connect": lambda: S.ERROR_CONNECT,
        "unexpected": lambda: S.ERROR_UNEXPECTED,
        "refused": lambda: S.ERROR_REFUSED,
        "length": lambda: S.ERROR_LENGTH,
        "internal": lambda: S.ERROR_INTERNAL,
    }
    if code == "steps":
        return S.ERROR_STEPS.format(count=detail)
    text = texts[code]() if code in texts else code
    return f"{text} ({detail})" if detail else text


def note_text(code: str) -> str:
    return S.NOTE_CHANGES if code == "changes" else S.NOTE_STOPPED


class AssistantPanel:
    def __init__(
        self,
        assistant: Assistant,
        *,
        provider_name: Callable[[], str],
        ready: Callable[[], bool],
        open_settings: Callable[[], None],
    ) -> None:
        self._assistant = assistant
        self._provider_name = provider_name
        self._ready = ready
        self._open_settings = open_settings
        self._question = ""
        self._allow_changes = False
        self._seen = -1

    def render(self) -> None:
        assistant = self._assistant
        if not self._ready():
            imgui.text_wrapped(S.NOT_SET_UP)
            if imgui.button(S.OPEN_SETTINGS):
                self._open_settings()
            return
        style = imgui.get_style()
        # The conversation fills the panel above the controls (two rows).
        controls = 2 * imgui.get_frame_height_with_spacing() + style.item_spacing.y
        if imgui.begin_child("##assistant_log", imgui.ImVec2(0, -controls)):
            entries = assistant.entries()
            if not entries:
                imgui.text_disabled(S.INTRO)
                imgui.text_disabled(S.PRIVACY.format(provider=self._provider_name()))
            for i, entry in enumerate(entries):
                imgui.push_id(i)
                self._render_entry(entry)
                imgui.pop_id()
            if assistant.busy:
                loading("##assistant_busy", S.WORKING)
            # Follow the conversation as it grows.
            if assistant.generation != self._seen:
                self._seen = assistant.generation
                imgui.set_scroll_here_y(1.0)
        imgui.end_child()
        self._render_controls()

    def _render_entry(self, entry: Entry) -> None:
        if entry.role == "user":
            imgui.spacing()
            with bold():
                imgui.text(S.YOU)
            imgui.text_wrapped(entry.text)
        elif entry.role == "assistant":
            imgui.spacing()
            with bold():
                imgui.text(S.PANEL_ASSISTANT)
            render_markdown(entry.text, COMPACT)
        elif entry.role == "tool":
            imgui.text_disabled(S.LOOKED_UP.format(tool=entry.text))
        elif entry.role == "error":
            imgui.push_style_color(imgui.Col_.text, status_color("error"))
            imgui.text_wrapped(error_text(entry.text, entry.detail))
            imgui.pop_style_color()
        else:
            imgui.text_disabled(note_text(entry.text))

    def _render_controls(self) -> None:
        assistant = self._assistant
        busy = assistant.busy
        imgui.set_next_item_width(-px(1))
        flags = imgui.InputTextFlags_.enter_returns_true
        entered, self._question = imgui.input_text_with_hint(
            "##assistant_question", S.INPUT_HINT, self._question, flags
        )
        send = entered
        if busy:
            if imgui.button(S.STOP):
                assistant.stop()
        else:
            send = imgui.button(S.SEND) or send
        imgui.same_line()
        if busy:
            imgui.begin_disabled()
        if imgui.button(S.NEW_CONVERSATION):
            assistant.clear()
        imgui.same_line()
        _, self._allow_changes = checkbox(S.ALLOW_CHANGES, self._allow_changes)
        if busy:
            imgui.end_disabled()
        if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
            imgui.set_tooltip(S.ALLOW_CHANGES_HINT)
        if (
            send
            and not busy
            and assistant.ask(self._question, allow_changes=self._allow_changes)
        ):
            self._question = ""
