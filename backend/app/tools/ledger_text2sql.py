from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

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
_LIMIT_PATTERN = re.compile(r"\blimit\s+(\d+|:\w+)\b", re.IGNORECASE)


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
    compact = _USER_FILTER.sub("__USER_FILTER__", compact)
    compact = re.sub(
        r"(?i)\b(?:and|or)\b\s*__USER_FILTER__\s*\b(?:and|or)\b",
        " AND ",
        compact,
    )
    compact = re.sub(r"(?i)\b(?:and|or)\b\s*__USER_FILTER__", " ", compact)
    compact = re.sub(r"(?i)__USER_FILTER__\s*\b(?:and|or)\b", " ", compact)
    compact = compact.replace("__USER_FILTER__", " ")
    compact = re.sub(r"\(\s*\)", "", compact)
    compact = re.sub(r"(?i)^(and|or)\b", " ", compact)
    compact = re.sub(r"(?i)\b(and|or)$", " ", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact


def _strip_single_statement(sql: str) -> str:
    stmt = (sql or "").strip()
    if stmt.endswith(";") and stmt.count(";") == 1:
        stmt = stmt[:-1].strip()
    return stmt


def _is_safe_sql(sql: str, intent: str, user_message: str) -> tuple[bool, str]:
    stmt = _strip_single_statement(sql)
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


def _is_safe_preview_sql(sql: str) -> tuple[bool, str]:
    ok, reason = _is_safe_sql(sql, "select", "")
    if not ok:
        return False, reason
    stmt = _strip_single_statement(sql)
    lower = stmt.lower()
    if " order by " not in lower:
        return False, "preview_missing_order_by"
    if not _LIMIT_PATTERN.search(lower):
        return False, "preview_missing_limit"
    select_match = re.search(r"(?is)^\s*select\s+(.*?)\s+from\s+", stmt)
    if not select_match:
        return False, "preview_missing_select_projection"
    projection = select_match.group(1)
    projection_l = projection.lower()
    id_projected = bool(
        re.search(r"\bas\s+id\b", projection_l)
        or re.search(r"(^|,)\s*(?:\w+\.)?id\s*(,|$)", projection_l)
    )
    if not id_projected:
        return False, "preview_missing_id_projection"
    for required in ("amount", "category", "item"):
        if required not in projection_l:
            return False, f"preview_missing_{required}_projection"
    return True, ""


def _extract_where_clause(stmt: str) -> str:
    sql = _strip_single_statement(stmt)
    match = re.search(r"\bwhere\b", sql, flags=re.IGNORECASE)
    if not match:
        return ""
    where_part = sql[match.end() :].strip()
    where_part = re.split(
        r"\b(returning|order\s+by|limit)\b",
        where_part,
        flags=re.IGNORECASE,
        maxsplit=1,
    )[0].strip()
    return where_part


def _build_fallback_preview_sql_from_write_sql(write_sql: str) -> str:
    where_raw = _extract_where_clause(write_sql)
    where_rest = _strip_user_filter(where_raw)
    final_where = "user_id = :user_id"
    if where_rest:
        final_where = f"{final_where} AND ({where_rest})"
    return (
        "SELECT id, transaction_date AS occurred_at, amount, category, item, "
        "'' AS merchant, '' AS account, currency "
        f"FROM ledgers WHERE {final_where} "
        "ORDER BY transaction_date DESC, id DESC LIMIT :preview_limit"
    )


async def _plan_sql(message: str, conversation_context: str = "") -> dict[str, Any]:
    llm = get_llm(node_name="ledger_text2sql")
    runnable = llm.with_structured_output(LedgerText2SQLPlan)
    now = datetime.utcnow().isoformat()
    system_prompt = (
        "你是 PostgreSQL 的账单 Text-to-SQL 规划器。\n"
        "只能操作 ledgers 表。\n"
        "必须且只能返回一个 json 对象。\n"
        "只返回结构化字段：matched, intent, sql, params, summary, confidence。\n"
        "intent 只能是 select/insert/update/delete/unknown 之一。\n"
        "如果请求与账单无关，返回 matched=false 且 intent=unknown。\n"
        "使用具名参数（例如 :user_id）。\n"
        "对于 select/update/delete：SQL 必须包含 WHERE user_id = :user_id。\n"
        "对于 insert：插入列中必须显式包含 user_id。\n"
        "允许插入的列只有：user_id, amount, currency, category, item, transaction_date, image_url。\n"
        "不要使用不存在的列。\n"
        "category/item 的文本字面量保持用户原始措辞或语言。\n"
        "对于行级查询结果（不是聚合统计），应包含 id, transaction_date, amount, category, item, currency，"
        "并优先使用 ORDER BY transaction_date DESC, id DESC 和合理的 LIMIT。\n"
        f"当前 UTC 时间：{now}。相对时间表达必须基于这个时间戳解析。"
    )
    result = await runnable.ainvoke(
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"会话上下文:\n{conversation_context or '（无）'}\n\n"
                    f"用户消息:\n{message}"
                ),
            },
        ]
    )
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {}
async def _plan_write_preview_sql(
    *,
    operation: str,
    message: str,
    preview_hints: dict[str, Any] | None = None,
    conversation_context: str = "",
) -> dict[str, Any]:
    llm = get_llm(node_name="ledger_text2sql")
    runnable = llm.with_structured_output(LedgerText2SQLPlan)
    now = datetime.utcnow().isoformat()
    system_prompt = (
        "你是 PostgreSQL 的账单写入预览 SQL 规划器。\n"
        f"当前操作是：{operation}（仅允许 delete/update）。\n"
        "必须且只能返回一个 json 对象。\n"
        "只返回结构化字段：matched, intent, sql, params, summary, confidence。\n"
        "intent 必须是 select。\n"
        "生成提交前用于预览候选行的 SELECT SQL。\n"
        "安全规则：\n"
        "1) 只能查询 ledgers 表。\n"
        "2) 必须包含 WHERE user_id = :user_id。\n"
        "3) 必须包含 ORDER BY transaction_date DESC, id DESC。\n"
        "4) 必须包含 LIMIT :preview_limit。\n"
        "5) 必须返回这些列（或别名）：id, occurred_at, amount, category, item, merchant, account。\n"
        "使用 ledgers.id AS id，并将 transaction_date AS occurred_at。\n"
        "id 必须是真实账单主键值，不能为 null，也不能是常量。\n"
        "如果 merchant/account 不存在，返回空字符串别名。\n"
        "文本字面量保持用户原始措辞或语言。\n"
        "对于 update 请求，WHERE 应筛选待修改的源记录，而不是目标值。\n"
        "对于改写式更新（A->B），WHERE 应使用源值 A；除非用户明确要求，不要按目标值 B 过滤。\n"
        "对于多源改写更新（A 和 B -> C），WHERE 必须用 OR/IN 语义匹配源集合 {A,B}。\n"
        "不要把多源改写压缩成类似 'A B' 这样的单个拼接字面量。\n"
        "对于多源改写，目标值 C 只能出现在 update_fields/SET 一侧，不能出现在预览 WHERE 中。\n"
        "仅当用户文本有歧义时，才使用 preview_hints 辅助消歧。\n"
        "如果 preview_hints 含有 target_item，应将其视为更新预览中优先使用的源侧过滤条件。\n"
        "如果 preview_hints.target_item 含有多个源 token（例如按空格、逗号、'和'、'或'、'/' 分隔），"
        "请在 WHERE 中将它们展开为多个源候选条件。\n"
        f"当前 UTC 时间：{now}。"
    )
    result = await runnable.ainvoke(
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"会话上下文:\n{conversation_context or '（无）'}\n\n"
                    f"预览提示 JSON:\n{json.dumps(preview_hints or {}, ensure_ascii=False)}\n\n"
                    f"用户消息:\n{message}"
                ),
            },
        ]
    )
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {}
def _to_iso_text(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    return str(value or "").strip()


def _extract_row_id(row: dict[str, Any]) -> int:
    for key in ("id", "ledger_id", "bill_id"):
        try:
            value = int(row.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _normalize_preview_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _extract_row_id(row),
        "datetime": _to_iso_text(row.get("occurred_at") or row.get("transaction_date") or row.get("created_at")),
        "amount": round(float(row.get("amount") or 0), 2),
        "currency": str(row.get("currency") or "CNY"),
        "category": str(row.get("category") or ""),
        "item": str(row.get("item") or ""),
        "merchant": str(row.get("merchant") or ""),
        "account": str(row.get("account") or ""),
    }


def _summarize_preview_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = round(sum(float(row.get("amount") or 0) for row in rows), 2)
    times = [str(row.get("datetime") or "").strip() for row in rows if str(row.get("datetime") or "").strip()]
    categories: dict[str, int] = {}
    for row in rows:
        key = str(row.get("category") or "其他").strip() or "其他"
        categories[key] = categories.get(key, 0) + 1
    top_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "count": len(rows),
        "total": total,
        "time_start": times[-1] if times else "",
        "time_end": times[0] if times else "",
        "categories": top_categories,
    }


