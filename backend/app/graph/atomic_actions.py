from __future__ import annotations

from typing import Any, TypedDict


class AtomicExecutionPlan(TypedDict, total=False):
    kind: str
    node_action: str
    content: str
    tool_source: str
    tool_name: str
    tool_args: dict[str, Any]


def _string(value: Any) -> str:
    return str(value or "").strip()


def _float_text(value: Any) -> str:
    try:
        amount = float(value)
    except Exception:
        return ""
    if amount <= 0:
        return ""
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}"


def _build_ledger_create_text(args: dict[str, Any], fallback_input: str) -> str:
    amount = _float_text(args.get("amount"))
    item = _string(args.get("item"))
    category = _string(args.get("category"))
    transaction_time = _string(args.get("transaction_time"))
    if amount and item:
        base = f"{item}{amount}元"
        if category:
            base += f" 分类{category}"
        if transaction_time:
            base = f"{transaction_time} {base}"
        return base
    return _string(args.get("input")) or fallback_input


def _build_schedule_create_text(args: dict[str, Any], fallback_input: str) -> str:
    content = _string(args.get("content")) or _string(args.get("title"))
    run_at_local = _string(args.get("run_at_local")) or _string(args.get("time"))
    if content and run_at_local:
        return f"{run_at_local}提醒我{content}"
    if content:
        return f"提醒我{content}"
    return _string(args.get("input")) or fallback_input


def _build_schedule_query_text(args: dict[str, Any]) -> str:
    scope = _string(args.get("scope")).lower()
    if scope in {"today", "today_all", "今天"}:
        return "/calendar today"
    if scope in {"tomorrow", "明天"}:
        return "/calendar tomorrow"
    if scope in {"week", "本周"}:
        return "/calendar week"
    if scope in {"month", "本月"}:
        return "/calendar month"
    date_text = _string(args.get("date"))
    if date_text:
        return f"/calendar {date_text}"
    return "/calendar today"


