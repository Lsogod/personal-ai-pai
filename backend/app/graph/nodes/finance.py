import json
import re
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.state import GraphState
from app.models.user import User
from app.services.llm import get_llm
from app.services.runtime_context import get_session
from app.tools.finance import (
    delete_ledger,
    get_latest_ledger,
    insert_ledger,
    list_recent_ledgers,
    update_ledger,
)
from app.tools.ledger_text2sql import try_execute_ledger_text2sql
from app.tools.vision import analyze_receipt


AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)")
LEDGER_ID_PATTERN = re.compile(r"(?:账单\s*#?\s*|#)(\d+)")
CORRECTION_HINT_PATTERN = re.compile(r"(错了|更正|改成|改为|修正|记错|不是|应该是)")
QUERY_HINT_PATTERN = re.compile(r"(统计|总额|消费|账单概览|花了多少|本月)")
DELETE_HINT_PATTERN = re.compile(r"(删除|删掉|去掉|移除)")

CATEGORY_MAP = {
    "餐饮": "餐饮",
    "吃饭": "餐饮",
    "午饭": "餐饮",
    "晚饭": "餐饮",
    "早餐": "餐饮",
    "交通": "交通",
    "打车": "交通",
    "地铁": "交通",
    "公交": "交通",
    "购物": "购物",
    "买": "购物",
    "超市": "购物",
    "居家": "居家",
    "房租": "居家",
    "水电": "居家",
    "娱乐": "娱乐",
    "电影": "娱乐",
    "医疗": "医疗",
    "看病": "医疗",
}


def _extract_amount(text: str) -> float | None:
    matches = AMOUNT_PATTERN.findall(text or "")
    if not matches:
        return None
    try:
        return float(matches[-1])
    except Exception:
        return None


