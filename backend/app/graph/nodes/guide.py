from __future__ import annotations

from app.graph.state import GraphState


def _build_manual(platform: str) -> str:
    lines: list[str] = []
    lines.append("PAI 使用手册（快速版）")
    lines.append("")
    lines.append("1. 直接自然语言即可，不必记命令。")
    lines.append("2. 常见能力：记账、改账、删账、提醒、查看日历、创建/管理技能。")
    lines.append("")
    lines.append("常用表达示例：")
    lines.append("- 记账：`今天晚饭30元`")
    lines.append("- 改账：`把账单#12改成28元 分类餐饮`")
    lines.append("- 删账：`删除账单#12` / `删除今天的所有订单`")
    lines.append("- 提醒：`明天中午12点提醒我开会`")
    lines.append("- 日历：`看下本周日程和账单`")
    lines.append("- 新建技能：`帮我新增一个角色设定技能`")
    lines.append("")
    lines.append("命令兜底（可选）：")
    lines.append("- 会话：`/new` ` /history` ` /switch <id>`")
    lines.append("- 账单：`/ledger list` ` /ledger update <id> <金额> [分类] [摘要]` ` /ledger delete <id|latest>`")
    lines.append("- 日历：`/calendar today|week|month|YYYY-MM-DD`")
    lines.append("- 技能：`/skill list` ` /skill show <source:slug>` ` /skill create ...`")
    lines.append("- 帮助：`/help`")
    if platform == "web":
        lines.append("")
        lines.append("Web 端：顶部可切换 `聊天 / 技能 / 日历` 页面。")
    return "\n".join(lines)


async def guide_node(state: GraphState) -> GraphState:
    message = state["message"]
    content = (message.content or "").strip()
    platform = (message.platform or "").strip().lower()
    manual = _build_manual(platform)

    if "记账" in content and "改" in content:
        extra = "你也可以直接说“我刚那笔记错了，改成28元”，系统会尽量基于上下文理解。"
        return {**state, "responses": [f"{manual}\n\n{extra}"]}
    return {**state, "responses": [manual]}
