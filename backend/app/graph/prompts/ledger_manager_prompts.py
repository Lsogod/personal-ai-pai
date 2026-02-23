from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate


LEDGER_PENDING_SELECTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "你是待确认记账解析器。只输出 JSON。"
                "字段: mode, indexes, amount, category, item。"
                "mode 仅可为 indexes, amount, cancel, unknown。"
                "若用户选择候选项（如“1 2”“第二个和第三个”“选2和3”“前两个”），mode=indexes，indexes 输出数组。"
                "若用户给出直接金额（如“28.50”），mode=amount，amount 输出数字。"
                "若用户表示取消（如“取消/算了/不记了”），mode=cancel。"
                "若不确定，mode=unknown。"
                "category 仅在用户明确提到分类时填写（餐饮/交通/购物/居家/娱乐/医疗/其他），否则留空。"
                "item 仅在用户明确给出摘要时填写，否则留空。"
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
                "你是记账意图解析器，只输出 JSON。"
                "字段: intent, ledger_id, target_ids, target_item, amount, item, category, query_scope, query_date, reference_mode, selection_mode, confidence。"
                "intent 仅可为 insert, correct_latest, correct_by_id, correct_by_name, correct_by_scope, delete_latest, delete_by_id, delete_by_name, delete_by_scope, query, list, unknown。"
                "若用户纠正最近一笔（如‘错了，改成30’）intent=correct_latest。"
                "若用户纠正指定ID（如‘把账单#12改成28元’）intent=correct_by_id 并给 ledger_id。"
                "若用户纠正某个名称/摘要（如‘不对，爬山门票应该是200元’）intent=correct_by_name，并提取 target_item。"
                "若用户按范围纠正（如‘把今天餐饮都改成30’）intent=correct_by_scope。"
                "若用户删除最近一笔 intent=delete_latest。"
                "若用户删除指定ID intent=delete_by_id 并给 ledger_id。"
                "若用户删除某个名称/摘要 intent=delete_by_name，并提取 target_item。"
                "若用户按范围删除（如‘删除今天所有账单’）intent=delete_by_scope。"
                "若用户新增支出/收入 intent=insert。"
                "若用户查询统计 intent=query；若用户要列表 intent=list。无法确定时 intent=unknown。"
                "amount 是数字；item 是简洁摘要；category 不确定时填‘其他’。"
                "query_scope 仅可为 today/week/month/date/recent/all/yesterday/day_before_yesterday/last_week。"
                "当 query_scope=date 时，query_date 输出 YYYY-MM-DD。"
                "reference_mode 仅可为 by_id/by_name/by_scope/latest/last_result_set/auto。"
                "当用户说‘这几笔/这些/刚才那几个’时，reference_mode=last_result_set。"
                "selection_mode 仅可为 all/single/subset/auto。"
                "confidence 范围 0~1。"
            ),
        ),
        ("human", "会话上下文:\n{conversation_context}\n\n用户输入:\n{content}"),
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
) -> list[BaseMessage]:
    return LEDGER_INTENT_PROMPT.format_messages(
        content=content,
        conversation_context=conversation_context,
    )