def _extract_correction_amount(text: str) -> float | None:
    patterns = [
        r"(?:改成|改为|更正为|应该是|不是)\s*(\d+(?:\.\d{1,2})?)",
        r"(\d+(?:\.\d{1,2})?)\s*(?:元|块)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            try:
                return float(match.group(1))
            except Exception:
                continue
    return _extract_amount(text)


def _extract_ledger_id(text: str) -> int | None:
    match = LEDGER_ID_PATTERN.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _normalize_category(text: str | None) -> str:
    raw = (text or "").strip()
    if not raw:
        return "其他"
    for key, value in CATEGORY_MAP.items():
        if key in raw:
            return value
    return raw if len(raw) <= 10 else "其他"


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _clean_item(item: str) -> str:
    value = (item or "").strip()
    if not value:
        return ""
    value = re.sub(r"^(错了|更正|改成|改为|修正|记错|不是|应该是)\s*", "", value)
    value = re.sub(r"^(今天|刚才|刚刚)\s*", "", value)
    value = re.sub(r"\d+(?:\.\d{1,2})?", "", value)
    value = value.replace("元", "").replace("块", "").replace("记账", "").strip()
    value = value.strip("，,。.!！?？")
    return value


async def _understand_finance_message(content: str) -> dict:
    llm = get_llm()
    system = SystemMessage(
        content=(
            "你是记账意图解析器。只输出 JSON。"
            "字段: intent, ledger_id, amount, item, category, confidence。"
            "intent 仅可为 insert, correct_latest, correct_by_id, delete_latest, delete_by_id, query, list。"
            "若用户在纠正上一笔（例如“错了...”“改成...”），intent=correct_latest。"
            "若用户在纠正指定账单（例如“把账单#12改成28元”），intent=correct_by_id，并给出 ledger_id。"
            "若用户在删除最近一笔，intent=delete_latest。"
            "若用户在删除指定账单（例如“删除账单#12”），intent=delete_by_id，并给出 ledger_id。"
            "若用户在新增一笔支出，intent=insert。"
            "若用户在问统计数据，intent=query。"
            "若用户在要账单列表，intent=list。"
            "amount 为数字；item 为简洁摘要，必须去掉“错了/改成”等词。"
            "category 不确定填“其他”。"
        )
    )
    human = HumanMessage(content=content)
    response = await llm.ainvoke([system, human])
    return _parse_json_object(str(response.content))


async def finance_node(state: GraphState) -> GraphState:
    message = state["message"]
    session = get_session()
    user = await session.get(User, state["user_id"])
    if not user:
        return {**state, "responses": ["未找到用户信息。"]}
    user_id = user.id
    user_platform = user.platform

    if message.image_urls:
        result = await analyze_receipt(message.image_urls[0])
        if result.get("confidence", 0) >= 0.8:
            ledger = await insert_ledger(
                session,
                user_id=user_id,
                amount=float(result.get("amount", 0)),
                category=_normalize_category(str(result.get("category", "其他"))),
                item=str(result.get("item", result.get("merchant", "消费"))),
                transaction_date=datetime.utcnow(),
                image_url=message.image_urls[0],
                platform=user_platform,
            )
            return {
                **state,
                "responses": [f"已记账：{ledger.item} {ledger.amount} {ledger.currency}，分类 {ledger.category}。"],
            }
        return {
            **state,
            "responses": ["我没能准确识别这张小票，能否告诉我金额和分类？"],
        }

    content = (message.content or "").strip()

    # deterministic command mode
    if content.startswith("/ledger"):
        if content.startswith("/ledger list"):
            rows = await list_recent_ledgers(session, user_id, limit=10)
            if not rows:
                return {**state, "responses": ["暂无账单记录。"]}
            lines = ["最近账单："]
            for row in rows:
                lines.append(
                    f"#{row.id} | {row.amount:.2f} {row.currency} | {row.category} | {row.item}"
                )
            lines.append("可用：`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`")
            return {**state, "responses": ["\n".join(lines)]}

        if content.startswith("/ledger update"):
            m = re.match(
                r"^/ledger\s+update\s+(\d+)\s+(\d+(?:\.\d{1,2})?)\s*(.*)$",
                content,
            )
            if not m:
                return {
                    **state,
                    "responses": [
                        "用法：`/ledger update <id> <金额> [分类] [摘要]`，例如 `/ledger update 12 28 餐饮 晚饭`。"
                    ],
                }
            ledger_id = int(m.group(1))
            amount = float(m.group(2))
            tail = (m.group(3) or "").strip()
            category = "其他"
            item = ""
            if tail:
                parts = tail.split(maxsplit=1)
                category = _normalize_category(parts[0])
                item = parts[1] if len(parts) > 1 else ""

            updated = await update_ledger(
                session,
                user_id=user_id,
                ledger_id=ledger_id,
                amount=amount,
                category=category if category != "其他" else None,
                item=item or None,
                platform=user_platform,
            )
            if not updated:
                return {**state, "responses": [f"未找到账单 #{ledger_id}，或它不属于你。"]}
            return {
                **state,
                "responses": [
                    f"已更新账单 #{updated.id}：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}。"
                ],
            }

        if content.startswith("/ledger delete"):
            m = re.match(r"^/ledger\s+delete\s+(.+)$", content)
            if not m:
                return {
                    **state,
                    "responses": ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"],
                }
            target = m.group(1).strip().lower().lstrip("#")
            if target == "latest":
                latest = await get_latest_ledger(session, user_id)
                if not latest:
                    return {**state, "responses": ["暂无可删除的账单。"]}
                deleted = await delete_ledger(
                    session,
                    user_id=user_id,
                    ledger_id=latest.id,
                    platform=user_platform,
                )
                if not deleted:
                    return {**state, "responses": ["删除失败，请稍后重试。"]}
                return {
                    **state,
                    "responses": [
                        f"已删除最近一笔：#{deleted.id} {deleted.item} {deleted.amount} {deleted.currency}。"
                    ],
                }
            if not target.isdigit():
                return {
                    **state,
                    "responses": ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"],
                }
            ledger_id = int(target)
            deleted = await delete_ledger(
                session,
                user_id=user_id,
                ledger_id=ledger_id,
                platform=user_platform,
            )
            if not deleted:
                return {**state, "responses": [f"未找到账单 #{ledger_id}，或它不属于你。"]}
            return {
                **state,
                "responses": [f"已删除账单 #{deleted.id}：{deleted.item} {deleted.amount} {deleted.currency}。"],
            }

        return {**state, "responses": ["可用命令：`/ledger list`、`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`。"]}

    # LLM text-to-sql path (ledger scoped, user scoped).
    try:
        sql_response = await try_execute_ledger_text2sql(
            user_id=user_id,
            message=content,
        )
    except Exception:
        sql_response = None
    if sql_response:
        return {**state, "responses": [sql_response]}

    parsed: dict = {}
    try:
        parsed = await _understand_finance_message(content)
    except Exception:
        parsed = {}

    intent = str(parsed.get("intent") or "").strip().lower()

    ledger_id = parsed.get("ledger_id")
    try:
        ledger_id = int(ledger_id) if ledger_id is not None else None
    except Exception:
        ledger_id = None
    if ledger_id is None:
        ledger_id = _extract_ledger_id(content)

    amount = parsed.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except Exception:
        amount = None
    if amount is None:
        amount = _extract_correction_amount(content) if ledger_id is not None else _extract_amount(content)

    item = _clean_item(str(parsed.get("item") or ""))
    if not item:
        item = _clean_item(content)

    category = _normalize_category(str(parsed.get("category") or ""))
    if category == "其他":
        category = _normalize_category(content)

    if not intent:
        if "账单列表" in content or "列出账单" in content or "最近账单" in content:
            intent = "list"
        elif DELETE_HINT_PATTERN.search(content) and ledger_id is not None:
            intent = "delete_by_id"
        elif DELETE_HINT_PATTERN.search(content):
            intent = "delete_latest"
        elif QUERY_HINT_PATTERN.search(content):
            intent = "query"
        elif ledger_id is not None and CORRECTION_HINT_PATTERN.search(content):
            intent = "correct_by_id"
        elif CORRECTION_HINT_PATTERN.search(content):
            intent = "correct_latest"
        else:
            intent = "insert"
    elif ledger_id is not None and CORRECTION_HINT_PATTERN.search(content) and intent in {"insert", "correct_latest", "query"}:
        intent = "correct_by_id"
    elif ledger_id is not None and DELETE_HINT_PATTERN.search(content) and intent in {"insert", "delete_latest", "query"}:
        intent = "delete_by_id"

    if intent == "list":
        rows = await list_recent_ledgers(session, user_id, limit=10)
        if not rows:
            return {**state, "responses": ["暂无账单记录。"]}
        lines = ["最近账单："]
        for row in rows:
            lines.append(
                f"#{row.id} | {row.amount:.2f} {row.currency} | {row.category} | {row.item}"
            )
        lines.append("要修改指定账单可说：`把账单#12改成28元 分类餐饮`。删除可说：`删除账单#12`。")
        return {**state, "responses": ["\n".join(lines)]}

    if intent == "query":
        return {**state, "responses": ["你可以在右侧账单概览查看统计，或说“本月花了多少”。"]}

    if intent == "delete_by_id":
        if ledger_id is None:
            return {**state, "responses": ["请指定账单 ID，例如：`删除账单#12`。"]}
        deleted = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            platform=user_platform,
        )
        if not deleted:
            return {**state, "responses": [f"未找到账单 #{ledger_id}，或它不属于你。"]}
        return {
            **state,
            "responses": [f"已删除账单 #{deleted.id}：{deleted.item} {deleted.amount} {deleted.currency}。"],
        }

    if intent == "delete_latest":
        latest = await get_latest_ledger(session, user_id)
        if not latest:
            return {**state, "responses": ["暂无可删除的账单。"]}
        deleted = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=latest.id,
            platform=user_platform,
        )
        if not deleted:
            return {**state, "responses": ["删除失败，请稍后重试。"]}
        return {
            **state,
            "responses": [f"已删除最近一笔：#{deleted.id} {deleted.item} {deleted.amount} {deleted.currency}。"],
        }

    if amount is None:
        return {**state, "responses": ["请告诉我金额，例如：今天晚饭 28 元。"]}

    if intent == "correct_by_id":
        if ledger_id is None:
            return {**state, "responses": ["请指定账单 ID，例如：`把账单#12改成28元`。"]}
        updated = await update_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            amount=amount,
            category=category if category != "其他" else None,
            item=item or None,
            platform=user_platform,
        )
        if not updated:
            return {**state, "responses": [f"未找到账单 #{ledger_id}，或它不属于你。"]}
        return {
            **state,
            "responses": [
                f"已更正账单 #{updated.id}：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}。"
            ],
        }

    if intent == "correct_latest":
        latest = await get_latest_ledger(session, user_id)
        if not latest:
            return {**state, "responses": ["你还没有可更正的账单，请先记一笔。"]}
        updated = await update_ledger(
            session,
            user_id=user_id,
            ledger_id=latest.id,
            amount=amount,
            category=category if category != "其他" else None,
            item=item or latest.item,
            platform=user_platform,
        )
        if not updated:
            return {**state, "responses": ["更正失败，请稍后重试。"]}
        return {
            **state,
            "responses": [
                f"已更正最近一笔：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}（账单 #{updated.id}）。"
            ],
        }

    ledger = await insert_ledger(
        session,
        user_id=user_id,
        amount=amount,
        category=category,
        item=item or "消费",
        transaction_date=datetime.utcnow(),
        platform=user_platform,
    )
    return {
        **state,
        "responses": [f"已记账：{ledger.item} {ledger.amount} {ledger.currency}，分类 {ledger.category}。"],
    }
