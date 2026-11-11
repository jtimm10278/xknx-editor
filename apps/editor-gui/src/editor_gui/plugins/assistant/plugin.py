"""AI Assistant plugin: the Assistant panel and its settings page (provider, model, API key)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from editor_gui.plugins.assistant.agent import Assistant
from editor_gui.plugins.assistant.providers import PROVIDERS, ProviderSettings
from editor_gui.plugins.assistant.strings import S
from editor_gui.plugins.assistant.ui import AssistantPanel
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.settings import load_settings, save_settings
from editor_gui.settings_window import Setting

if TYPE_CHECKING:
    from fastmcp import FastMCP

SETTINGS_PAGE = "assistant"
_STORE = "assistant"
# Product names stay as they are in every language.
_PROVIDER_NAMES = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI (GPT)",
    "xai": "xAI (Grok)",
}


def provider_items() -> Sequence[tuple[str, str]]:
    return [*_PROVIDER_NAMES.items(), ("custom", S.PROVIDER_OTHER)]


class AssistantPlugin:
    name = "assistant"

    def __init__(self, api: PluginAPI, server: Callable[[], FastMCP]) -> None:
        self._api = api
        self._log = Logger(api.log, "assistant")
        # Set by the app: opens the Settings window on a page.
        self.open_settings: Callable[[str], None] = lambda _page: None
        self.assistant = Assistant(server, self.provider_settings, self._log)
        self._panel = AssistantPanel(
            self.assistant,
            provider_name=self._provider_name,
            ready=self.ready,
            open_settings=lambda: self.open_settings(SETTINGS_PAGE),
        )
        self._panels = [
            PanelDefinition(
                name="assistant",
                label=S.PANEL_ASSISTANT,
                dock="RightSpace",
                render=self._panel.render,
            )
        ]

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        self.assistant.stop()

    # --- settings -----------------------------------------------------------

    def _stored(self) -> dict[str, str]:
        data = load_settings(_STORE)
        return {
            key: str(data.get(key) or "")
            for key in ("provider", "model", "api_key", "base_url")
        }

    def _store(self, key: str, value: object) -> None:
        data = load_settings(_STORE)
        data[key] = str(value).strip()
        save_settings(_STORE, data)

    def provider_settings(self) -> ProviderSettings:
        stored = self._stored()
        provider = (
            stored["provider"] if stored["provider"] in PROVIDERS else "anthropic"
        )
        return ProviderSettings(
            provider, stored["model"], stored["api_key"], stored["base_url"]
        )

    def ready(self) -> bool:
        """Whether a question can be sent: a key, and an address where the provider needs one."""
        settings = self.provider_settings()
        return bool(settings.api_key and settings.resolved_url())

    def _provider_name(self) -> str:
        provider = self.provider_settings().provider
        return dict(provider_items()).get(provider, provider)

    def _model_hint(self) -> str:
        default = PROVIDERS.get(self.provider_settings().provider, ("", ""))[1]
        return (
            S.SETTING_MODEL_HINT.format(model=default)
            if default
            else S.SETTING_MODEL_HINT_NONE
        )

    def settings(self) -> list[Setting]:
        """The AI Assistant page of the Settings window (and of settings_get/settings_set)."""
        return [
            Setting(
                "assistant_provider",
                lambda: S.SETTING_PROVIDER,
                "choice",
                get=lambda: self.provider_settings().provider,
                set=self._set_provider,
                choices=provider_items,
            ),
            Setting(
                "assistant_model",
                lambda: S.SETTING_MODEL,
                "text",
                get=lambda: self._stored()["model"],
                set=lambda value: self._store("model", value),
                hint=self._model_hint,
            ),
            Setting(
                "assistant_api_key",
                lambda: S.SETTING_API_KEY,
                "password",
                get=lambda: self._stored()["api_key"],
                set=lambda value: self._store("api_key", value),
                hint=lambda: S.SETTING_API_KEY_HINT,
                secret=True,
            ),
            Setting(
                "assistant_base_url",
                lambda: S.SETTING_BASE_URL,
                "text",
                get=lambda: self._stored()["base_url"],
                set=lambda value: self._store("base_url", value),
                hint=lambda: S.SETTING_BASE_URL_HINT,
            ),
        ]

    def _set_provider(self, value: object) -> None:
        provider = str(value)
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        if provider != self.provider_settings().provider:
            # Another provider's model name would not fit.
            self._store("model", "")
        self._store("provider", provider)
