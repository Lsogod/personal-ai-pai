from __future__ import annotations

import re
from datetime import datetime
from typing import Awaitable, Callable

from app.tools.finance import delete_ledger, get_latest_ledger, list_recent_ledgers, update_ledger


async def handle_ledger_command(
    *,
    content: str,
    session,
    user_id: int,
    user_platform: str,
    format_datetime: Callable[[datetime | None], str],
    normalize_category: Callable[[str | None], str],
) -> list[str] | None:
    if not content.startswith("/ledger"):
        return None

    if content.startswith("/ledger list"):
        rows = await list_recent_ledgers(session, user_id, limit=10)
        if not rows:
            return ["暂无账单记录。"]
        lines = ["最近账单："]
        for row in rows:
            lines.append(
                f"#{row.id} | {format_datetime(row.transaction_date)} | {row.amount:.2f} {row.currency} | {row.category} | {row.item}"
            )
        lines.append("可用：`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`")
        return ["\n".join(lines)]

    if content.startswith("/ledger update"):
        m = re.match(
            r"^/ledger\s+update\s+(\d+)\s+(\d+(?:\.\d{1,2})?)\s*(.*)$",
            content,
        )
        if not m:
            return [
                "用法：`/ledger update <id> <金额> [分类] [摘要]`，例如 `/ledger update 12 28 餐饮 晚饭`。"
            ]
        ledger_id = int(m.group(1))
        amount = float(m.group(2))
        tail = (m.group(3) or "").strip()
        category = "其他"
        item = ""
        if tail:
            parts = tail.split(maxsplit=1)
            category = normalize_category(parts[0])
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
            return [f"未找到账单 #{ledger_id}，或它不属于你。"]
        return [
            f"已更新账单 #{updated.id}：{updated.item} {updated.amount} {updated.currency}，分类 {updated.category}，时间 {format_datetime(updated.transaction_date)}。"
        ]

    if content.startswith("/ledger delete"):
        m = re.match(r"^/ledger\s+delete\s+(.+)$", content)
        if not m:
            return ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"]
        target = m.group(1).strip().lower().lstrip("#")
        if target == "latest":
            latest = await get_latest_ledger(session, user_id)
            if not latest:
                return ["暂无可删除的账单。"]
            deleted = await delete_ledger(
                session,
                user_id=user_id,
                ledger_id=latest.id,
                platform=user_platform,
            )
            if not deleted:
                return ["删除失败，请稍后重试。"]
            return [
                f"已删除最近一笔：#{deleted.id} {deleted.item} {deleted.amount} {deleted.currency}，时间 {format_datetime(deleted.transaction_date)}。"
            ]
        if not target.isdigit():
            return ["用法：`/ledger delete <id|latest>`，例如 `/ledger delete 12`。"]
        ledger_id = int(target)
        deleted = await delete_ledger(
            session,
            user_id=user_id,
            ledger_id=ledger_id,
            platform=user_platform,
        )
        if not deleted:
            return [f"未找到账单 #{ledger_id}，或它不属于你。"]
        return [
            f"已删除账单 #{deleted.id}：{deleted.item} {deleted.amount} {deleted.currency}，时间 {format_datetime(deleted.transaction_date)}。"
        ]

    return ["可用命令：`/ledger list`、`/ledger update <id> <金额> [分类] [摘要]`、`/ledger delete <id|latest>`。"]

