from langchain.messages import BaseMessage
from langchain.prompts import ChatPromptTemplate


SCHEDULE_INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "你是提醒与日历统一计划器，请按 schema 输出结构化字段。"
                "只输出一个 JSON 对象，不要输出解释文本。"
                "字段: intent, confidence, confirmation_action, needs_clarification, clarify_question, run_at_local, time_precision, "
                "reminder_content, offsets_minutes, condition_type, condition_city, condition_date, "
                "target_content, target_ids, reference_mode, selection_mode, event_type, priority, "
                "calendar_scope, calendar_date, schedule_status_filter。"
                "intent 仅可为 reminder, update_by_name, update_by_scope, delete_by_name, delete_by_scope, "
                "calendar, time_query, context_recall, unknown。"
                "confirmation_action 仅可为 confirm/cancel/none。"
                "当用户表达“确认/可以/就这样”时用 confirm；表达“取消/不要了”时用 cancel。"
                "confidence 范围 0~1。"
                "needs_clarification 仅可为 true/false；若关键信息缺失必须为 true 并给出 clarify_question。"
                "run_at_local 使用用户时区，格式 YYYY-MM-DD HH:MM[:SS]。"
                "time_precision 仅可为 second/minute/none。"
                "reminder_content 必须是最终可执行提醒标题，不要输出占位词。"
                "offsets_minutes 是最终提醒偏移数组（单位分钟，0=准点），按从大到小输出。"
                "condition_type 仅可为 weather_good/weather_rain/none。"
                "condition_city 和 condition_date(YYYY-MM-DD)仅在 condition_type 非 none 时填写。"
                "天气条件提醒中，若缺城市，优先询问城市，不要先询问提醒次数。"
                "当用户表达“天气好/天气好的话/晴天/不下雨”时，condition_type 必须为 weather_good。"
                "当用户表达“下雨/雨天/有雨”时，condition_type 必须为 weather_rain。"
                "若用户未明确提醒次数，你要主动给出建议 offsets_minutes，并把 needs_clarification=true，"
                "clarify_question 用于向用户确认该建议（例如“我建议提前10分钟和准点提醒，确认吗？”）。"
                "如果无法确认提醒时间、提醒标题、提醒次数、城市或天气日期，必须触发澄清。"
                "澄清优先级：天气提醒先补城市，再补天气日期，再补提醒时间/提醒次数。"
                "可结合会话上下文补全本轮补充信息，例如上一轮追问城市，本轮仅回复“武汉”。"
                "禁止用默认时间或默认城市替用户补全。"
                "示例1：用户“明天天气好的话提醒我晒衣服” -> intent=reminder, condition_type=weather_good, needs_clarification=true,"
                "clarify_question=“我可以按天气条件创建提醒。请补充城市，例如：武汉。”。"
                "示例2：上轮已追问城市，本轮“武汉” -> 结合上下文补全条件城市，不要重置为 none。"
                "按名称修改提醒 intent=update_by_name，按范围修改 intent=update_by_scope。"
                "按名称删除提醒 intent=delete_by_name，按范围删除 intent=delete_by_scope。"
                "查看日历/日程 intent=calendar，calendar_scope 仅可为 today/tomorrow/week/month/date/yesterday/day_before_yesterday/last_week。"
                "若 scope=date，calendar_date 输出 YYYY-MM-DD。"
                "schedule_status_filter 仅可为 all/pending/executed/cancelled/failed。"
                "reference_mode 仅可为 by_id/by_name/by_scope/latest/last_result_set/auto。"
                "selection_mode 仅可为 all/single/subset/auto。"
                "用户时区: {timezone}。当前本地时间: {now_local}。"
            ),
        ),
        ("human", "会话上下文:\n{conversation_context}\n\n用户输入:\n{content}"),
    ]
)


def build_schedule_intent_messages(
    *,
    content: str,
    conversation_context: str,
    timezone: str,
    now_local: str,
) -> list[BaseMessage]:
    return SCHEDULE_INTENT_PROMPT.format_messages(
        content=content,
        conversation_context=conversation_context,
        timezone=timezone,
        now_local=now_local,
    )
