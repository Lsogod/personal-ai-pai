from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, TypedDict
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.conversation import Conversation
from app.models.ledger import Ledger
from app.models.memory import LongTermMemory
from app.models.message import Message
from app.models.reminder_delivery import ReminderDelivery
from app.models.schedule import Schedule
from app.models.user import User
from app.services.admin_tools import is_tool_enabled
from app.services.conversations import ensure_active_conversation, list_conversations
from app.services.memory import (
    find_active_long_term_memory,
    list_long_term_memories,
    mark_long_term_memory_vector_dirty,
    upsert_long_term_memories,
)
from app.services.mcp_fetch import (
    get_mcp_client_for_tool,
    get_mcp_fetch_client,
    get_mcp_search_fallback_client,
)
from app.services.memory_vector_store import delete_memory_vectors
from app.services.runtime_context import get_scheduler, get_session, get_tool_message_id
from app.services.scheduler_tasks import send_reminder_job
from app.services.tool_registry import (
    get_allowed_mcp_tool_names_for,
    is_mcp_tool_allowed,
    list_runtime_tool_metas,
)
from app.services.user_mcp_tools import (
    get_user_mcp_langchain_tools,
    get_user_mcp_tool_display_names,
)
from app.services.usage import enqueue_tool_usage
from app.tools.finance import (
    delete_ledger,
    get_latest_ledger,
    insert_ledger,
    list_recent_ledgers,
    update_ledger,
)
from app.tools.ledger_text2sql import (
    commit_write_by_ids_text2sql,
    plan_write_preview_text2sql,
    try_execute_ledger_text2sql,
)
from app.tools.vision import analyze_image, analyze_receipt


class ToolExecResult(TypedDict):
    ok: bool
    source: str
    name: str
    output: str
    output_data: Any | None
    error: str
    latency_ms: int


BUILTIN_TOOL_ALIAS: dict[str, str] = {
    "mcp_list_tools": "tool_list",
    "mcp_call_tool": "tool_call",
}

COMMUNITY_DOMAIN_HINTS: tuple[str, ...] = (
    "zhihu.com",
    "tieba.baidu.com",
    "xiaohongshu.com",
    "weibo.com",
    "bilibili.com",
    "douyin.com",
)

AUTHORITATIVE_DOMAIN_HINTS: tuple[str, ...] = (
    ".gov.cn",
    ".edu.cn",
    "gov.cn",
    "edu.cn",
    "people.com.cn",
    "xinhuanet.com",
    "cctv.com",
)

DETAIL_QUERY_HINTS: tuple[str, ...] = (
    "新闻",
    "热点",
    "怎么回事",
    "发生了什么",
    "原因",
    "详情",
    "详细",
    "价格",
    "售价",
    "多少钱",
    "配置",
    "参数",
    "发布",
    "发布时间",
    "上市",
    "官方",
    "官网",
)

SUBJECTIVE_QUERY_HINTS: tuple[str, ...] = (
    "体验",
    "评价",
    "怎么看",
    "怎么样",
    "如何看待",
    "哪个好",
    "好不好",
    "值得",
)

INSTITUTION_QUERY_HINTS: tuple[str, ...] = (
    "学校",
    "中学",
    "小学",
    "大学",
    "学院",
    "医院",
    "公司",
    "集团",
    "教育局",
    "政府",
    "研究院",
    "协会",
    "中心官网",
    "一中",
    "二中",
)


def _to_client_tz_iso(value: datetime | None, *, assume_utc: bool) -> str:
    if value is None:
        return ""
    tz = ZoneInfo(get_settings().timezone)
    if value.tzinfo is None:
        source_tz = ZoneInfo("UTC") if assume_utc else tz
        value = value.replace(tzinfo=source_tz)
    return value.astimezone(tz).isoformat(timespec="seconds")


def _render_now_time(timezone: str) -> str:
    tz = (timezone or "").strip() or "Asia/Shanghai"
    try:
        now = datetime.now(ZoneInfo(tz))
        return f"{tz} 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
    except Exception:
        now = datetime.utcnow()
        return f"UTC 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"


def _render_mcp_tool_rows(rows: list[dict[str, Any]], *, user_id: int | None = None) -> str:
    if not rows:
        return "当前无可用外部工具。"
    user_labels = get_user_mcp_tool_display_names(int(user_id or 0)) if user_id else {}
    system_lines: list[str] = []
    user_lines: list[str] = []
    for item in rows:
        name = str(item.get("name") or "").strip() or "unknown"
        desc = str(item.get("description") or "").strip() or "无描述"
        enabled = bool(item.get("enabled") is True)
        display_name = user_labels.get(name, name)
        line = f"- {display_name} | enabled={str(enabled).lower()} | {desc}"
        if name.startswith("umcp_"):
            user_lines.append(line)
        else:
            system_lines.append(line)

    sections: list[str] = []
    if system_lines:
        sections.append("系统外部工具:\n" + "\n".join(system_lines))
    if user_lines:
        sections.append("我的 MCP 工具:\n" + "\n".join(user_lines))
    return "\n\n".join(sections) if sections else "当前无可用外部工具。"


async def _list_user_mcp_tool_rows(user_id: int | None) -> list[dict[str, Any]]:
    try:
        uid = int(user_id or 0)
    except Exception:
        uid = 0
    if uid <= 0:
        return []

    async with AsyncSessionLocal() as session:
        tools = await get_user_mcp_langchain_tools(uid, session)

    rows: list[dict[str, Any]] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "source": "mcp",
                "description": str(getattr(tool, "description", "") or "").strip() or "User MCP tool",
                "enabled": True,
            }
        )
    return rows