def _jsonable_params(params: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if isinstance(value, datetime):
            result[key] = _to_iso_text(value)
        else:
            result[key] = value
    return result


def _normalize_preview_hints(
    preview_hints: dict[str, Any] | None,
    update_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    if isinstance(preview_hints, dict):
        for key in (
            "intent",
            "target_item",
            "query_scope",
            "query_date",
            "category",
            "reference_mode",
            "selection_mode",
        ):
            value = preview_hints.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    hints[key] = cleaned
        raw_ids = preview_hints.get("target_ids")
        if isinstance(raw_ids, list):
            ids: list[int] = []
            for value in raw_ids:
                try:
                    ledger_id = int(value)
                except Exception:
                    continue
                if ledger_id > 0:
                    ids.append(ledger_id)
            if ids:
                hints["target_ids"] = ids[:200]
    if isinstance(update_fields, dict):
        updates: dict[str, Any] = {}
        amount_raw = update_fields.get("amount")
        try:
            amount = float(amount_raw) if amount_raw is not None else None
        except Exception:
            amount = None
        if amount is not None:
            updates["amount"] = amount
        category = str(update_fields.get("category") or "").strip()
        if category:
            updates["category"] = category
        item = str(update_fields.get("item") or "").strip()
        if item:
            updates["item"] = item
        if updates:
            hints["update_fields"] = updates
    return hints


async def plan_write_preview_text2sql(
    *,
    user_id: int,
    message: str,
    operation: str,
    conversation_context: str = "",
    preview_limit: int = 50,
    preview_hints: dict[str, Any] | None = None,
    update_fields: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    op = (operation or "").strip().lower()
    if op not in {"delete", "update"}:
        return None

    normalized_hints = _normalize_preview_hints(preview_hints, update_fields)
    plan = await _plan_write_preview_sql(
        operation=op,
        message=message,
        preview_hints=normalized_hints,
        conversation_context=conversation_context,
    )
    if not plan or not plan.get("matched"):
        return None

    intent = str(plan.get("intent") or "").strip().lower()
    if intent != "select":
        return None
    confidence = float(plan.get("confidence") or 0.0)
    if confidence < 0.60:
        return None

    sql = _strip_single_statement(str(plan.get("sql") or ""))
    ok, reason = _is_safe_preview_sql(sql)
    if not ok:
        sql = ""

    params = _normalize_params(dict(plan.get("params") or {}))
    params["user_id"] = user_id
    params["preview_limit"] = max(1, min(50, int(preview_limit or 50)))
    params.setdefault("now_utc", datetime.utcnow())

    stmt = text(sql) if sql else None
    async with AsyncSessionLocal() as db:
        try:
            rows: list[dict[str, Any]] = []
            if stmt is not None:
                result = await db.execute(stmt, params)
                rows = [dict(item) for item in result.mappings().all()]
            else:
                raise RuntimeError("invalid_preview_sql")
        except Exception as exc:
            await db.rollback()
            write_plan = await _plan_sql(message, conversation_context)
            write_intent = str(write_plan.get("intent") or "").strip().lower()
            write_sql = _strip_single_statement(str(write_plan.get("sql") or ""))
            write_ok, _ = _is_safe_sql(write_sql, write_intent, message)
            if not write_ok or write_intent not in {"delete", "update"}:
                return {
                    "ok": False,
                    "error": "preview_execute_failed",
                    "plan_intent": write_intent,
                    "plan_sql": write_sql[:500],
                    "detail": str(exc)[:300],
                    "operation": op,
                }
            if op == "delete" and write_intent != "delete":
                return {"ok": False, "error": "intent_mismatch", "operation": op}
            if op == "update" and write_intent != "update":
                return {"ok": False, "error": "intent_mismatch", "operation": op}

            sql = _build_fallback_preview_sql_from_write_sql(write_sql)
            ok2, reason2 = _is_safe_preview_sql(sql)
            if not ok2:
                return {
                    "ok": False,
                    "error": f"fallback_preview_blocked:{reason2}",
                    "operation": op,
                }
            params = _normalize_params(dict(write_plan.get("params") or {}))
            params["user_id"] = user_id
            params["preview_limit"] = max(1, min(50, int(preview_limit or 50)))
            params.setdefault("now_utc", datetime.utcnow())
            try:
                result = await db.execute(text(sql), params)
                rows = [dict(item) for item in result.mappings().all()]
            except Exception as exc2:
                return {
                    "ok": False,
                    "error": "preview_execute_failed",
                    "plan_intent": write_intent,
                    "plan_sql": write_sql[:500],
                    "preview_sql": sql[:500],
                    "detail": str(exc2)[:300],
                    "operation": op,
                }

    candidate_rows = [_normalize_preview_row(row) for row in rows[:50]]
    target_ids = [int(row.get("id") or 0) for row in candidate_rows if int(row.get("id") or 0) > 0][:200]
    if candidate_rows and not target_ids:
        write_plan = await _plan_sql(message, conversation_context)
        write_intent = str(write_plan.get("intent") or "").strip().lower()
        write_sql = _strip_single_statement(str(write_plan.get("sql") or ""))
        write_ok, _ = _is_safe_sql(write_sql, write_intent, message)
        if write_ok and write_intent == op:
            fallback_sql = _build_fallback_preview_sql_from_write_sql(write_sql)
            ok3, _ = _is_safe_preview_sql(fallback_sql)
            if ok3:
                fallback_params = _normalize_params(dict(write_plan.get("params") or {}))
                fallback_params["user_id"] = user_id
                fallback_params["preview_limit"] = max(1, min(50, int(preview_limit or 50)))
                fallback_params.setdefault("now_utc", datetime.utcnow())
                async with AsyncSessionLocal() as db:
                    try:
                        result = await db.execute(text(fallback_sql), fallback_params)
                        rows = [dict(item) for item in result.mappings().all()]
                    except Exception:
                        rows = []
                if rows:
                    sql = fallback_sql
                    params = fallback_params
                    candidate_rows = [_normalize_preview_row(row) for row in rows[:50]]
                    target_ids = [int(row.get("id") or 0) for row in candidate_rows if int(row.get("id") or 0) > 0][:200]
    if candidate_rows and not target_ids:
        return {
            "ok": False,
            "error": "preview_missing_row_ids",
            "operation": op,
        }
    summary = _summarize_preview_rows(candidate_rows)
    return {
        "ok": True,
        "operation": op,
        "preview_sql": sql,
        "preview_params": _jsonable_params(params),
        "summary": summary,
        "candidate_rows": candidate_rows,
        "target_ids": target_ids,
    }


def _build_ids_select_stmt() -> Any:
    return (
        text("SELECT id FROM ledgers WHERE user_id = :user_id AND id IN :target_ids ORDER BY id")
        .bindparams(bindparam("target_ids", expanding=True))
    )


def _build_delete_stmt() -> Any:
    return (
        text("DELETE FROM ledgers WHERE user_id = :user_id AND id IN :target_ids")
        .bindparams(bindparam("target_ids", expanding=True))
    )


def _build_update_stmt(set_clauses: list[str]) -> Any:
    sql = (
        f"UPDATE ledgers SET {', '.join(set_clauses)} "
        "WHERE user_id = :user_id AND id IN :target_ids"
    )
    return text(sql).bindparams(bindparam("target_ids", expanding=True))


async def commit_write_by_ids_text2sql(
    *,
    user_id: int,
    operation: str,
    target_ids: list[int],
    expected_count: int,
    update_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    op = (operation or "").strip().lower()
    ids: list[int] = []
    seen: set[int] = set()
    for item in list(target_ids or []):
        try:
            lid = int(item)
        except Exception:
            continue
        if lid <= 0 or lid in seen:
            continue
        seen.add(lid)
        ids.append(lid)
    ids = ids[:200]
    if op not in {"delete", "update"}:
        return {"ok": False, "error": "unsupported_operation"}
    if not ids:
        return {"ok": False, "error": "empty_target_ids"}

    expected = max(0, int(expected_count or 0))
    if expected <= 0:
        expected = len(ids)

    params: dict[str, Any] = {
        "user_id": int(user_id),
        "target_ids": ids,
    }
    fields = dict(update_fields or {})

    set_clauses: list[str] = []
    if op == "update":
        amount_value = fields.get("amount")
        try:
            amount = float(amount_value) if amount_value is not None else None
        except Exception:
            amount = None
        if amount is not None:
            params["amount"] = amount
            set_clauses.append("amount = :amount")

        category = str(fields.get("category") or "").strip()
        if category:
            params["category"] = category
            set_clauses.append("category = :category")
        item = str(fields.get("item") or "").strip()
        if item:
            params["item"] = item
            set_clauses.append("item = :item")
        if not set_clauses:
            return {"ok": False, "error": "missing_update_fields"}

    select_stmt = _build_ids_select_stmt()
    delete_stmt = _build_delete_stmt()
    update_stmt = _build_update_stmt(set_clauses) if op == "update" else None

    async with AsyncSessionLocal() as db:
        try:
            exists_result = await db.execute(select_stmt, params)
            existing_ids = [int(row[0]) for row in exists_result.all()]
            existing_count = len(existing_ids)
            if existing_count != expected:
                await db.rollback()
                return {
                    "ok": False,
                    "error": "count_mismatch",
                    "expected": expected,
                    "actual": existing_count,
                }

            if op == "delete":
                result = await db.execute(delete_stmt, params)
            else:
                result = await db.execute(update_stmt, params)  # type: ignore[arg-type]
            affected = int(result.rowcount or 0)
            if affected > expected:
                await db.rollback()
                return {
                    "ok": False,
                    "error": "rowcount_guard_triggered",
                    "expected": expected,
                    "actual": affected,
                }
            await db.commit()
            return {
                "ok": True,
                "operation": op,
                "expected": expected,
                "matched": existing_count,
                "affected": affected,
            }
        except Exception:
            await db.rollback()
            return {"ok": False, "error": "commit_execute_failed"}


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

    sql = _strip_single_statement(str(plan.get("sql") or ""))
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
                    row_data = dict(row)
                    row_id = _extract_row_id(row_data)
                    amount = float(row.get("amount") or 0)
                    currency = str(row.get("currency") or "CNY")
                    category = str(row.get("category") or "其他")
                    item = str(row.get("item") or "")
                    transaction_date = str(
                        row.get("occurred_at") or row.get("transaction_date") or row.get("created_at") or ""
                    )
                    time_text = transaction_date.replace("T", " ")[:16] if transaction_date else "未知时间"
                    row_prefix = f"#{row_id} | " if row_id > 0 else ""
                    lines.append(
                        f"{row_prefix}{time_text} | {amount:.2f} {currency} | {category} | {item}"
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