def build_atomic_action_catalog(runtime_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "action": "atomic.ledger.create",
            "domain": "ledger",
            "description": "Create a ledger record from structured args. Best for explicit amount/item/category.",
            "required_args": {"amount": "number", "item": "string"},
            "optional_args": {"category": "string", "transaction_time": "string(YYYY-MM-DD HH:mm)"},
            "example": {"amount": 30, "item": "晚饭", "category": "餐饮"},
        },
        {
            "action": "atomic.ledger.list_recent",
            "domain": "ledger",
            "description": "List recent ledger records for the current user.",
            "required_args": {},
            "optional_args": {"limit": "number(1-50)"},
            "example": {"limit": 10},
        },
        {
            "action": "atomic.ledger.update_by_id",
            "domain": "ledger",
            "description": "Update one ledger row by id.",
            "required_args": {"id": "number", "amount": "number"},
            "optional_args": {"category": "string", "item": "string"},
            "example": {"id": 12, "amount": 35, "category": "餐饮", "item": "午饭"},
        },
        {
            "action": "atomic.ledger.delete_by_id",
            "domain": "ledger",
            "description": "Delete one ledger row by id.",
            "required_args": {"id": "number"},
            "optional_args": {},
            "example": {"id": 12},
        },
        {
            "action": "atomic.ledger.delete_latest",
            "domain": "ledger",
            "description": "Delete the latest ledger row of current user.",
            "required_args": {},
            "optional_args": {},
            "example": {},
        },
        {
            "action": "atomic.schedule.create",
            "domain": "schedule",
            "description": "Create reminder/schedule with structured content and target time.",
            "required_args": {"content": "string"},
            "optional_args": {
                "run_at_local": "string(YYYY-MM-DD HH:mm or natural time text)",
                "priority": "low|medium|high|critical",
            },
            "example": {"content": "开会", "run_at_local": "明天 12:00"},
        },
        {
            "action": "atomic.schedule.query",
            "domain": "schedule",
            "description": "Query schedules by scope/date.",
            "required_args": {},
            "optional_args": {"scope": "today|tomorrow|week|month", "date": "YYYY-MM-DD"},
            "example": {"scope": "tomorrow"},
        },
        {
            "action": "atomic.schedule.update",
            "domain": "schedule",
            "description": "Update schedule by natural-language target and new values.",
            "required_args": {"target": "string"},
            "optional_args": {"content": "string", "run_at_local": "string"},
            "example": {"target": "明天中午会议", "run_at_local": "明天 13:00"},
        },
        {
            "action": "atomic.schedule.delete",
            "domain": "schedule",
            "description": "Delete schedule(s) by target phrase or id in natural language.",
            "required_args": {"target": "string"},
            "optional_args": {},
            "example": {"target": "明天的所有提醒"},
        },
        {
            "action": "atomic.skill.list",
            "domain": "skill",
            "description": "List all skills (builtin + user).",
            "required_args": {},
            "optional_args": {},
            "example": {},
        },
        {
            "action": "atomic.skill.show",
            "domain": "skill",
            "description": "Show one skill detail by slug.",
            "required_args": {"slug": "string"},
            "optional_args": {"source": "builtin|user"},
            "example": {"slug": "translator", "source": "builtin"},
        },
        {
            "action": "atomic.skill.publish",
            "domain": "skill",
            "description": "Publish a draft skill by slug.",
            "required_args": {"slug": "string"},
            "optional_args": {},
            "example": {"slug": "my-skill"},
        },
        {
            "action": "atomic.skill.disable",
            "domain": "skill",
            "description": "Disable a skill by slug.",
            "required_args": {"slug": "string"},
            "optional_args": {},
            "example": {"slug": "my-skill"},
        },
        {
            "action": "atomic.help.show",
            "domain": "help",
            "description": "Show help and command usage.",
            "required_args": {},
            "optional_args": {},
            "example": {},
        },
        {
            "action": "atomic.chat.respond",
            "domain": "chat",
            "description": "Use chat manager for general conversation or writing task.",
            "required_args": {"input": "string"},
            "optional_args": {},
            "example": {"input": "帮我总结下面这段话"},
        },
        {
            "action": "atomic.tool.now_time",
            "domain": "tool",
            "description": "Get current time using builtin now_time tool.",
            "required_args": {},
            "optional_args": {"timezone": "IANA timezone"},
            "example": {"timezone": "Asia/Shanghai"},
        },
        {
            "action": "atomic.tool.fetch_url",
            "domain": "tool",
            "description": "Fetch URL content through builtin fetch_url.",
            "required_args": {"url": "string"},
            "optional_args": {"max_length": "number", "start_index": "number", "raw": "boolean"},
            "example": {"url": "https://example.com"},
        },
        {
            "action": "atomic.tool.list",
            "domain": "tool",
            "description": "List currently available external tools.",
            "required_args": {},
            "optional_args": {},
            "example": {},
        },
        {
            "action": "atomic.tool.call",
            "domain": "tool",
            "description": "Call one external tool by name and arguments.",
            "required_args": {"tool_name": "string"},
            "optional_args": {"arguments": "object"},
            "example": {"tool_name": "maps_weather", "arguments": {"city": "上海"}},
        },
    ]

    for node_name in ("ledger_manager", "schedule_manager", "chat_manager", "skill_manager", "help_center"):
        rows.append(
            {
                "action": f"node.{node_name}",
                "domain": "node_handoff",
                "description": "Fallback handoff to existing node with args.input as message content.",
                "required_args": {"input": "string"},
                "optional_args": {"image_urls": "string[]"},
                "example": {"input": "示例输入"},
            }
        )

    for tool in runtime_tools:
        source = _string(tool.get("source")).lower()
        name = _string(tool.get("name"))
        desc = _string(tool.get("description")) or "Runtime tool call."
        if not name:
            continue
        rows.append(
            {
                "action": f"tool.{name}",
                "domain": "runtime_tool",
                "description": f"Call runtime tool `{name}` (source={source}). {desc}",
                "required_args": {},
                "optional_args": {"*": "Follow tool spec"},
                "example": {},
            }
        )

    rows.append(
        {
            "action": "logic.weather_rain_check",
            "domain": "logic",
            "description": (
                "Determine whether precipitation is expected from weather output. "
                "Use before conditional reminder creation."
            ),
            "required_args": {},
            "optional_args": {
                "weather_step": "step_id with weather tool output",
                "weather_output": "raw weather text",
                "target_date": "YYYY-MM-DD",
                "period": "day|night|afternoon|evening|all",
            },
            "example": {"weather_step": "step_weather", "target_date": "2026-02-23", "period": "afternoon"},
        }
    )
    return rows