async def _mark_source_message_memory_processed(
    *,
    session,
    user_id: int,
    conversation_id: int | None,
    source_message_id: int | None,
) -> None:
    try:
        message_id = int(source_message_id or 0)
    except Exception:
        return
    if message_id <= 0:
        return
    row = await session.get(Message, message_id)
    if (
        row is None
        or int(row.user_id or 0) != int(user_id)
        or str(row.role or "").strip().lower() != "user"
    ):
        return
    if conversation_id is not None and int(row.conversation_id or 0) != int(conversation_id):
        return
    row.memory_status = "PROCESSED"
    row.memory_processed_at = datetime.now(ZoneInfo("UTC"))
    row.memory_error = None
    session.add(row)

    conv_id = int(row.conversation_id or 0)
    if conv_id > 0:
        conversation = await session.get(Conversation, conv_id)
        if conversation is not None and int(conversation.user_id or 0) == int(user_id):
            prev = int(conversation.memory_last_processed_message_id or 0)
            if prev < message_id:
                conversation.memory_last_processed_message_id = message_id
            conversation.memory_extracted_at = datetime.now(ZoneInfo("UTC"))
            session.add(conversation)
    await session.commit()


def _long_term_memory_to_payload(row: LongTermMemory) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "memory_key": str(row.memory_key or ""),
        "memory_type": str(row.memory_type or ""),
        "content": str(row.content or ""),
        "importance": int(row.importance or 3),
        "confidence": round(float(row.confidence or 0.0), 3),
        "updated_at": _to_client_tz_iso(getattr(row, "updated_at", None), assume_utc=True),
    }


def _try_parse_json_payload(text: str) -> Any | None:
    payload = (text or "").strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def _decode_nested_json_payload(value: Any) -> Any | None:
    payload = value
    for _ in range(3):
        if not isinstance(payload, str):
            break
        text = payload.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return payload
    return payload


def _resolve_user_id(value: Any) -> int:
    try:
        user_id = int(value or 0)
    except Exception:
        user_id = 0
    return user_id


def _normalize_datetime_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("T", " ").replace("/", "-")


def _is_date_only_text(value: Any) -> bool:
    normalized = _normalize_datetime_text(value)
    return len(normalized) == 10 and normalized.count("-") == 2 and ":" not in normalized


def _parse_local_naive_arg(value: Any) -> datetime | None:
    normalized = _normalize_datetime_text(value)
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt
    local_tz = ZoneInfo(get_settings().timezone)
    return dt.astimezone(local_tz).replace(tzinfo=None)


