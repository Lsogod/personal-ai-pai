from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain_core.tools import BaseTool, tool

from app.services.tool_executor import execute_capability_with_usage


AuditHook = Callable[
    [str, str, dict[str, Any], bool, int, str, str],
    Awaitable[None],
]


@dataclass
class ToolInvocationContext:
    user_id: int | None
    platform: str
    conversation_id: int | None
    audit_hook: AuditHook | None = None


async def _run_tool(
    *,
    context: ToolInvocationContext,
    source: str,
    name: str,
    args: dict[str, Any],
) -> str:
    result = await execute_capability_with_usage(
        source=source,
        name=name,
        args=args,
        user_id=context.user_id,
        platform=context.platform,
        conversation_id=context.conversation_id,
    )
    ok = bool(result.get("ok"))
    output = str(result.get("output") or "")
    error = str(result.get("error") or "")
    latency_ms = int(result.get("latency_ms") or 0)
    if context.audit_hook is not None:
        await context.audit_hook(source, name, args, ok, latency_ms, output, error)
    if ok:
        return output
    return error or f"tool `{name}` failed"


def build_langchain_tools(
    *,
    context: ToolInvocationContext,
    enabled_tool_names: set[str] | None = None,
) -> list[BaseTool]:
    allowed = {name.lower().strip() for name in (enabled_tool_names or set()) if str(name).strip()}

    def _enabled(name: str) -> bool:
        if not allowed:
            return True
        return name.lower().strip() in allowed

    tools: list[BaseTool] = []

    if _enabled("now_time"):
        @tool("now_time")
        async def now_time_tool(timezone: str = "Asia/Shanghai") -> str:
            """按时区名称返回当前本地时间，例如：Asia/Shanghai。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="now_time",
                args={"timezone": timezone},
            )

        tools.append(now_time_tool)

    if _enabled("fetch_url"):
        @tool("fetch_url")
        async def fetch_url_tool(
            url: str,
            max_length: int = 5000,
            start_index: int = 0,
            raw: bool = False,
        ) -> str:
            """抓取网页或 JSON 内容。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="fetch_url",
                args={
                    "url": url,
                    "max_length": max_length,
                    "start_index": start_index,
                    "raw": raw,
                },
            )

        tools.append(fetch_url_tool)

    if _enabled("mcp_list_tools"):
        @tool("mcp_list_tools")
        async def mcp_list_tools_tool() -> str:
            """列出当前可用的外部工具。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="tool_list",
                args={},
            )

        tools.append(mcp_list_tools_tool)

    if _enabled("mcp_call_tool"):
        @tool("mcp_call_tool")
        async def mcp_call_tool_tool(tool_name: str, arguments_json: str = "{}") -> str:
            """按名称调用外部工具，并传入 JSON 参数。"""
            name = (tool_name or "").strip()
            args: dict[str, Any] = {}
            try:
                parsed = json.loads(arguments_json or "{}")
                if isinstance(parsed, dict):
                    args = parsed
            except Exception:
                args = {}
            return await _run_tool(
                context=context,
                source="builtin",
                name="tool_call",
                args={"tool_name": name, "arguments": args},
            )

        tools.append(mcp_call_tool_tool)

    if _enabled("maps_weather"):
        @tool("maps_weather")
        async def maps_weather_tool(city: str = "", adcode: str = "") -> str:
            """按城市名或 adcode 查询天气。"""
            payload: dict[str, Any] = {}
            if city.strip():
                payload["city"] = city.strip()
            elif adcode.strip():
                payload["adcode"] = adcode.strip()
            else:
                payload["city"] = ""
            return await _run_tool(
                context=context,
                source="mcp",
                name="maps_weather",
                args=payload,
            )

        tools.append(maps_weather_tool)

    if _enabled("analyze_receipt"):
        @tool("analyze_receipt")
        async def analyze_receipt_tool(image_ref: str) -> str:
            """分析小票或支付图片，并返回结构化提取 JSON。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="analyze_receipt",
                args={"image_ref": image_ref},
            )

        tools.append(analyze_receipt_tool)

    if _enabled("ledger_text2sql"):
        @tool("ledger_text2sql")
        async def ledger_text2sql_tool(message: str, conversation_context: str = "") -> str:
            """通过安全的 text2sql 流程执行自然语言账单增删改查。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_text2sql",
                args={
                    "message": message,
                    "conversation_context": conversation_context,
                },
            )

        tools.append(ledger_text2sql_tool)

    if _enabled("ledger_insert"):
        @tool("ledger_insert")
        async def ledger_insert_tool(
            amount: float,
            category: str,
            item: str,
            transaction_date: str = "",
            image_url: str = "",
        ) -> str:
            """插入一条账单记录，并返回 JSON 行数据。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_insert",
                args={
                    "amount": amount,
                    "category": category,
                    "item": item,
                    "transaction_date": transaction_date,
                    "image_url": image_url,
                },
            )

        tools.append(ledger_insert_tool)

    if _enabled("ledger_update"):
        @tool("ledger_update")
        async def ledger_update_tool(
            ledger_id: int,
            amount: float | None = None,
            category: str = "",
            item: str = "",
            transaction_date: str = "",
        ) -> str:
            """更新一条账单记录，并返回 JSON 行数据。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_update",
                args={
                    "ledger_id": ledger_id,
                    "amount": amount,
                    "category": category,
                    "item": item,
                    "transaction_date": transaction_date,
                },
            )

        tools.append(ledger_update_tool)

    if _enabled("ledger_delete"):
        @tool("ledger_delete")
        async def ledger_delete_tool(ledger_id: int) -> str:
            """删除一条账单记录，并返回被删除的 JSON 行数据。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_delete",
                args={"ledger_id": ledger_id},
            )

        tools.append(ledger_delete_tool)

    if _enabled("ledger_get_latest"):
        @tool("ledger_get_latest")
        async def ledger_get_latest_tool() -> str:
            """返回最新一条账单的 JSON；如果没有则返回空 JSON。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_get_latest",
                args={},
            )

        tools.append(ledger_get_latest_tool)

    if _enabled("ledger_list_recent"):
        @tool("ledger_list_recent")
        async def ledger_list_recent_tool(limit: int = 10) -> str:
            """返回最近账单记录的 JSON 列表。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_list_recent",
                args={"limit": limit},
            )

        tools.append(ledger_list_recent_tool)

    if _enabled("ledger_list"):
        @tool("ledger_list")
        async def ledger_list_tool(
            limit: int = 100,
            start_at: str = "",
            end_at: str = "",
            category: str = "",
            item_like: str = "",
            order: str = "desc",
            ledger_ids: list[int] | None = None,
        ) -> str:
            """按可选的 id、日期、分类、摘要条件列出账单，并返回 JSON 列表。"""
            safe_ids: list[int] = []
            for item in list(ledger_ids or []):
                try:
                    safe_ids.append(int(item))
                except Exception:
                    continue
            return await _run_tool(
                context=context,
                source="builtin",
                name="ledger_list",
                args={
                    "limit": limit,
                    "start_at": start_at,
                    "end_at": end_at,
                    "category": category,
                    "item_like": item_like,
                    "order": order,
                    "ledger_ids": safe_ids,
                },
            )

        tools.append(ledger_list_tool)

    if _enabled("conversation_current"):
        @tool("conversation_current")
        async def conversation_current_tool() -> str:
            """返回当前激活会话的 JSON 对象。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="conversation_current",
                args={},
            )

        tools.append(conversation_current_tool)

    if _enabled("conversation_list"):
        @tool("conversation_list")
        async def conversation_list_tool(limit: int = 20) -> str:
            """返回带有激活标记的会话 JSON 数组。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="conversation_list",
                args={"limit": limit},
            )

        tools.append(conversation_list_tool)

    if _enabled("memory_list"):
        @tool("memory_list")
        async def memory_list_tool(limit: int = 120) -> str:
            """返回长期记忆的 JSON 数组。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="memory_list",
                args={"limit": limit},
            )

        tools.append(memory_list_tool)

    if _enabled("memory_save"):
        @tool("memory_save")
        async def memory_save_tool(
            content: str,
            memory_type: str = "fact",
            importance: int = 3,
            confidence: float = 1.0,
            ttl_days: int = 180,
            key: str = "",
        ) -> str:
            """将用户明确要求记住的信息写入长期记忆，并返回 JSON 结果。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="memory_save",
                args={
                    "content": content,
                    "memory_type": memory_type,
                    "importance": importance,
                    "confidence": confidence,
                    "ttl_days": ttl_days,
                    "key": key,
                },
            )

        tools.append(memory_save_tool)

    if _enabled("memory_append"):
        @tool("memory_append")
        async def memory_append_tool(
            content: str,
            memory_id: int | None = None,
            memory_key: str = "",
            target_hint: str = "",
            memory_type: str = "",
            separator: str = "；",
            importance: int | None = None,
            confidence: float | None = None,
            ttl_days: int | None = None,
        ) -> str:
            """向一条已有长期记忆追加内容，并返回 JSON 结果。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="memory_append",
                args={
                    "content": content,
                    "memory_id": memory_id,
                    "memory_key": memory_key,
                    "target_hint": target_hint,
                    "memory_type": memory_type,
                    "separator": separator,
                    "importance": importance,
                    "confidence": confidence,
                    "ttl_days": ttl_days,
                },
            )

        tools.append(memory_append_tool)

    if _enabled("memory_delete"):
        @tool("memory_delete")
        async def memory_delete_tool(
            memory_id: int | None = None,
            memory_key: str = "",
            target_hint: str = "",
            memory_type: str = "",
        ) -> str:
            """删除一条已有长期记忆，并返回 JSON 结果。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="memory_delete",
                args={
                    "memory_id": memory_id,
                    "memory_key": memory_key,
                    "target_hint": target_hint,
                    "memory_type": memory_type,
                },
            )

        tools.append(memory_delete_tool)

    if _enabled("schedule_get_latest"):
        @tool("schedule_get_latest")
        async def schedule_get_latest_tool() -> str:
            """返回最新一条日程提醒的 JSON；如果没有则返回空 JSON。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="schedule_get_latest",
                args={},
            )

        tools.append(schedule_get_latest_tool)

    if _enabled("schedule_list_recent"):
        @tool("schedule_list_recent")
        async def schedule_list_recent_tool(limit: int = 10) -> str:
            """返回最近日程提醒记录的 JSON 列表。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="schedule_list_recent",
                args={"limit": limit},
            )

        tools.append(schedule_list_recent_tool)

    if _enabled("schedule_insert"):
        @tool("schedule_insert")
        async def schedule_insert_tool(
            content: str,
            trigger_time: str,
            status: str = "PENDING",
            job_id: str = "",
        ) -> str:
            """创建一条日程提醒，并返回 JSON 行数据。trigger_time 支持绝对时间（YYYY-MM-DD HH:MM:SS）和相对时间（如 '10秒后'、'5分钟后'、'2小时后'、'3天后'）。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="schedule_insert",
                args={
                    "content": content,
                    "trigger_time": trigger_time,
                    "status": status,
                    "job_id": job_id,
                },
            )

        tools.append(schedule_insert_tool)

    if _enabled("schedule_update"):
        @tool("schedule_update")
        async def schedule_update_tool(
            schedule_id: int,
            content: str = "",
            trigger_time: str = "",
            status: str = "",
        ) -> str:
            """更新一条日程提醒，并返回 JSON 行数据。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="schedule_update",
                args={
                    "schedule_id": schedule_id,
                    "content": content,
                    "trigger_time": trigger_time,
                    "status": status,
                },
            )

        tools.append(schedule_update_tool)

    if _enabled("schedule_delete"):
        @tool("schedule_delete")
        async def schedule_delete_tool(schedule_id: int) -> str:
            """删除一条日程提醒，并返回被删除的 JSON 行数据。"""
            return await _run_tool(
                context=context,
                source="builtin",
                name="schedule_delete",
                args={"schedule_id": schedule_id},
            )

        tools.append(schedule_delete_tool)

    if _enabled("schedule_list"):
        @tool("schedule_list")
        async def schedule_list_tool(
            status: str = "all",
            start_at: str = "",
            end_at: str = "",
            limit: int = 100,
            content_like: str = "",
            order: str = "asc",
            schedule_ids: list[int] | None = None,
        ) -> str:
            """按可选状态和时间窗口列出日程，并返回 JSON 列表。"""
            safe_ids: list[int] = []
            for item in list(schedule_ids or []):
                try:
                    safe_ids.append(int(item))
                except Exception:
                    continue
            return await _run_tool(
                context=context,
                source="builtin",
                name="schedule_list",
                args={
                    "status": status,
                    "start_at": start_at,
                    "end_at": end_at,
                    "limit": limit,
                    "content_like": content_like,
                    "order": order,
                    "schedule_ids": safe_ids,
                },
            )

        tools.append(schedule_list_tool)

    if _enabled("update_user_profile"):
        @tool("update_user_profile")
        async def update_user_profile_tool(
            nickname: str = "",
            ai_name: str = "",
            ai_emoji: str = "",
        ) -> str:
            """更新用户档案。可设置用户昵称(nickname)、AI助手名称(ai_name)、AI助手表情(ai_emoji)。仅传入需要修改的字段。"""
            from app.db.session import AsyncSessionLocal
            from app.models.user import User
            from app.services.memory import deactivate_identity_memories_for_user

            user_id = context.user_id
            if not user_id:
                return "未找到用户信息。"
            async with AsyncSessionLocal() as session:
                user = await session.get(User, user_id)
                if not user:
                    return "未找到用户信息。"
                changed = False
                if nickname and nickname != str(user.nickname or "").strip():
                    user.nickname = nickname
                    changed = True
                if ai_name and ai_name != str(user.ai_name or "").strip():
                    user.ai_name = ai_name
                    changed = True
                if ai_emoji and ai_emoji != str(user.ai_emoji or "").strip():
                    user.ai_emoji = ai_emoji
                    changed = True
                if changed:
                    await deactivate_identity_memories_for_user(session, user_id=user_id)
                    session.add(user)
                    await session.commit()
                parts: list[str] = []
                if nickname:
                    parts.append(f"昵称已更新为{nickname}")
                if ai_name:
                    parts.append(f"助手名称已更新为{ai_name}")
                if ai_emoji:
                    parts.append(f"助手表情已更新为{ai_emoji}")
                return "，".join(parts) + "。" if parts else "未检测到需要修改的字段。"

        tools.append(update_user_profile_tool)

    if _enabled("query_user_profile"):
        @tool("query_user_profile")
        async def query_user_profile_tool() -> str:
            """查询当前用户的完整档案信息（昵称、助手名称、表情、平台、邮箱等）。"""
            from app.db.session import AsyncSessionLocal
            from app.models.user import User

            user_id = context.user_id
            if not user_id:
                return "未找到用户信息。"
            async with AsyncSessionLocal() as session:
                user = await session.get(User, user_id)
                if not user:
                    return "未找到用户信息。"
                nickname = str(user.nickname or "").strip() or "未设置"
                ai_name = str(user.ai_name or "").strip() or "AI 助手"
                ai_emoji = str(user.ai_emoji or "").strip() or "🤖"
                platform = str(user.platform or "").strip() or "unknown"
                email = str(user.email or "").strip() or "未绑定"
                return (
                    f"昵称：{nickname}\n"
                    f"助手名称：{ai_name}\n"
                    f"助手表情：{ai_emoji}\n"
                    f"平台：{platform}\n"
                    f"邮箱：{email}"
                )

        tools.append(query_user_profile_tool)

    return tools
