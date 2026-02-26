from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.llm import get_llm


_FORBIDDEN_SQL = re.compile(
    r"\b(drop|alter|truncate|create|grant|revoke|vacuum|analyze|copy|attach|detach)\b",
    re.IGNORECASE,
)
_OTHER_TABLES = re.compile(
    r"\b(users|messages|skills|schedules|conversations|audits)\b",
    re.IGNORECASE,
)
_USER_FILTER = re.compile(r"(?:\w+\.)?user_id\s*=\s*:user_id", re.IGNORECASE)
_DELETE_ALL_HINT = re.compile(r"(所有|全部|清空|all)", re.IGNORECASE)


class LedgerText2SQLPlan(BaseModel):
    matched: bool = Field(default=False)
    intent: str = Field(default="unknown")
    sql: str = Field(default="")
    params: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default="")
    confidence: float = Field(default=0.0)


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.endswith("Z"):
                candidate = candidate[:-1] + "+00:00"
            if (
                key.endswith("_at")
                or "date" in key.lower()
                or "time" in key.lower()
            ):
                try:
                    dt = datetime.fromisoformat(candidate)
                    if dt.tzinfo is not None:
                        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    normalized[key] = dt
                    continue
                except Exception:
                    pass
        if isinstance(value, datetime) and value.tzinfo is not None:
            normalized[key] = value.astimezone(timezone.utc).replace(tzinfo=None)
            continue
        normalized[key] = value
    return normalized


def _strip_user_filter(where_clause: str) -> str:
    compact = re.sub(r"\s+", " ", where_clause).strip()
    compact = _USER_FILTER.sub("", compact)
    compact = re.sub(r"\(\s*\)", "", compact)
    compact = re.sub(r"\b(and|or)\b", " ", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact


def _is_safe_sql(sql: str, intent: str, user_message: str) -> tuple[bool, str]:
    stmt = (sql or "").strip()
    if stmt.endswith(";") and stmt.count(";") == 1:
        stmt = stmt[:-1].strip()
    lower = stmt.lower()
    if not stmt:
        return False, "empty_sql"
    if ";" in stmt:
        return False, "multi_statement_not_allowed"
    if _FORBIDDEN_SQL.search(stmt):
        return False, "forbidden_keyword"
    if _OTHER_TABLES.search(stmt):
        return False, "non_ledger_table_detected"
    if " ledgers" not in f" {lower} ":
        return False, "must_target_ledgers"

    if intent == "select":
        if not lower.startswith("select "):
            return False, "intent_mismatch"
        if " where " not in lower:
            return False, "missing_where"
        if not _USER_FILTER.search(stmt):
            return False, "missing_user_filter"
        return True, ""

    if intent == "insert":
        if not lower.startswith("insert into ledgers"):
            return False, "intent_mismatch"
        if "user_id" not in lower:
            return False, "missing_user_id_column"
        return True, ""

    if intent in {"update", "delete"}:
        expected = "update ledgers" if intent == "update" else "delete from ledgers"
        if not lower.startswith(expected):
            return False, "intent_mismatch"
        if " where " not in lower:
            return False, "missing_where"
        if not _USER_FILTER.search(stmt):
            return False, "missing_user_filter"
        where_part = re.split(r"\bwhere\b", stmt, flags=re.IGNORECASE, maxsplit=1)[-1]
        remains = _strip_user_filter(where_part)
        if not remains and not _DELETE_ALL_HINT.search(user_message or ""):
            return False, "missing_additional_filter"
        return True, ""

    return False, "unsupported_intent"


async def _plan_sql(message: str, conversation_context: str = "") -> dict[str, Any]:
    llm = get_llm(node_name="ledger_text2sql")
    runnable = llm.with_structured_output(LedgerText2SQLPlan)
    now = datetime.utcnow().isoformat()
    system_prompt = (
        "你是账单 Text-to-SQL 规划器（PostgreSQL）。"
        "只允许操作 ledgers 表，不允许出现任何其他表。"
        "请仅返回结构化字段: matched, intent, sql, params, summary, confidence。"
        "intent 仅可为 select/insert/update/delete/unknown。"
        "如果不是账单请求，matched=false，intent=unknown。"
        "必须使用命名参数占位符（如 :user_id）。"
        "select/update/delete 必须带 WHERE user_id = :user_id。"
        "insert 必须显式写入 user_id。"
        "若用户说“今天/本周/本月”，请将时间转成 start_at/end_at 参数（ISO 格式）。"
        "对“删除今天所有订单”这类请求，输出 delete SQL，条件必须为 user_id + 时间范围。"
        "当前时间(UTC): "
        f"{now}。所有 today/week/month 必须基于这个时间计算。"
        "插入账单时只可使用列: user_id, amount, currency, category, item, transaction_date, image_url。"
        "不得使用不存在的列（例如 description）。"
        "禁止输出 Markdown、解释文本、代码块。"
    )
    result = await runnable.ainvoke(
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"会话上下文:\n{conversation_context or '（无）'}\n\n"
                    f"用户输入:\n{message}"
                ),
            },
        ]
    )
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {}


