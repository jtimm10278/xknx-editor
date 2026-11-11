"""AI Assistant strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("assistant", _locale_dir)


class AssistantStrings:
    @property
    def PANEL_ASSISTANT(self) -> str:
        return _("AI Assistant")

    @property
    def INTRO(self) -> str:
        return _(
            "Ask about the open project. The assistant looks things up with the editor's own "
            "tools and never uses the bus."
        )

    @property
    def INPUT_HINT(self) -> str:
        return _("Ask about the open project…")

    @property
    def SEND(self) -> str:
        return _("Send")

    @property
    def STOP(self) -> str:
        return _("Stop")

    @property
    def NEW_CONVERSATION(self) -> str:
        return _("New Conversation")

    @property
    def ALLOW_CHANGES(self) -> str:
        return _("Allow Changes")

    @property
    def ALLOW_CHANGES_HINT(self) -> str:
        return _(
            "The assistant may then change the project; Edit > Undo takes each change back. It "
            "never deletes, exports, programs devices or uses the bus."
        )

    @property
    def WORKING(self) -> str:
        return _("Working…")

    @property
    def YOU(self) -> str:
        return _("You")

    @property
    def LOOKED_UP(self) -> str:
        return _("Used: {tool}")

    @property
    def PRIVACY(self) -> str:
        return _("What the assistant reads in the project is sent to {provider}.")

    @property
    def NOT_SET_UP(self) -> str:
        return _("Choose an AI provider and enter your API key in the settings first.")

    @property
    def OPEN_SETTINGS(self) -> str:
        return _("Open Settings")

    @property
    def NOTE_CHANGES(self) -> str:
        return _("Allow Changes was switched: a new conversation started.")

    @property
    def NOTE_STOPPED(self) -> str:
        return _("Stopped.")

    @property
    def ERROR_NO_KEY(self) -> str:
        return _("No API key is set.")

    @property
    def ERROR_NO_MODEL(self) -> str:
        return _("No model is set.")

    @property
    def ERROR_NO_URL(self) -> str:
        return _("No API address is set.")

    @property
    def ERROR_AUTH(self) -> str:
        return _("The provider did not accept the API key.")

    @property
    def ERROR_RATE(self) -> str:
        return _("The provider's rate limit was reached; try again shortly.")

    @property
    def ERROR_STATUS(self) -> str:
        return _("The provider answered with an error.")

    @property
    def ERROR_CONNECT(self) -> str:
        return _("The provider could not be reached.")

    @property
    def ERROR_UNEXPECTED(self) -> str:
        return _("The provider's answer could not be read.")

    @property
    def ERROR_REFUSED(self) -> str:
        return _("The model declined to answer this.")

    @property
    def ERROR_LENGTH(self) -> str:
        return _("The answer was cut off because it was too long.")

    @property
    def ERROR_STEPS(self) -> str:
        return _("Stopped after {count} steps without an answer.")

    @property
    def ERROR_INTERNAL(self) -> str:
        return _("The assistant failed.")

    @property
    def SETTINGS_PAGE(self) -> str:
        return _("AI Assistant")

    @property
    def SETTINGS_INTRO(self) -> str:
        return _(
            "The AI Assistant uses an AI provider of your choice with your own API key. The "
            "provider bills its use; a chat subscription (such as Claude Pro or ChatGPT Plus) does "
            "not include it."
        )

    @property
    def SETTING_PROVIDER(self) -> str:
        return _("Provider")

    @property
    def PROVIDER_OTHER(self) -> str:
        return _("Other (OpenAI-compatible)")

    @property
    def SETTING_MODEL(self) -> str:
        return _("Model")

    @property
    def SETTING_MODEL_HINT(self) -> str:
        return _("The model's name as the provider lists it. Empty: {model}.")

    @property
    def SETTING_MODEL_HINT_NONE(self) -> str:
        return _("The model's name as the provider lists it.")

    @property
    def SETTING_API_KEY(self) -> str:
        return _("API Key")

    @property
    def SETTING_API_KEY_HINT(self) -> str:
        return _(
            "From the provider's developer console. It is kept in the editor's settings on this "
            "computer."
        )

    @property
    def SETTING_BASE_URL(self) -> str:
        return _("API Address")

    @property
    def SETTING_BASE_URL_HINT(self) -> str:
        return _("Empty: the provider's own address. Needed for Other.")


S = AssistantStrings()
