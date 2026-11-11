"""The AI Assistant: a chat panel that answers questions about the open project with an AI model
of the user's choice (their own API key), using the editor's own MCP tools in-process."""

from editor_gui.plugins.assistant.plugin import AssistantPlugin

__all__ = ["AssistantPlugin"]
