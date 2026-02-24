from __future__ import annotations

import re


_LIST_PATTERN = re.compile(r"(技能.*(列表|清单|有哪些|有啥|有哪|列出|查看|展示)|\bskills?\s*(list|show)\b)", re.IGNORECASE)
_CREATE_PATTERN = re.compile(r"(创建|新建|生成).*(技能)|\bskill\s*create\b", re.IGNORECASE)
_UPDATE_PATTERN = re.compile(r"(更新|修改|调整).*(技能)|\bskill\s*update\b", re.IGNORECASE)
_PUBLISH_PATTERN = re.compile(r"(发布|启用).*(技能)|\bskill\s*publish\b", re.IGNORECASE)
_DISABLE_PATTERN = re.compile(r"(停用|禁用|下线).*(技能)|\bskill\s*disable\b", re.IGNORECASE)
_DELETE_PATTERN = re.compile(r"(删除|删掉|移除|清空)|\bskill\s*delete\b", re.IGNORECASE)
_SHOW_PATTERN = re.compile(r"(查看|展示).*(技能.*详情)|\bskill\s*show\b", re.IGNORECASE)
_BROAD_SCOPE_PATTERN = re.compile(r"(我的|我创建的|全部|所有|全量).*(技能)", re.IGNORECASE)
_DELETE_ALL_HINT_PATTERN = re.compile(r"(全部|所有|全量|我的技能|我创建的技能|用户技能)", re.IGNORECASE)
_DELETE_CONFIRM_PATTERN = re.compile(r"(确认|确定|是的|执行删除|立即删除)", re.IGNORECASE)


def _clean_target(value: str) -> str:
    text = (value or "").strip().strip("`'\"。！，,.；;：:")
    return re.sub(r"\s+", " ", text)


def _extract_target_after_action(content: str, action_words: str) -> str:
    match = re.search(rf"(?:{action_words})\s*(?:技能)?\s*(.+)$", content, re.IGNORECASE)
    if not match:
        return ""
    candidate = _clean_target(match.group(1))
    if not candidate:
        return ""
    if _BROAD_SCOPE_PATTERN.search(candidate) or candidate in {"我的", "我创建的", "全部", "所有", "全量"}:
        return ""
    return candidate


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
        if action == "delete":
            target = remainder
            return {
                "action": "delete",
                "target": target,
                "request": target or content,
                "delete_scope": "single" if target else "unknown",
                "confirmed": bool(target),
                "clarification_needed": not bool(target),
            }
        return {"action": action, "target": remainder, "request": remainder}

    # Natural-language fallback for non-command skill management requests.
    if "list" in allowed_actions and _LIST_PATTERN.search(content):
        return {"action": "list", "target": "", "request": content}
    if "show" in allowed_actions and _SHOW_PATTERN.search(content):
        target = _extract_target_after_action(content, r"查看|展示|show")
        return {"action": "show", "target": target, "request": content}
    if "publish" in allowed_actions and _PUBLISH_PATTERN.search(content):
        target = _extract_target_after_action(content, r"发布|启用|publish")
        return {"action": "publish", "target": target, "request": content}
    if "disable" in allowed_actions and _DISABLE_PATTERN.search(content):
        target = _extract_target_after_action(content, r"停用|禁用|下线|disable")
        return {"action": "disable", "target": target, "request": content}
    if "delete" in allowed_actions and _DELETE_PATTERN.search(content):
        target = _extract_target_after_action(content, r"删除|删掉|移除|清空|delete")
        all_scope = bool(_DELETE_ALL_HINT_PATTERN.search(content))
        if target:
            return {
                "action": "delete",
                "target": target,
                "request": content,
                "delete_scope": "single",
                "confirmed": True,
                "clarification_needed": False,
            }
        if all_scope:
            return {
                "action": "delete",
                "target": "",
                "request": content,
                "delete_scope": "all",
                "confirmed": bool(_DELETE_CONFIRM_PATTERN.search(content)),
                "clarification_needed": False,
            }
        return {
            "action": "delete",
            "target": "",
            "request": content,
            "delete_scope": "unknown",
            "confirmed": False,
            "clarification_needed": True,
        }
    if "update" in allowed_actions and _UPDATE_PATTERN.search(content):
        target = _extract_target_after_action(content, r"更新|修改|调整|update")
        return {"action": "update", "target": target, "request": content}
    if "create" in allowed_actions and _CREATE_PATTERN.search(content):
        return {"action": "create", "target": "", "request": content}

    return {"action": "help", "request": content}


def skill_help_text() -> str:
    return (
        "技能命令：\n"
        "- `/skill list`\n"
        "- `/skill create <技能名或需求>`\n"
        "- `/skill update <slug> <更新需求>`\n"
        "- `/skill show <slug>`\n"
        "- `/skill publish <slug>`\n"
        "- `/skill disable <slug>`\n"
        "- `/skill delete <slug>`\n\n"
        "如何使用已创建的技能：\n"
        "1. 先创建并发布：`/skill create ...` -> `/skill publish <slug>`\n"
        "2. 发布后，不需要 `/skill use`，直接在普通对话里提需求即可自动生效。\n"
        "3. 建议在提问里明确写“按xx风格/按xx技能”，命中会更稳定。\n\n"
        "示例：\n"
        "- `请按小红书文案生成风格，写一条通勤穿搭文案（120字，带5个话题）`\n"
        "- `按我的翻译技能，把这段话翻成英文并保留术语`"
    )


def no_dynamic_skill_text() -> str:
    return "你还没有动态技能。可发送：`/skill create 翻译专家` 来创建。"


def publish_usage_text() -> str:
    return "请指定要发布的技能，例如：`/skill publish translator`"


def disable_usage_text() -> str:
    return "请指定要停用的技能，例如：`/skill disable translator`"


def delete_usage_text() -> str:
    return "请指定要删除的技能，例如：`/skill delete my-skill`"


def show_usage_text() -> str:
    return "请指定要查看的技能，例如：`/skill show builtin:translator` 或 `/skill show user:my-skill`。"


def update_usage_text() -> str:
    return "请指定要更新的技能，例如：`/skill update translator 新增术语保留规则`"


def builtin_update_block_text() -> str:
    return "内置技能不可直接更新，请先 `/skill create <新技能名>` 复制后再改。"


def builtin_delete_block_text() -> str:
    return "内置技能不可删除。你可以删除自己创建的 user 技能。"


def publish_hint_text(slug: str) -> str:
    return f"发送 `/skill publish {slug}` 后生效。"