def _parse_utc_naive_arg(value: Any) -> datetime | None:
    normalized = _normalize_datetime_text(value)
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    local_tz = ZoneInfo(get_settings().timezone)
    return dt.replace(tzinfo=local_tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _domain_from_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        host = (urlparse(text).netloc or "").strip().lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _source_type_from_domain(domain: str) -> str:
    host = str(domain or "").strip().lower()
    if not host:
        return "unknown"
    if any(hint in host for hint in AUTHORITATIVE_DOMAIN_HINTS):
        return "authoritative"
    if any(hint in host for hint in COMMUNITY_DOMAIN_HINTS):
        return "community"
    return "general"


def _query_has_any(query: str, hints: tuple[str, ...]) -> bool:
    text = str(query or "").strip().lower()
    return any(hint.lower() in text for hint in hints)


def _query_prefers_discussion(query: str) -> bool:
    return _query_has_any(query, SUBJECTIVE_QUERY_HINTS)


def _looks_like_institution_query(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if _query_has_any(text, INSTITUTION_QUERY_HINTS):
        return True
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·]{2,10}", text))


def _parse_search_results(text: str) -> tuple[str, list[dict[str, Any]], int]:
    payload = _try_parse_json_payload(text)
    if not isinstance(payload, dict):
        return "", [], 0
    query = str(payload.get("query") or "").strip()
    total_results = 0
    try:
        total_results = int(payload.get("totalResults") or 0)
    except Exception:
        total_results = 0
    rows: list[dict[str, Any]] = []
    for item in list(payload.get("results") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        uuid = str(item.get("uuid") or "").strip()
        if not url or not title:
            continue
        domain = _domain_from_url(url)
        rows.append(
            {
                "uuid": uuid,
                "title": title,
                "url": url,
                "snippet": snippet,
                "display_url": str(item.get("displayUrl") or "").strip(),
                "domain": domain,
                "source_type": _source_type_from_domain(domain),
            }
        )
    return query, rows, total_results


def _parse_web_search_prime_results(text: str) -> list[dict[str, Any]]:
    payload = _decode_nested_json_payload(text)
    if isinstance(payload, str):
        payload = _decode_nested_json_payload(payload)
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("link") or item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not url:
            continue
        domain = _domain_from_url(url)
        rows.append(
            {
                "title": title,
                "url": url,
                "snippet": content[:400].strip(),
                "content_preview": content[:2000].strip(),
                "domain": domain,
                "source_type": _source_type_from_domain(domain),
                "refer": str(item.get("refer") or "").strip(),
                "crawl_error": "",
            }
        )
    return rows


def _parse_web_search_api_results(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    for item in list(payload.get("search_result") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("link") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title:
            continue
        domain = _domain_from_url(url)
        rows.append(
            {
                "title": title,
                "url": url,
                "snippet": content[:400].strip(),
                "content_preview": content[:2500].strip(),
                "domain": domain,
                "source_type": _source_type_from_domain(domain),
                "media": str(item.get("media") or "").strip(),
                "icon": str(item.get("icon") or "").strip(),
                "publish_date": str(item.get("publish_date") or "").strip(),
                "refer": str(item.get("refer") or "").strip(),
                "crawl_error": "",
            }
        )
    return rows, len(rows)


async def _call_web_search_api(query: str, *, max_results: int) -> tuple[list[dict[str, Any]], int]:
    settings = get_settings()
    api_key = str(settings.web_search_api_key or "").strip()
    if not api_key:
        raise RuntimeError("WEB_SEARCH_API_KEY is empty")
    url = str(settings.web_search_api_url or "").strip()
    if not url:
        raise RuntimeError("WEB_SEARCH_API_URL is empty")

    payload = {
        "search_query": query,
        "search_engine": str(settings.web_search_engine or "search_pro_sogou").strip() or "search_pro_sogou",
        "search_intent": bool(settings.web_search_intent),
        "count": max(1, min(max_results, 10)),
        "content_size": str(settings.web_search_content_size or "medium").strip() or "medium",
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=float(settings.mcp_fetch_timeout_sec), trust_env=False) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"web_search_api request failed: {exc}") from exc

    body_text = str(response.text or "").strip()
    if response.status_code >= 400:
        raise RuntimeError(f"web_search_api http {response.status_code}: {body_text[:500]}")

    data = _try_parse_json_payload(body_text)
    if not isinstance(data, dict):
        raise RuntimeError("web_search_api invalid response")
    error = data.get("error")
    if isinstance(error, dict) and error:
        raise RuntimeError(str(error.get("message") or "web_search_api error"))

    return _parse_web_search_api_results(data)


def _dedupe_search_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in rows:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip().lower()
        if not url or url in seen_urls or (title and title in seen_titles):
            continue
        seen_urls.add(url)
        if title:
            seen_titles.add(title)
        merged.append(item)
    return merged


def _extract_crawl_error(text: str) -> str:
    payload: Any | None = None
    try:
        payload = json.loads(text or "")
    except Exception:
        payload = None
    if isinstance(payload, list):
        first = payload[0] if payload else None
        if isinstance(first, dict):
            error = str(first.get("error") or "").strip()
            if error:
                return error
    if isinstance(payload, dict):
        error = str(payload.get("error") or "").strip()
        if error:
            return error
    lowered = str(text or "").lower()
    if "禁止抓取" in str(text or "") or "blacklist" in lowered:
        return str(text or "").strip()[:500]
    return ""


def _should_retry_with_official_terms(query: str, rows: list[dict[str, Any]]) -> bool:
    text = str(query or "").strip()
    if not text or _query_prefers_discussion(text):
        return False
    if "官网" in text or "官方" in text:
        return False
    if not _looks_like_institution_query(text):
        return False
    top = rows[:5]
    if not top:
        return False
    return all(str(item.get("source_type") or "") == "community" for item in top)


def _pick_results_for_crawl(query: str, rows: list[dict[str, Any]], *, limit: int = 2) -> list[dict[str, Any]]:
    preferred: list[dict[str, Any]] = []
    backups: list[dict[str, Any]] = []
    wants_detail = _query_has_any(query, DETAIL_QUERY_HINTS)
    for item in rows[:5]:
        source_type = str(item.get("source_type") or "")
        if source_type != "community":
            preferred.append(item)
        elif wants_detail:
            backups.append(item)
    picked = preferred[:limit]
    if len(picked) < limit:
        for item in backups:
            if item not in picked:
                picked.append(item)
            if len(picked) >= limit:
                break
    return picked[:limit]


async def _execute_web_search(query: str, *, focus: str, max_results: int) -> dict[str, Any]:
    original_query = str(query or "").strip()
    focus_text = str(focus or "").strip()
    search_query = original_query
    if focus_text:
        search_query = f"{original_query} {focus_text}".strip()

    settings = get_settings()
    executed_queries: list[str] = []

    def _build_payload(
        *,
        rows: list[dict[str, Any]],
        total_results: int,
        failure_reason: str = "",
    ) -> dict[str, Any]:
        source_rows = rows[: max(1, min(max_results, 5))]
        for item in source_rows:
            item.setdefault("content_preview", "")
            item.setdefault("crawl_error", "")

        if not source_rows:
            return {
                "query": original_query,
                "executed_queries": executed_queries,
                "status": "no_results",
                "answer_ready": False,
                "summary": "未检索到可用结果。",
                "failure_reason": failure_reason or "no_results",
                "sources": [],
                "total_results": total_results,
            }

        source_types = {str(item.get("source_type") or "") for item in source_rows}
        all_community = bool(source_rows) and source_types.issubset({"community"})
        status = "ok"
        answer_ready = True
        summary = f"已检索到 {len(rows)} 条候选结果。"
        if all_community and not _query_prefers_discussion(original_query):
            status = "results_low_authority"
            answer_ready = False
            summary = "已检索到结果，但当前结果主要来自社区讨论或问答站点，缺少更权威的来源。"
            failure_reason = failure_reason or "results_low_authority"
        elif all(
            not str(item.get("snippet") or "").strip() and not str(item.get("content_preview") or "").strip()
            for item in source_rows
        ):
            status = "results_insufficient"
            answer_ready = False
            summary = "已检索到结果，但结果摘要和正文信息都不足以支撑稳定回答。"
            failure_reason = failure_reason or "results_insufficient"

        normalized_sources: list[dict[str, Any]] = []
        for item in source_rows:
            normalized_sources.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "domain": str(item.get("domain") or "").strip(),
                    "source_type": str(item.get("source_type") or "").strip(),
                    "snippet": str(item.get("snippet") or "").strip(),
                    "content_preview": str(item.get("content_preview") or "").strip(),
                    "crawl_error": str(item.get("crawl_error") or "").strip(),
                }
            )

        return {
            "query": original_query,
            "executed_queries": executed_queries,
            "status": status,
            "answer_ready": answer_ready,
            "summary": summary,
            "failure_reason": failure_reason,
            "sources": normalized_sources,
            "total_results": total_results,
        }

    primary_error = ""
    if str(settings.web_search_api_url or "").strip() and str(settings.web_search_api_key or "").strip():
        executed_queries.append(search_query)
        try:
            rows, total_results = await _call_web_search_api(search_query, max_results=max_results)
            rows = _dedupe_search_results(rows)
            if rows:
                primary_payload = _build_payload(rows=rows, total_results=total_results)
                if str(primary_payload.get("status") or "") == "ok":
                    return primary_payload
                primary_error = str(primary_payload.get("failure_reason") or primary_payload.get("status") or "")
            else:
                primary_error = "primary_search_empty"
        except Exception as exc:
            primary_error = str(exc)

    search_client = get_mcp_search_fallback_client()
    crawl_client = get_mcp_search_fallback_client()

    async def _search_once(text: str) -> tuple[list[dict[str, Any]], int]:
        executed_queries.append(text)
        raw = await search_client.call_tool(
            name="bing_search",
            arguments={"query": text, "count": max(3, min(max_results, 8)), "offset": 0},
        )
        _, parsed_rows, total_results = _parse_search_results(raw)
        return parsed_rows, total_results

    try:
        rows, total_results = await _search_once(search_query)
    except Exception as exc:
        reason = primary_error or str(exc)
        return {
            "query": original_query,
            "executed_queries": executed_queries or [search_query],
            "status": "network_error",
            "answer_ready": False,
            "summary": "联网搜索失败，暂时无法获得外部结果。",
            "failure_reason": reason,
            "sources": [],
            "total_results": 0,
        }

    rows = _dedupe_search_results(rows)
    if _should_retry_with_official_terms(original_query, rows):
        try:
            retry_rows, retry_total = await _search_once(f"{original_query} 官网 官方")
            rows = _dedupe_search_results(rows + retry_rows)
            total_results = max(total_results, retry_total)
        except Exception:
            pass

    crawled_sources: list[dict[str, Any]] = []
    for item in _pick_results_for_crawl(original_query, rows, limit=2):
        uuid = str(item.get("uuid") or "").strip()
        url = str(item.get("url") or "").strip()
        if not uuid or not url:
            continue
        try:
            content = await crawl_client.call_tool(
                name="crawl_webpage",
                arguments={"uuids": [uuid], "urlMap": {uuid: url}},
            )
            preview = str(content or "").strip()
            if preview:
                item = dict(item)
                crawl_error = _extract_crawl_error(preview)
                if crawl_error:
                    item["crawl_error"] = crawl_error[:500]
                    item["content_preview"] = ""
                else:
                    item["content_preview"] = preview[:2000]
                crawled_sources.append(item)
        except Exception as exc:
            item = dict(item)
            item["crawl_error"] = str(exc)
            crawled_sources.append(item)

    source_rows = crawled_sources if crawled_sources else rows
    return _build_payload(rows=source_rows, total_results=total_results)


def _ledger_to_payload(row: Ledger) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "user_id": int(row.user_id),
        "amount": float(row.amount),
        "currency": str(row.currency or "CNY"),
        "category": str(row.category or ""),
        "item": str(row.item or ""),
        "image_url": str(row.image_url or ""),
        "transaction_date": _to_client_tz_iso(row.transaction_date, assume_utc=True),
    }


