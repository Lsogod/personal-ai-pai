from __future__ import annotations


def parse_skill_command_fallback(text: str, allowed_actions: set[str]) -> dict:
    content = (text or "").strip()
    if content.startswith("/skill"):
        parts = content.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "help"
        remainder = parts[2].strip() if len(parts) > 2 else ""
        if action not in allowed_actions:
            return {"action": "help", "request": remainder or content}
        if action == "update":
            update_parts = remainder.split(maxsplit=1)
            return {
                "action": action,
                "target": update_parts[0] if update_parts else "",
                "request": update_parts[1] if len(update_parts) > 1 else "",
            }
        return {"action": action, "target": remainder, "request": remainder}

    return {"action": "help", "request": content}


def skill_help_text() -> str:
    return (
        "技能命令：\n"
        "- `/skill list`\n"
        "- `/skill create <技能名或需求>`\n"
        "- `/skill update <slug> <更新需求>`\n"
        "- `/skill show <slug>`\n"
        "- `/skill publish <slug>`\n"
        "- `/skill disable <slug>`"
    )


def no_dynamic_skill_text() -> str:
    return "你还没有动态技能。可发送：`/skill create 翻译专家` 来创建。"


def publish_usage_text() -> str:
    return "请指定要发布的技能，例如：`/skill publish translator`"


def disable_usage_text() -> str:
    return "请指定要停用的技能，例如：`/skill disable translator`"


def show_usage_text() -> str:
    return "请指定要查看的技能，例如：`/skill show builtin:translator` 或 `/skill show user:my-skill`。"


def update_usage_text() -> str:
    return "请指定要更新的技能，例如：`/skill update translator 新增术语保留规则`"


def builtin_update_block_text() -> str:
    return "内置技能不可直接更新，请先 `/skill create <新技能名>` 复制后再改。"


def publish_hint_text(slug: str) -> str:
    return f"发送 `/skill publish {slug}` 后生效。"