def resolve_atomic_action_plan(action: str, args: dict[str, Any], fallback_input: str) -> AtomicExecutionPlan | None:
    key = _string(action).lower()
    if not key.startswith("atomic."):
        return None

    if key == "atomic.help.show":
        return {"kind": "node", "node_action": "node.help_center", "content": "/help"}

    if key == "atomic.chat.respond":
        return {
            "kind": "node",
            "node_action": "node.chat_manager",
            "content": _string(args.get("input")) or fallback_input,
        }

    if key == "atomic.ledger.create":
        return {
            "kind": "node",
            "node_action": "node.ledger_manager",
            "content": _build_ledger_create_text(args, fallback_input),
        }

    if key == "atomic.ledger.list_recent":
        return {"kind": "node", "node_action": "node.ledger_manager", "content": "/ledger list"}

    if key == "atomic.ledger.update_by_id":
        ledger_id = _string(args.get("id"))
        amount = _float_text(args.get("amount"))
        if not ledger_id or not amount:
            return {
                "kind": "node",
                "node_action": "node.ledger_manager",
                "content": _string(args.get("input")) or fallback_input,
            }
        category = _string(args.get("category"))
        item = _string(args.get("item"))
        tail = " ".join([part for part in (category, item) if part]).strip()
        cmd = f"/ledger update {ledger_id} {amount}"
        if tail:
            cmd = f"{cmd} {tail}"
        return {"kind": "node", "node_action": "node.ledger_manager", "content": cmd}

    if key == "atomic.ledger.delete_by_id":
        ledger_id = _string(args.get("id"))
        if ledger_id:
            return {
                "kind": "node",
                "node_action": "node.ledger_manager",
                "content": f"/ledger delete {ledger_id}",
            }
        return {
            "kind": "node",
            "node_action": "node.ledger_manager",
            "content": _string(args.get("input")) or fallback_input,
        }

    if key == "atomic.ledger.delete_latest":
        return {"kind": "node", "node_action": "node.ledger_manager", "content": "/ledger delete latest"}

    if key == "atomic.schedule.create":
        return {
            "kind": "node",
            "node_action": "node.schedule_manager",
            "content": _build_schedule_create_text(args, fallback_input),
        }

    if key == "atomic.schedule.query":
        return {
            "kind": "node",
            "node_action": "node.schedule_manager",
            "content": _build_schedule_query_text(args),
        }

    if key == "atomic.schedule.update":
        target = _string(args.get("target"))
        new_content = _string(args.get("content"))
        run_at_local = _string(args.get("run_at_local"))
        parts: list[str] = []
        if target:
            parts.append(f"把{target}")
        if new_content:
            parts.append(f"改成{new_content}")
        if run_at_local:
            parts.append(f"时间改到{run_at_local}")
        text = "，".join(parts).strip("，")
        return {
            "kind": "node",
            "node_action": "node.schedule_manager",
            "content": text or (_string(args.get("input")) or fallback_input),
        }

    if key == "atomic.schedule.delete":
        target = _string(args.get("target"))
        text = f"删除{target}" if target else (_string(args.get("input")) or fallback_input)
        return {"kind": "node", "node_action": "node.schedule_manager", "content": text}

    if key == "atomic.skill.list":
        return {"kind": "node", "node_action": "node.skill_manager", "content": "/skill list"}

    if key == "atomic.skill.show":
        slug = _string(args.get("slug"))
        source = _string(args.get("source"))
        target = f"{source}:{slug}" if source and slug else slug
        text = f"/skill show {target}".strip()
        return {"kind": "node", "node_action": "node.skill_manager", "content": text}

    if key == "atomic.skill.publish":
        slug = _string(args.get("slug"))
        text = f"/skill publish {slug}".strip()
        return {"kind": "node", "node_action": "node.skill_manager", "content": text}

    if key == "atomic.skill.disable":
        slug = _string(args.get("slug"))
        text = f"/skill disable {slug}".strip()
        return {"kind": "node", "node_action": "node.skill_manager", "content": text}

    if key == "atomic.tool.now_time":
        return {
            "kind": "tool",
            "tool_source": "builtin",
            "tool_name": "now_time",
            "tool_args": {"timezone": _string(args.get("timezone")) or "Asia/Shanghai"},
        }

    if key == "atomic.tool.fetch_url":
        tool_args: dict[str, Any] = {}
        url = _string(args.get("url"))
        if url:
            tool_args["url"] = url
        if "max_length" in args:
            tool_args["max_length"] = args.get("max_length")
        if "start_index" in args:
            tool_args["start_index"] = args.get("start_index")
        if "raw" in args:
            tool_args["raw"] = args.get("raw")
        return {
            "kind": "tool",
            "tool_source": "builtin",
            "tool_name": "fetch_url",
            "tool_args": tool_args,
        }

    if key == "atomic.tool.list":
        return {
            "kind": "tool",
            "tool_source": "builtin",
            "tool_name": "tool_list",
            "tool_args": {},
        }

    if key == "atomic.tool.call":
        tool_name = _string(args.get("tool_name")) or _string(args.get("name"))
        tool_arguments = args.get("arguments")
        if not isinstance(tool_arguments, dict):
            tool_arguments = {}
        return {
            "kind": "tool",
            "tool_source": "builtin",
            "tool_name": "tool_call",
            "tool_args": {"tool_name": tool_name, "arguments": tool_arguments},
        }

    return None
