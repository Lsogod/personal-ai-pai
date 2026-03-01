from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate


LEDGER_PENDING_SELECTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "你是待确认记账解析器。请严格按 schema 输出结构化字段。\n"
                "字段: mode, indexes, amount, category, item。\n"
                "mode 仅可为 indexes / amount / cancel / unknown。\n"
                "规则:\n"
                "1) 用户选择候选项（如“第2个”“选2和3”“前两个”）=> mode=indexes，indexes 输出数组。\n"
                "2) 用户给出直接金额（如“28.5”）=> mode=amount，amount 输出数字。\n"
                "3) 用户表示取消（如“取消/算了/不记了”）=> mode=cancel。\n"
                "4) 不确定 => mode=unknown。\n"
                "5) category 仅在用户明确给出分类时填写，否则留空。\n"
                "6) item 仅在用户明确给出摘要时填写，否则留空。"
            ),
        ),
        (
            "human",
            (
                "会话上下文:\n{conversation_context}\n\n"
                "候选金额列表（序号从1开始）: {candidates}\n"
                "识别来源摘要: {detected_item}\n"
                "默认分类: {default_category}\n"
                "用户回复: {content}"
            ),
        ),
    ]
)


LEDGER_INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "你是账单意图解析器，请按 schema 输出结构化字段。\n"
                "字段: intent, ledger_id, target_ids, target_item, amount, item, category, query_scope, query_date, "
                "reference_mode, selection_mode, confidence。\n"
                "intent 仅可为: insert, correct_latest, correct_by_id, correct_by_name, correct_by_scope, "
                "delete_latest, delete_by_id, delete_by_name, delete_by_scope, query, list, unknown。\n"
                "query_scope 仅可为: today, week, month, last_month, date, recent, all, yesterday, "
                "day_before_yesterday, last_week。\n"
                "reference_mode 仅可为: by_id, by_name, by_scope, latest, last_result_set, auto。\n"
                "selection_mode 仅可为: all, single, subset, auto。\n"
                "相对时间词（今天/昨天/本周/上周/本月/上月）必须基于上下文给出的时间基准。\n"
                "当用户表达‘这几笔/这些/刚才那些’时，reference_mode=last_result_set。\n"
                "当 intent 为 correct_by_name/delete_by_name 且用户未明确要求“只改/只删某一条”，"
                "优先选择 selection_mode=all。\n"
                "关键规则（修改语义）:\n"
                "1) 对“把A改成B/将A修改为B/把A变更为B”这类表达，target_item=A（旧值/被修改对象），"
                "item=B（新值）。\n"
                "2) 不要把新值 B 放到 target_item。\n"
                "3) 对“把A和B改成C/将A、B修改为C”这类表达，target_item 必须保留源值集合（A,B），item=C。\n"
                "4) 仅当用户在当前输入中明确给出“新金额”时，amount 才可填写；"
                "若只是改摘要/改分类且未明确金额，amount 必须为 null，不能从上下文或统计值推断。\n"
                "5) 若用户仅说“改成28元”且无目标，按 correct_latest 处理。\n"
                "待确认任务规则:\n"
                "若上下文存在待确认任务，但用户当前输入本身是完整新请求（新增/查询/删除/修改），"
                "优先按新请求解析，不要当作对旧任务的确认。\n"
                "confidence 范围 0~1。"
            ),
        ),
        (
            "human",
            (
                "会话上下文:\n{conversation_context}\n\n"
                "待确认任务摘要:\n{pending_preview_hint}\n\n"
                "用户输入:\n{content}"
            ),
        ),
    ]
)


def build_ledger_pending_selection_messages(
    *,
    content: str,
    candidates: list[float],
    detected_item: str,
    default_category: str,
    conversation_context: str,
) -> list[BaseMessage]:
    return LEDGER_PENDING_SELECTION_PROMPT.format_messages(
        content=content,
        candidates=candidates,
        detected_item=detected_item,
        default_category=default_category,
        conversation_context=conversation_context,
    )


def build_ledger_intent_messages(
    *,
    content: str,
    conversation_context: str,
    pending_preview_hint: str = "",
) -> list[BaseMessage]:
    return LEDGER_INTENT_PROMPT.format_messages(
        content=content,
        conversation_context=conversation_context,
        pending_preview_hint=pending_preview_hint or "无",
    )
