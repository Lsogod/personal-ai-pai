from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate


SCHEDULE_WEATHER_CONDITIONAL_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You are a conditional weather-reminder parser. Output JSON only.\n"
                "Fields: conditional, condition_type, city, target_date, run_at_local, reminder_content, confidence.\n"
                "condition_type allowed: weather_good, weather_rain, none.\n"
                "If user asks 'if weather good/rain then remind me', set conditional=true.\n"
                "city must come from user message or context. Do not invent a city.\n"
                "target_date must be YYYY-MM-DD when clearly derivable, else empty.\n"
                "run_at_local must be local datetime string when provided, else empty.\n"
                "reminder_content should be concise action text, for example '晒衣服' or '带伞'."
            ),
        ),
        (
            "human",
            "conversation_context:\n{conversation_context}\n\nuser_message:\n{content}",
        ),
    ]
)


SCHEDULE_REMINDER_FALLBACK_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "你是提醒专用解析器。只输出 JSON。"
                "字段: is_reminder, confidence, run_at_local, reminder_content, event_type, priority, explicit_offsets_minutes, event_tags。"
                "is_reminder 只可为 true/false。"
                "如果用户表达了提醒诉求（例如“1分钟后提醒我测试”“明早9点提醒我开会”），is_reminder=true。"
                "run_at_local 必须换算为用户时区时间，格式 YYYY-MM-DD HH:MM[:SS]。"
                "若用户明确到秒（如“30秒后”），必须保留秒；否则秒填 00。"
                "reminder_content 必须是提醒标题本体（2-12字），去掉人称、时间、语气词。"
                "例如“明天中午12点我有个会” -> reminder_content=“开会”。"
                "event_type 仅可为 meeting/travel/deadline/appointment/payment/study/work/family/task/other。"
                "priority 仅可为 low/medium/high/critical。"
                "若用户明确说“提前X分钟/小时/天”，explicit_offsets_minutes 输出分钟整数数组；否则 []。"
                "event_tags 是可选标签数组，用于补充细分场景。"
                "用户时区: {timezone}。当前本地时间: {now_local}。"
            ),
        ),
        ("human", "会话上下文:\n{conversation_context}\n\n用户输入:\n{content}"),
    ]
)


SCHEDULE_INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "你是提醒与日历意图解析器，只输出 JSON。"
                "字段: intent, confidence, run_at_local, reminder_content, target_content, target_ids, reference_mode, selection_mode, event_type, priority, explicit_offsets_minutes, event_tags, calendar_scope, calendar_date, schedule_status_filter。"
                "intent 仅可为 reminder, update_by_name, update_by_scope, delete_by_name, delete_by_scope, calendar, time_query, context_recall, unknown。"
                "创建提醒时 intent=reminder。"
                "按名称修改提醒（如‘把开会改到明天11点’）intent=update_by_name，并给 target_content 与 run_at_local。"
                "按名称删除提醒（如‘删除开会这个提醒’）intent=delete_by_name，并给 target_content。"
                "按范围删除提醒（如‘删除明天所有未完成提醒’）intent=delete_by_scope。"
                "按范围修改提醒（如‘把明天所有提醒改到11点’）intent=update_by_scope。"
                "run_at_local 使用用户时区，格式 YYYY-MM-DD HH:MM[:SS]。"
                "若用户明确到秒，保留秒；否则秒可为00。"
                "若用户只给了日期/星期但未给具体时刻（例如“周五我有个会”），run_at_local 必须留空，不得猜测 11:00/08:00 等默认时间。"
                "reminder_content 必须是提醒标题本体（2-12字），去掉“我/你/提醒我”、时间词和语气词。"
                "例如“明天中午12点我有个会” -> reminder_content=“开会”。"
                "event_type 仅可为 meeting/travel/deadline/appointment/payment/study/work/family/task/other。"
                "priority 仅可为 low/medium/high/critical。"
                "explicit_offsets_minutes 为分钟整数数组，未提及则 []。"
                "查看日历/日程时 intent=calendar，calendar_scope 仅可为 today/tomorrow/week/month/date/yesterday/day_before_yesterday/last_week。"
                "若 scope=date，calendar_date 输出 YYYY-MM-DD。"
                "询问当前时间/日期/星期 intent=time_query。"
                "询问上下文回顾 intent=context_recall。"
                "schedule_status_filter 仅可为 all/pending/executed/cancelled，未提及时 all。"
                "reference_mode 仅可为 by_id/by_name/by_scope/latest/last_result_set/auto。"
                "当用户说‘这几个/这些/刚才那些’时，reference_mode=last_result_set。"
                "selection_mode 仅可为 all/single/subset/auto。"
                "confidence 范围 0~1。"
                "用户时区: {timezone}。当前本地时间: {now_local}。"
            ),
        ),
        ("human", "会话上下文:\n{conversation_context}\n\n用户输入:\n{content}"),
    ]
)


def build_schedule_weather_conditional_messages(
    *,
    content: str,
    conversation_context: str,
) -> list[BaseMessage]:
    return SCHEDULE_WEATHER_CONDITIONAL_PROMPT.format_messages(
        content=content,
        conversation_context=conversation_context,
    )


def build_schedule_reminder_fallback_messages(
    *,
    content: str,
    conversation_context: str,
    timezone: str,
    now_local: str,
) -> list[BaseMessage]:
    return SCHEDULE_REMINDER_FALLBACK_PROMPT.format_messages(
        content=content,
        conversation_context=conversation_context,
        timezone=timezone,
        now_local=now_local,
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