def _schedule_to_payload(row: Schedule) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "user_id": int(row.user_id),
        "job_id": str(row.job_id or ""),
        "content": str(row.content or ""),
        "status": str(row.status or ""),
        "trigger_time": _to_client_tz_iso(row.trigger_time, assume_utc=False),
    }


SCHEDULE_STATUS_ALIASES: dict[str, str | None] = {
    "": None,
    "all": None,
    "全部": None,
    "所有": None,
    "pending": "PENDING",
    "todo": "PENDING",
    "未完成": "PENDING",
    "待办": "PENDING",
    "待执行": "PENDING",
    "未执行": "PENDING",
    "executed": "EXECUTED",
    "completed": "EXECUTED",
    "done": "EXECUTED",
    "已完成": "EXECUTED",
    "完成": "EXECUTED",
    "已执行": "EXECUTED",
    "已提醒": "EXECUTED",
    "提醒过": "EXECUTED",
    "已触发": "EXECUTED",
    "已发送": "EXECUTED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "已取消": "CANCELLED",
    "取消": "CANCELLED",
    "failed": "FAILED",
    "失败": "FAILED",
    "未送达": "FAILED",
}


def _normalize_schedule_status_arg(value: Any) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    normalized = SCHEDULE_STATUS_ALIASES.get(key.lower())
    if normalized is not None or key.lower() in SCHEDULE_STATUS_ALIASES:
        return normalized
    normalized = SCHEDULE_STATUS_ALIASES.get(key)
    if normalized is not None or key in SCHEDULE_STATUS_ALIASES:
        return normalized
    upper = key.upper()
    if upper == "ALL":
        return None
    return upper


def _conversation_to_payload(row: Any, active_id: int | None) -> dict[str, Any]:
    row_id = int(getattr(row, "id", 0) or 0)
    return {
        "id": row_id,
        "title": str(getattr(row, "title", "") or ""),
        "summary": str(getattr(row, "summary", "") or ""),
        "last_message_at": _to_client_tz_iso(getattr(row, "last_message_at", None), assume_utc=True),
        "active": bool(active_id and row_id == int(active_id)),
    }