async def try_execute_ledger_text2sql(
    user_id: int,
    message: str,
    conversation_context: str = "",
) -> str | None:
    plan = await _plan_sql(message, conversation_context)
    if not plan or not plan.get("matched"):
        return None

    intent = str(plan.get("intent") or "unknown").strip().lower()
    if intent == "unknown":
        return None

    confidence = float(plan.get("confidence") or 0.0)
    if confidence < 0.60:
        return None

    sql = str(plan.get("sql") or "").strip()
    if sql.endswith(";") and sql.count(";") == 1:
        sql = sql[:-1].strip()
    ok, reason = _is_safe_sql(sql, intent, message)
    if not ok:
        return f"该账单操作被安全策略拦截：{reason}。请换一种更明确的说法。"

    params = _normalize_params(dict(plan.get("params") or {}))
    params["user_id"] = user_id
    params.setdefault("now_utc", datetime.utcnow())

    stmt = text(sql)
    async with AsyncSessionLocal() as db:
        try:
            if intent == "select":
                result = await db.execute(stmt, params)
                rows = result.mappings().all()
                if not rows:
                    return "没有匹配到账单记录。"
                if (
                    len(rows) == 1
                    and "total" in rows[0]
                    and "count" in rows[0]
                ):
                    total = float(rows[0].get("total") or 0)
                    count = int(rows[0].get("count") or 0)
                    return f"统计结果：共 {count} 笔，总额 {total:.2f} CNY。"
                lines = ["账单结果："]
                for row in rows[:12]:
                    row_id = row.get("id")
                    amount = float(row.get("amount") or 0)
                    currency = str(row.get("currency") or "CNY")
                    category = str(row.get("category") or "其他")
                    item = str(row.get("item") or "")
                    transaction_date = str(row.get("transaction_date") or row.get("created_at") or "")
                    time_text = transaction_date.replace("T", " ")[:16] if transaction_date else "未知时间"
                    lines.append(
                        f"#{row_id} | {time_text} | {amount:.2f} {currency} | {category} | {item}"
                    )
                return "\n".join(lines)

            if intent == "insert":
                result = await db.execute(stmt, params)
                await db.commit()
                first = result.mappings().first() if result.returns_rows else None
                if first:
                    return (
                        "已记账："
                        f"{first.get('item', '消费')} "
                        f"{float(first.get('amount') or 0):.2f} "
                        f"{first.get('currency', 'CNY')}，分类 {first.get('category', '其他')}。"
                    )
                return "已新增账单记录。"

            if intent == "update":
                result = await db.execute(stmt, params)
                await db.commit()
                affected = result.rowcount or 0
                if affected <= 0:
                    return "没有匹配到可修改的账单。"
                return f"已更新 {affected} 条账单记录。"

            if intent == "delete":
                result = await db.execute(stmt, params)
                await db.commit()
                affected = result.rowcount or 0
                if affected <= 0:
                    return "没有匹配到可删除的账单。"
                return f"已删除 {affected} 条账单记录。"
        except Exception:
            await db.rollback()
            return None

    return None
