from __future__ import annotations


def route_command_intent(content: str, has_images: bool) -> str | None:
    text = (content or "").strip().lower()
    if text.startswith("/help"):
        return "help_center"
    if text.startswith("/mcp") or text.startswith("/fetch") or text.startswith("/weather"):
        return "chat_manager"
    if text.startswith("/skill"):
        return "skill_manager"
    if has_images or text.startswith("/ledger"):
        return "ledger_manager"
    if text.startswith("/calendar"):
        return "schedule_manager"
    return None