async def _log_tool_usage_safe(
    *,
    user_id: int | None,
    platform: str,
    conversation_id: int | None,
    tool_source: str,
    tool_name: str,
    success: bool,
    latency_ms: int,
    error: str = "",
) -> None:
    try:
        enqueue_tool_usage(
            user_id=user_id,
            platform=platform,
            conversation_id=conversation_id,
            tool_source=tool_source,
            tool_name=tool_name,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        return


async def execute_capability(
    *,
    source: str,
    name: str,
    args: dict[str, Any] | None = None,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> ToolExecResult:
    started = time.perf_counter()
    src = str(source or "").strip().lower()
    raw_tool = str(name or "").strip()
    tool_l = raw_tool.lower()
    tool_l = BUILTIN_TOOL_ALIAS.get(tool_l, tool_l)
    tool = tool_l
    params = dict(args or {})
    settings = get_settings()

    def _result(
        ok: bool,
        output: str = "",
        output_data: Any | None = None,
        error: str = "",
    ) -> ToolExecResult:
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed_output = output_data
        if ok and parsed_output is None:
            parsed_output = _try_parse_json_payload(output)
        return {
            "ok": ok,
            "source": src,
            "name": tool,
            "output": output if ok else "",
            "output_data": parsed_output if ok else None,
            "error": error if not ok else "",
            "latency_ms": latency_ms,
        }

    if src not in {"builtin", "mcp"}:
        return _result(False, error=f"unsupported tool source: {src}")

    if not tool_l:
        return _result(False, error="missing tool name")

    try:
        if src == "builtin":
            if not await is_tool_enabled("builtin", tool_l):
                return _result(False, error=f"tool `{tool_l}` is disabled by admin.")

            if tool_l == "now_time":
                timezone = str(params.get("timezone") or settings.timezone or "Asia/Shanghai").strip()
                return _result(True, output=_render_now_time(timezone))

            if tool_l == "web_search":
                query = str(params.get("query") or "").strip()
                focus = str(params.get("focus") or "").strip()
                max_results = max(1, min(8, int(params.get("max_results") or 5)))
                if not query:
                    return _result(False, error="missing required arg: query")
                payload = await _execute_web_search(query, focus=focus, max_results=max_results)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "fetch_url":
                return _result(
                    False,
                    error="tool `fetch_url` is disabled. Use MCP search tools such as `bing_search` and `crawl_webpage` instead.",
                )

            if tool_l == "tool_list":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                runtime_tools = await list_runtime_tool_metas()
                mcp_tools = [dict(item) for item in runtime_tools if str(item.get("source") or "") == "mcp"]
                user_mcp_tools = await _list_user_mcp_tool_rows(user_id)
                if user_mcp_tools:
                    seen_names = {str(item.get("name") or "").strip() for item in mcp_tools}
                    for item in user_mcp_tools:
                        name = str(item.get("name") or "").strip()
                        if not name or name in seen_names:
                            continue
                        seen_names.add(name)
                        mcp_tools.append(item)
                return _result(True, output=_render_mcp_tool_rows(mcp_tools, user_id=user_id))

            if tool_l == "tool_call":
                if not settings.mcp_fetch_enabled:
                    return _result(False, error="MCP fetch is disabled.")
                target_name = str(params.get("tool_name") or params.get("name") or "").strip()
                if not target_name:
                    return _result(False, error="missing required arg: tool_name")
                if not is_mcp_tool_allowed(target_name):
                    allowed = sorted(get_allowed_mcp_tool_names_for(target_name))
                    allowed_text = ", ".join(allowed) if allowed else "none"
                    return _result(False, error=f"MCP tool `{target_name}` is blocked by allowlist. Allowed tools: {allowed_text}.")
                if not await is_tool_enabled("mcp", target_name):
                    return _result(False, error=f"MCP tool `{target_name}` is disabled by admin.")
                target_args = params.get("arguments")
                if not isinstance(target_args, dict):
                    target_args = {}
                output = await get_mcp_client_for_tool(target_name).call_tool(name=target_name, arguments=target_args)
                return _result(True, output=output)

            if tool_l == "analyze_receipt":
                image_url = str(params.get("image_url") or params.get("image_ref") or "").strip()
                if not image_url:
                    return _result(False, error="missing required arg: image_url")
                output = await analyze_receipt(image_url)
                payload = output if isinstance(output, dict) else {"result": str(output)}
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "analyze_image":
                image_url = str(params.get("image_url") or params.get("image_ref") or "").strip()
                if not image_url:
                    return _result(False, error="missing required arg: image_url")
                question = str(params.get("question") or "").strip()
                output = await analyze_image(image_url, question=question)
                payload = output if isinstance(output, dict) else {"result": str(output)}
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_text2sql":
                uid_raw = params.get("user_id", user_id)
                try:
                    uid = int(uid_raw or 0)
                except Exception:
                    uid = 0
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                message = str(params.get("message") or "").strip()
                if not message:
                    return _result(False, error="missing required arg: message")
                conversation_context = str(params.get("conversation_context") or "").strip()
                mode = str(params.get("mode") or "execute").strip().lower()
                if mode == "preview_write":
                    operation = str(params.get("operation") or "").strip().lower()
                    preview_limit = int(params.get("preview_limit") or 50)
                    preview_hints = params.get("preview_hints")
                    if not isinstance(preview_hints, dict):
                        preview_hints = {}
                    update_fields = params.get("update_fields")
                    if not isinstance(update_fields, dict):
                        update_fields = {}
                    output_data = await plan_write_preview_text2sql(
                        user_id=uid,
                        message=message,
                        operation=operation,
                        conversation_context=conversation_context,
                        preview_limit=preview_limit,
                        preview_hints=preview_hints,
                        update_fields=update_fields,
                    )
                    return _result(
                        True,
                        output=json.dumps(output_data or {}, ensure_ascii=False),
                        output_data=output_data or {},
                    )
                if mode == "commit_write_by_ids":
                    operation = str(params.get("operation") or "").strip().lower()
                    raw_ids = params.get("target_ids")
                    target_ids = raw_ids if isinstance(raw_ids, list) else []
                    expected_count = int(params.get("expected_count") or 0)
                    update_fields = params.get("update_fields")
                    if not isinstance(update_fields, dict):
                        update_fields = {}
                    output_data = await commit_write_by_ids_text2sql(
                        user_id=uid,
                        operation=operation,
                        target_ids=target_ids,
                        expected_count=expected_count,
                        update_fields=update_fields,
                    )
                    return _result(
                        True,
                        output=json.dumps(output_data or {}, ensure_ascii=False),
                        output_data=output_data or {},
                    )

                output = await try_execute_ledger_text2sql(
                    user_id=uid,
                    message=message,
                    conversation_context=conversation_context,
                )
                return _result(True, output=str(output or ""))

            if tool_l == "ledger_insert":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                amount_raw = params.get("amount")
                try:
                    amount = float(amount_raw)
                except Exception:
                    amount = 0.0
                if amount <= 0:
                    return _result(False, error="invalid amount")
                category = str(params.get("category") or "其他").strip() or "其他"
                item = str(params.get("item") or "消费").strip() or "消费"
                transaction_date = _parse_utc_naive_arg(params.get("transaction_date")) or datetime.utcnow()
                image_url = str(params.get("image_url") or "").strip() or None
                session = get_session()
                row = await insert_ledger(
                    session=session,
                    user_id=uid,
                    amount=amount,
                    category=category,
                    item=item,
                    transaction_date=transaction_date,
                    image_url=image_url,
                    platform=platform,
                )
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_update":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                ledger_id = _resolve_user_id(params.get("ledger_id"))
                if ledger_id <= 0:
                    return _result(False, error="missing required arg: ledger_id")
                amount_value = params.get("amount")
                amount = None
                if amount_value is not None and str(amount_value).strip() != "":
                    try:
                        amount = float(amount_value)
                    except Exception:
                        return _result(False, error="invalid amount")
                category = str(params.get("category") or "").strip() or None
                item = str(params.get("item") or "").strip() or None
                transaction_date = _parse_utc_naive_arg(params.get("transaction_date"))
                session = get_session()
                row = await update_ledger(
                    session=session,
                    user_id=uid,
                    ledger_id=ledger_id,
                    amount=amount,
                    category=category,
                    item=item,
                    transaction_date=transaction_date,
                    platform=platform,
                )
                if row is None:
                    return _result(False, error="ledger not found")
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_delete":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                ledger_id = _resolve_user_id(params.get("ledger_id"))
                if ledger_id <= 0:
                    return _result(False, error="missing required arg: ledger_id")
                session = get_session()
                row = await delete_ledger(
                    session=session,
                    user_id=uid,
                    ledger_id=ledger_id,
                    platform=platform,
                )
                if row is None:
                    return _result(False, error="ledger not found")
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_get_latest":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                session = get_session()
                row = await get_latest_ledger(session=session, user_id=uid)
                if row is None:
                    return _result(True, output=json.dumps({}, ensure_ascii=False), output_data={})
                payload = _ledger_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_list_recent":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(200, int(params.get("limit") or 10)))
                session = get_session()
                rows = await list_recent_ledgers(session=session, user_id=uid, limit=limit)
                payload = [_ledger_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "ledger_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(500, int(params.get("limit") or 100)))
                stmt = select(Ledger).where(Ledger.user_id == uid)

                raw_ids = params.get("ledger_ids")
                if isinstance(raw_ids, list):
                    picked_ids: list[int] = []
                    for item in raw_ids:
                        try:
                            num = int(item)
                        except Exception:
                            continue
                        if num > 0 and num not in picked_ids:
                            picked_ids.append(num)
                    if picked_ids:
                        stmt = stmt.where(Ledger.id.in_(picked_ids))

                start_at_raw = params.get("start_at")
                end_at_raw = params.get("end_at")
                start_at = _parse_utc_naive_arg(start_at_raw)
                end_at = _parse_utc_naive_arg(end_at_raw)
                if end_at is not None and _is_date_only_text(end_at_raw):
                    end_at += timedelta(days=1)
                if start_at is not None:
                    stmt = stmt.where(Ledger.transaction_date >= start_at)
                if end_at is not None:
                    stmt = stmt.where(Ledger.transaction_date < end_at)

                category = str(params.get("category") or "").strip()
                if category:
                    stmt = stmt.where(Ledger.category == category)

                item_like = str(params.get("item_like") or "").strip()
                if item_like:
                    stmt = stmt.where(Ledger.item.ilike(f"%{item_like}%"))

                order = str(params.get("order") or "desc").strip().lower()
                if order == "asc":
                    stmt = stmt.order_by(Ledger.transaction_date.asc(), Ledger.id.asc())
                else:
                    stmt = stmt.order_by(Ledger.transaction_date.desc(), Ledger.id.desc())
                stmt = stmt.limit(limit)

                session = get_session()
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
                payload = [_ledger_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "conversation_current":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                session = get_session()
                user_row = await session.get(User, uid)
                if user_row is None:
                    return _result(False, error="user not found")
                current = await ensure_active_conversation(session, user_row)
                payload = _conversation_to_payload(current, int(current.id or 0))
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "conversation_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(100, int(params.get("limit") or 20)))
                session = get_session()
                user_row = await session.get(User, uid)
                if user_row is None:
                    return _result(False, error="user not found")
                await ensure_active_conversation(session, user_row)
                rows = await list_conversations(session, user_row, limit=limit)
                active_id = int(user_row.active_conversation_id or 0) or None
                payload = [_conversation_to_payload(item, active_id) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "memory_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(500, int(params.get("limit") or 120)))
                session = get_session()
                payload = await list_long_term_memories(
                    session=session,
                    user_id=uid,
                    limit=limit,
                )
                return _result(
                    True,
                    output=json.dumps(payload or [], ensure_ascii=False),
                    output_data=payload or [],
                )

            if tool_l == "memory_save":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                content = str(params.get("content") or "").strip()
                if not content:
                    return _result(False, error="missing required arg: content")
                memory_type = str(params.get("memory_type") or "fact").strip().lower() or "fact"
                memory_key = str(params.get("key") or "").strip()
                try:
                    importance = int(params.get("importance") or 3)
                except Exception:
                    importance = 3
                importance = max(1, min(5, importance))
                try:
                    confidence = float(params.get("confidence") or 1.0)
                except Exception:
                    confidence = 1.0
                confidence = max(0.0, min(1.0, confidence))
                try:
                    ttl_days = int(params.get("ttl_days") or settings.long_term_memory_default_ttl_days)
                except Exception:
                    ttl_days = int(settings.long_term_memory_default_ttl_days)
                ttl_days = max(1, ttl_days)

                session = get_session()
                source_message_id = _resolve_user_id(params.get("source_message_id") or get_tool_message_id())
                processed = await upsert_long_term_memories(
                    session=session,
                    user_id=uid,
                    conversation_id=conversation_id,
                    source_message_id=(source_message_id or None),
                    candidates=[
                        {
                            "op": "save",
                            "memory_type": memory_type,
                            "key": memory_key,
                            "content": content,
                            "importance": importance,
                            "confidence": confidence,
                            "ttl_days": ttl_days,
                        }
                    ],
                    user_text=f"用户明确要求记住：{content}",
                    bypass_refine=True,
                )
                if processed <= 0:
                    return _result(False, error="memory not saved")
                await _mark_source_message_memory_processed(
                    session=session,
                    user_id=uid,
                    conversation_id=conversation_id,
                    source_message_id=(source_message_id or None),
                )
                payload = {
                    "status": "saved",
                    "content": content,
                    "memory_type": memory_type,
                    "importance": importance,
                    "confidence": confidence,
                    "ttl_days": ttl_days,
                    "source_message_id": source_message_id or None,
                    "conversation_id": conversation_id,
                }
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "memory_append":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                append_text = str(params.get("content") or "").strip()
                if not append_text:
                    return _result(False, error="missing required arg: content")
                memory_id = _resolve_user_id(params.get("memory_id")) or None
                memory_key = str(params.get("memory_key") or "").strip()
                target_hint = str(params.get("target_hint") or "").strip()
                memory_type = str(params.get("memory_type") or "").strip().lower()
                separator = str(params.get("separator") or "；").strip() or "；"
                session = get_session()
                target = await find_active_long_term_memory(
                    session=session,
                    user_id=uid,
                    memory_id=memory_id,
                    memory_key=memory_key,
                    content_hint=target_hint,
                    memory_type=memory_type,
                )
                if target is None:
                    return _result(False, error="target memory not found")

                current_content = str(target.content or "").strip()
                if append_text in current_content:
                    source_message_id = _resolve_user_id(params.get("source_message_id") or get_tool_message_id()) or None
                    await _mark_source_message_memory_processed(
                        session=session,
                        user_id=uid,
                        conversation_id=conversation_id,
                        source_message_id=source_message_id,
                    )
                    payload = {
                        "status": "unchanged",
                        "memory": _long_term_memory_to_payload(target),
                    }
                    return _result(
                        True,
                        output=json.dumps(payload, ensure_ascii=False),
                        output_data=payload,
                    )

                target.content = f"{current_content}{separator}{append_text}" if current_content else append_text
                importance_raw = params.get("importance")
                if importance_raw is not None and str(importance_raw).strip() != "":
                    try:
                        target.importance = max(1, min(5, int(importance_raw)))
                    except Exception:
                        pass
                confidence_raw = params.get("confidence")
                if confidence_raw is not None and str(confidence_raw).strip() != "":
                    try:
                        target.confidence = max(0.0, min(1.0, float(confidence_raw)))
                    except Exception:
                        pass
                ttl_days_raw = params.get("ttl_days")
                if ttl_days_raw is not None and str(ttl_days_raw).strip() != "":
                    try:
                        ttl_days = max(1, int(ttl_days_raw))
                        target.expires_at = datetime.now(ZoneInfo("UTC")) + timedelta(days=ttl_days)
                    except Exception:
                        pass
                target.conversation_id = conversation_id
                target.source_message_id = _resolve_user_id(params.get("source_message_id") or get_tool_message_id()) or None
                target.updated_at = datetime.now(ZoneInfo("UTC"))
                mark_long_term_memory_vector_dirty(target)
                session.add(target)
                await session.commit()
                await session.refresh(target)
                await _mark_source_message_memory_processed(
                    session=session,
                    user_id=uid,
                    conversation_id=conversation_id,
                    source_message_id=target.source_message_id,
                )
                payload = {
                    "status": "appended",
                    "memory": _long_term_memory_to_payload(target),
                }
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "memory_delete":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                memory_id = _resolve_user_id(params.get("memory_id")) or None
                memory_key = str(params.get("memory_key") or "").strip()
                target_hint = str(params.get("target_hint") or "").strip()
                memory_type = str(params.get("memory_type") or "").strip().lower()
                session = get_session()
                target = await find_active_long_term_memory(
                    session=session,
                    user_id=uid,
                    memory_id=memory_id,
                    memory_key=memory_key,
                    content_hint=target_hint,
                    memory_type=memory_type,
                )
                if target is None:
                    return _result(False, error="target memory not found")
                payload = _long_term_memory_to_payload(target)
                source_message_id = _resolve_user_id(params.get("source_message_id") or get_tool_message_id()) or None
                deleted_memory_id = int(target.id or 0) or None
                await session.delete(target)
                await session.commit()
                if deleted_memory_id:
                    try:
                        await delete_memory_vectors([deleted_memory_id])
                    except Exception:
                        pass
                await _mark_source_message_memory_processed(
                    session=session,
                    user_id=uid,
                    conversation_id=conversation_id,
                    source_message_id=source_message_id,
                )
                return _result(
                    True,
                    output=json.dumps({"status": "deleted", "memory": payload}, ensure_ascii=False),
                    output_data={"status": "deleted", "memory": payload},
                )

            if tool_l == "schedule_insert":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                content = str(params.get("content") or "").strip()
                if not content:
                    return _result(False, error="missing required arg: content")
                raw_trigger = str(params.get("trigger_time") or "").strip()
                trigger_time = _parse_local_naive_arg(raw_trigger)
                if trigger_time is None:
                    return _result(
                        False,
                        error=f"无法解析 trigger_time='{raw_trigger}'。"
                              "请使用绝对时间格式（如 '2025-03-20 15:30:00'）"
                              "或相对时间（如 '10秒后'、'5分钟后'、'明天下午3点'、'下周一上午10点'）重试。",
                    )
                status = str(params.get("status") or "PENDING").strip().upper() or "PENDING"
                job_id = str(params.get("job_id") or "").strip() or str(uuid4())
                session = get_session()
                scheduler = get_scheduler()
                row = Schedule(
                    user_id=uid,
                    job_id=job_id,
                    content=content,
                    trigger_time=trigger_time,
                    status=status,
                )
                session.add(row)
                await session.flush()
                if status == "PENDING":
                    scheduler.add_job(job_id, trigger_time, send_reminder_job, int(row.id or 0))
                await session.commit()
                await session.refresh(row)
                payload = _schedule_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_update":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                schedule_id = _resolve_user_id(params.get("schedule_id"))
                if schedule_id <= 0:
                    return _result(False, error="missing required arg: schedule_id")
                session = get_session()
                scheduler = get_scheduler()
                row = await session.get(Schedule, schedule_id)
                if row is None or int(row.user_id or 0) != uid:
                    return _result(False, error="schedule not found")
                content_value = str(params.get("content") or "").strip()
                if content_value:
                    row.content = content_value
                raw_trigger_upd = str(params.get("trigger_time") or "").strip()
                if raw_trigger_upd:
                    trigger_time = _parse_local_naive_arg(raw_trigger_upd)
                    if trigger_time is None:
                        return _result(
                            False,
                            error=f"无法解析 trigger_time='{raw_trigger_upd}'。"
                                  "请使用绝对时间格式（如 '2025-03-20 15:30:00'）"
                                  "或相对时间（如 '10秒后'、'5分钟后'、'明天下午3点'）重试。",
                        )
                    row.trigger_time = trigger_time
                status_value = _normalize_schedule_status_arg(params.get("status")) or ""
                if status_value:
                    row.status = status_value
                try:
                    scheduler.remove_job(str(row.job_id))
                except Exception:
                    pass
                if str(row.status or "").upper() == "PENDING":
                    scheduler.add_job(str(row.job_id), row.trigger_time, send_reminder_job, int(row.id or 0))
                session.add(row)
                await session.commit()
                await session.refresh(row)
                payload = _schedule_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_delete":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                schedule_id = _resolve_user_id(params.get("schedule_id"))
                if schedule_id <= 0:
                    return _result(False, error="missing required arg: schedule_id")
                session = get_session()
                scheduler = get_scheduler()
                row = await session.get(Schedule, schedule_id)
                if row is None or int(row.user_id or 0) != uid:
                    return _result(False, error="schedule not found")
                payload = _schedule_to_payload(row)
                try:
                    scheduler.remove_job(str(row.job_id))
                except Exception:
                    pass
                await session.execute(delete(ReminderDelivery).where(ReminderDelivery.schedule_id == schedule_id))
                await session.delete(row)
                await session.commit()
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_get_latest":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                session = get_session()
                stmt = (
                    select(Schedule)
                    .where(Schedule.user_id == uid)
                    .order_by(Schedule.id.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                row = result.scalars().first()
                if row is None:
                    return _result(True, output="{}", output_data={})
                payload = _schedule_to_payload(row)
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_list_recent":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(100, int(params.get("limit") or 10)))
                session = get_session()
                stmt = (
                    select(Schedule)
                    .where(Schedule.user_id == uid)
                    .order_by(Schedule.id.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
                payload = [_schedule_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            if tool_l == "schedule_list":
                uid = _resolve_user_id(params.get("user_id", user_id))
                if uid <= 0:
                    return _result(False, error="missing required arg: user_id")
                limit = max(1, min(500, int(params.get("limit") or 100)))
                stmt = select(Schedule).where(Schedule.user_id == uid)

                raw_ids = params.get("schedule_ids")
                if isinstance(raw_ids, list):
                    picked_ids: list[int] = []
                    for item in raw_ids:
                        try:
                            num = int(item)
                        except Exception:
                            continue
                        if num > 0 and num not in picked_ids:
                            picked_ids.append(num)
                    if picked_ids:
                        stmt = stmt.where(Schedule.id.in_(picked_ids))

                start_at_raw = params.get("start_at")
                end_at_raw = params.get("end_at")
                start_at = _parse_local_naive_arg(start_at_raw)
                end_at = _parse_local_naive_arg(end_at_raw)
                if end_at is not None and _is_date_only_text(end_at_raw):
                    end_at += timedelta(days=1)
                if start_at is not None:
                    stmt = stmt.where(Schedule.trigger_time >= start_at)
                if end_at is not None:
                    stmt = stmt.where(Schedule.trigger_time < end_at)

                content_like = str(params.get("content_like") or "").strip()
                if content_like:
                    stmt = stmt.where(Schedule.content.ilike(f"%{content_like}%"))

                status = _normalize_schedule_status_arg(params.get("status"))
                if status:
                    stmt = stmt.where(Schedule.status == status)
                order = str(params.get("order") or "asc").strip().lower()
                if order == "desc":
                    stmt = stmt.order_by(Schedule.trigger_time.desc(), Schedule.id.desc())
                else:
                    stmt = stmt.order_by(Schedule.trigger_time.asc(), Schedule.id.asc())
                stmt = stmt.limit(limit)
                session = get_session()
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
                payload = [_schedule_to_payload(item) for item in rows]
                return _result(
                    True,
                    output=json.dumps(payload, ensure_ascii=False),
                    output_data=payload,
                )

            return _result(False, error=f"unsupported builtin tool: {tool_l}")

        # src == "mcp"
        target_name = tool.strip()
        target_norm = target_name.lower()
        if not settings.mcp_fetch_enabled:
            return _result(False, error="MCP fetch is disabled.")
        if not is_mcp_tool_allowed(target_norm):
            allowed = sorted(get_allowed_mcp_tool_names_for(target_norm))
            allowed_text = ", ".join(allowed) if allowed else "none"
            return _result(False, error=f"MCP tool `{target_name}` is blocked by allowlist. Allowed tools: {allowed_text}.")
        if not await is_tool_enabled("mcp", target_norm):
            return _result(False, error=f"MCP tool `{target_name}` is disabled by admin.")
        output = await get_mcp_client_for_tool(target_name).call_tool(name=target_name, arguments=params)
        return _result(True, output=output)

    except Exception as exc:
        return _result(False, error=str(exc))


async def execute_capability_with_usage(
    *,
    source: str,
    name: str,
    args: dict[str, Any] | None = None,
    user_id: int | None = None,
    platform: str = "",
    conversation_id: int | None = None,
) -> ToolExecResult:
    result = await execute_capability(
        source=source,
        name=name,
        args=args,
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
    )
    await _log_tool_usage_safe(
        user_id=user_id,
        platform=platform,
        conversation_id=conversation_id,
        tool_source=str(result["source"] or source),
        tool_name=str(result["name"] or name),
        success=bool(result["ok"]),
        latency_ms=int(result["latency_ms"] or 0),
        error=str(result.get("error") or ""),
    )
    return result
