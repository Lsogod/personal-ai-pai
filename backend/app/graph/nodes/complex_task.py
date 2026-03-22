from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.graph.context import render_conversation_context
from app.graph.nodes.chat_manager import _try_handle_profile_intent, chat_manager_node
from app.graph.nodes.help_center import help_center_node
from app.graph.nodes.ledger_manager import ledger_manager_node
from app.graph.nodes.schedule_manager import schedule_manager_node
from app.graph.nodes.skill_manager import skill_manager_node
from app.graph.state import GraphState
from app.models.user import User
from app.schemas.unified import UnifiedMessage
from app.services.audit import log_event
from app.services.llm import get_llm
from app.services.langchain_tools import AgentToolContext, ToolInvocationContext, build_langchain_tools
from app.services.runtime_context import get_session
from app.services.tool_executor import execute_capability_with_usage
from app.services.tool_registry import list_runtime_tool_metas

logger = logging.getLogger(__name__)


class PlanRetry(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=3)
    backoff_ms: int = Field(default=0, ge=0, le=5000)


class PlanCondition(BaseModel):
    step_id: str = Field(default="")
    field: str = Field(default="matched")
    equals: Any = Field(default=True)


class PlanStep(BaseModel):
    step_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=120)
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=20000, ge=1000, le=120000)
    retry: PlanRetry = Field(default_factory=PlanRetry)
    when: PlanCondition | None = None


class ComplexTaskPlan(BaseModel):
    goal: str = Field(default="")
    steps: list[PlanStep] = Field(default_factory=list, min_length=1, max_length=20)


class ComplexTaskPlanExtraction(BaseModel):
    need_complex: bool | None = Field(default=True)
    reason: str | None = Field(default="")
    plan: ComplexTaskPlan | dict[str, Any] | list[Any] | None = None

    non_complex_node_action: str | None = Field(default="")
    clarify_question: str | None = Field(default="")
    followup_expected: bool | None = Field(default=False)
    followup_topic: str | None = Field(default="")
    missing_slots: list[str] | None = Field(default_factory=list)


class ClarificationReply(BaseModel):
    question: str | None = Field(default="")


class WeatherRainCheckExtraction(BaseModel):
    matched: bool | None = Field(default=False)
    target_date: str | None = Field(default="")
    dayweather: str | None = Field(default="")
    nightweather: str | None = Field(default="")
    period: str | None = Field(default="all")
    reason: str | None = Field(default="")
    confidence: float | None = Field(default=0.0, ge=0.0, le=1.0)


class ComplexTaskOutcomeExtraction(BaseModel):
    completed: bool | None = Field(default=True)
    final_response: str | None = Field(default="")
    followup_question: str | None = Field(default="")
    reason: str | None = Field(default="")


class ComplexPlanDecision(BaseModel):
    mode: str | None = Field(default="clarify")
    reason: str | None = Field(default="")
    plan_summary: str | None = Field(default="")
    clarify_question: str | None = Field(default="")
    non_complex_node_action: str | None = Field(default="")
    followup_expected: Any = Field(default=False)
    followup_topic: str | None = Field(default="")


class ComplexFollowupExtraction(BaseModel):
    followup_expected: Any = Field(default=False)
    followup_topic: str | None = Field(default="")
    reason: str | None = Field(default="")


NODE_ACTIONS: dict[str, Callable[[GraphState], Awaitable[GraphState]]] = {
    "ledger_manager": ledger_manager_node,
    "schedule_manager": schedule_manager_node,
    "chat_manager": chat_manager_node,
    "skill_manager": skill_manager_node,
    "help_center": help_center_node,
}


def _parse_json_object(content: str) -> dict[str, Any]:
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


def _coerce_plan(value: Any) -> ComplexTaskPlan | None:
    if isinstance(value, ComplexTaskPlan):
        return value
    if isinstance(value, dict):
        try:
            return ComplexTaskPlan.model_validate(value)
        except Exception:
            return None
    return None


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "on", "是"}:
        return True
    if text in {"false", "0", "no", "n", "off", "否", "不是"}:
        return False
    return default


def _deep_get(data: Any, path: str) -> Any:
    cursor: Any = data
    if not path:
        return cursor
    for part in path.split("."):
        token = part.strip()
        if not token:
            continue
        if isinstance(cursor, dict):
            cursor = cursor.get(token)
            continue
        if isinstance(cursor, list):
            try:
                idx = int(token)
            except Exception:
                return None
            if idx < 0 or idx >= len(cursor):
                return None
            cursor = cursor[idx]
            continue
        return None
    return cursor


PLACEHOLDER_PATTERN = re.compile(r"^\$([a-zA-Z0-9_\-]+)(?:\.(.+))?$")


def _resolve_value(value: Any, step_outputs: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = PLACEHOLDER_PATTERN.match(value.strip())
        if not match:
            return value
        ref_step = str(match.group(1) or "").strip()
        ref_path = str(match.group(2) or "").strip()
        ref_value = step_outputs.get(ref_step)
        if ref_path:
            return _deep_get(ref_value, ref_path)
        return ref_value
    if isinstance(value, list):
        return [_resolve_value(item, step_outputs) for item in value]
    if isinstance(value, dict):
        return {str(k): _resolve_value(v, step_outputs) for k, v in value.items()}
    return value


def _arg_is_grounded_in_state(value: str, state: GraphState) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    message = state.get("message")
    snippets: list[str] = [str(getattr(message, "content", "") or "")]
    extra = dict(state.get("extra") or {})
    raw_messages = extra.get("context_messages")
    if isinstance(raw_messages, list):
        for item in raw_messages[-16:]:
            if not isinstance(item, dict):
                continue
            if str(item.get("role") or "").strip().lower() != "user":
                continue
            snippets.append(str(item.get("content") or ""))
    corpus = "\n".join(snippets)
    return text in corpus


def _validate_plan(plan: ComplexTaskPlan) -> tuple[bool, str]:
    step_map: dict[str, PlanStep] = {}
    for step in plan.steps:
        if step.step_id in step_map:
            return False, f"duplicate step_id: {step.step_id}"
        step_map[step.step_id] = step

    for step in plan.steps:
        if step.step_id in set(step.depends_on):
            return False, f"self dependency: {step.step_id}"
        for dep in step.depends_on:
            if dep not in step_map:
                return False, f"unknown dependency: {dep}"
        if step.when and step.when.step_id and step.when.step_id not in step_map:
            return False, f"unknown condition dependency: {step.when.step_id}"

    visited: dict[str, int] = defaultdict(int)

    def _dfs(step_id: str) -> bool:
        state = visited[step_id]
        if state == 1:
            return True
        if state == 2:
            return False
        visited[step_id] = 1
        for dep in step_map[step_id].depends_on:
            if _dfs(dep):
                return True
        visited[step_id] = 2
        return False

    for sid in step_map:
        if _dfs(sid):
            return False, "dependency cycle detected"
    return True, ""


def _build_action_catalog(runtime_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for node_name in sorted(NODE_ACTIONS.keys()):
        rows.append(
            {
                "action": f"node.{node_name}",
                "description": "执行该领域节点，使用 args.input 作为用户输入。",
            }
        )
    for tool in runtime_tools:
        source = str(tool.get("source") or "").strip().lower()
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        description = f"调用运行时工具 `{name}`（source={source}）。"
        if name == "schedule_insert":
            description = (
                "创建提醒。args 必须使用: content(提醒文本), "
                "trigger_time(YYYY-MM-DD HH:MM:SS), status(可选，默认PENDING), job_id(可选)。"
            )
        elif name == "maps_weather":
            description = "查询天气。args 使用 city 或 adcode。"
        elif name == "now_time":
            description = "查询当前时间。args 可用 timezone。"
        rows.append(
            {
                "action": f"tool.{name}",
                "description": description,
            }
        )
    rows.append(
        {
            "action": "logic.weather_rain_check",
            "description": (
                "根据天气输出判断是否有降水。"
                "args 支持 weather_step|weather_output、target_date、period(day|night|afternoon|evening)。"
            ),
        }
    )
    return rows


async def _plan_complex_task(
    *,
    content: str,
    conversation_context: str,
    runtime_tools: list[dict[str, Any]],
    routing_hint: dict[str, Any] | None = None,
    pending_context: dict[str, Any] | None = None,
) -> ComplexTaskPlanExtraction:
    settings = get_settings()
    tz = settings.timezone
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    action_catalog = _build_action_catalog(runtime_tools)

    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ComplexTaskPlanExtraction)
    system = SystemMessage(
        content=(
            "你是复杂任务规划器。只输出一个 JSON 对象。\n"
            "输出必须是 json 对象。\n"
            "输出字段：need_complex, reason, plan, non_complex_node_action, clarify_question, "
            "followup_expected, followup_topic, missing_slots。\n"
            "判定原则：\n"
            "1) 需要跨节点编排、条件分支、外部工具证据 + 元操作时，need_complex=true。\n"
            "2) 单节点可直接完成时，need_complex=false，并给 non_complex_node_action。\n"
            "3) 若缺少关键参数且当前无任何可执行步骤，need_complex=false，并给 clarify_question。\n"
            "4) 若虽有缺参但仍有可执行步骤，need_complex=true，输出 partial plan，后续再追问缺参。\n"
            "5) pending_context.active=true 时，当前消息通常是补充信息，需与上下文合并后再判断。\n"
            "计划规则：\n"
            "1) step_id 唯一，步骤数 <= 8，只能使用动作目录中的 action。\n"
            "2) 依赖用 depends_on，条件分支用 when={step_id, field, equals}。\n"
            "3) args 支持占位符 $<step_id> 或 $<step_id>.<field_path>。\n"
            "4) 不得编造工具输出。\n"
            "天气条件提醒规则（如“明天天气好的话提醒我出游”）：\n"
            "1) 这是条件任务，通常 need_complex=true。\n"
            "2) 能执行时优先模板：tool.maps_weather -> logic.weather_rain_check -> tool.schedule_insert。\n"
            "3) 城市未知先问 location；时间未知可先做天气查询，再根据执行结果追问 time。\n"
            "4) clarify_question 只问缺失参数，不要问授权式问题（如“是否需要我查询”）。\n"
            "5) missing_slots 仅用：location,time,date,condition,content,amount,category,target。\n"
            "输出约束：\n"
            "1) need_complex=true 时，non_complex_node_action/clarify_question 置空，followup_expected=false。\n"
            "2) need_complex=true 时，plan 必须是对象（goal + steps），不能是数组。\n"
            "3) need_complex=false 且为澄清时，followup_expected=true 并填写 followup_topic。\n"
            "4) 仅输出 JSON，不输出解释。"
            f"用户时区：{tz}。当前本地时间：{now_local}。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"路由提示(可为空):\n{json.dumps(routing_hint or {}, ensure_ascii=False)}\n\n"
            f"待续复杂任务上下文(可为空):\n{json.dumps(pending_context or {}, ensure_ascii=False)}\n\n"
            f"动作目录:\n{json.dumps(action_catalog, ensure_ascii=False)}"
        )
    )
    return await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=120)


async def _generate_clarification(
    *,
    content: str,
    conversation_context: str,
    reason: str,
) -> str:
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ClarificationReply)
    system = SystemMessage(
        content=(
            "你需要提出一个简洁澄清问题，以解除执行阻塞。\n"
            "输出必须是 json。\n"
            "不要暴露内部错误，不要编造事实。\n"
            "问题中请加入一条可见的简短决策说明（不要输出内部推理细节），"
            "例如“我已识别到X，当前缺少Y，所以需要你补充Z”。\n"
            "请仅输出 JSON。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"原因:\n{reason}"
        )
    )
    try:
        result = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=20)
        question = str(result.question or "").strip()
        return question or "请补充更具体的信息，我再继续执行。"
    except Exception:
        return "请补充更具体的信息，我再继续执行。"


async def _generate_clarification_from_hint(
    *,
    content: str,
    conversation_context: str,
    hint_payload: dict[str, Any],
) -> str:
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ClarificationReply)
    system = SystemMessage(
        content=(
            "你需要基于路由提示生成一条简洁澄清问题。\n"
            "输出必须是 json。\n"
            "不要暴露内部字段名，不要编造用户未提及事实。\n"
            "问题中请加入一条可见的简短决策说明（不要输出内部推理细节）。\n"
            "请仅输出 JSON。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"路由提示:\n{json.dumps(hint_payload, ensure_ascii=False)}"
        )
    )
    try:
        result = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=20)
        question = str(result.question or "").strip()
        return question
    except Exception:
        return ""


def _clone_message(message: UnifiedMessage, *, content: str, image_urls: list[str] | None = None) -> UnifiedMessage:
    payload = message.model_dump()
    payload["content"] = content
    if image_urls is not None:
        payload["image_urls"] = image_urls
    return UnifiedMessage(**payload)


async def _execute_node_handoff(
    *,
    node_action: str,
    base_state: GraphState,
    content: str,
) -> tuple[list[str], dict[str, Any]]:
    action = node_action.strip().lower()
    if not action.startswith("node."):
        return [], {}
    node_name = action.split(".", 1)[1].strip().lower()
    node_fn = NODE_ACTIONS.get(node_name)
    if node_fn is None:
        return [], {}
    message = base_state["message"]
    sub_state: GraphState = dict(base_state)
    sub_state["message"] = _clone_message(message, content=content)
    result = await node_fn(sub_state)
    responses = [str(x) for x in (result.get("responses") or []) if str(x).strip()]
    extra = result.get("extra")
    return responses, (dict(extra) if isinstance(extra, dict) else {})


def _resolve_tool_source(tool_name: str, runtime_tools: list[dict[str, Any]]) -> str:
    target = tool_name.strip().lower()
    for row in runtime_tools:
        if str(row.get("name") or "").strip().lower() == target:
            return str(row.get("source") or "mcp").strip().lower() or "mcp"
    if target in {"now_time", "fetch_url", "tool_list", "tool_call"}:
        return "builtin"
    return "mcp"


def _coerce_target_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return ""


async def _execute_logic_weather_rain_check(
    *,
    args: dict[str, Any],
    step_outputs: dict[str, Any],
) -> dict[str, Any]:
    weather_output: Any = args.get("weather_output")
    weather_step = str(args.get("weather_step") or "").strip()
    if weather_output is None and weather_step:
        weather_output = step_outputs.get(weather_step)
        if isinstance(weather_output, dict):
            if "output" in weather_output:
                weather_output = weather_output.get("output")
            elif "tool_output" in weather_output:
                weather_output = weather_output.get("tool_output")

    if isinstance(weather_output, (dict, list)):
        try:
            weather_text = json.dumps(weather_output, ensure_ascii=False)
        except Exception:
            weather_text = str(weather_output)
    else:
        weather_text = str(weather_output or "")

    target_date = _coerce_target_date(args.get("target_date"))
    if not target_date:
        tz = ZoneInfo(get_settings().timezone)
        tomorrow = (datetime.now(tz).date() + timedelta(days=1)).isoformat()
        target_date = tomorrow

    period = str(args.get("period") or "").strip().lower()
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(WeatherRainCheckExtraction)
    system = SystemMessage(
        content=(
            "你是天气降水判定器。请仅返回 JSON 结构化字段：matched,target_date,dayweather,nightweather,period,reason,confidence。\n"
            "输出必须是 json。\n"
            "定义：matched=true 表示目标日期在指定时段有降水（雨/雪/雨夹雪等）。\n"
            "必须只依据给定天气工具原始输出，不得编造。只输出 JSON。"
        )
    )
    human = HumanMessage(
        content=(
            f"target_date={target_date}\n"
            f"period={period or 'all'}\n\n"
            f"天气工具原始输出:\n{weather_text}"
        )
    )
    try:
        parsed = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=25)
        matched = bool(getattr(parsed, "matched", False))
        return {
            "matched": matched,
            "is_rain_expected": matched,
            "unknown": False,
            "target_date": str(getattr(parsed, "target_date", "") or target_date),
            "dayweather": str(getattr(parsed, "dayweather", "") or ""),
            "nightweather": str(getattr(parsed, "nightweather", "") or ""),
            "period": str(getattr(parsed, "period", "") or period or "all"),
            "reason": str(getattr(parsed, "reason", "") or ""),
            "confidence": float(getattr(parsed, "confidence", 0.0) or 0.0),
        }
    except Exception as exc:
        return {
            "matched": False,
            "is_rain_expected": False,
            "unknown": True,
            "reason": f"weather_rain_check_failed: {exc}",
            "target_date": target_date,
            "period": period or "all",
        }


def _should_run_step(step: PlanStep, step_outputs: dict[str, Any]) -> bool:
    action = str(step.action or "").strip().lower()
    # Weather probe steps are evidence-producing primitives and should not be
    # skipped by model-generated conditions.
    if action in {"tool.maps_weather", "logic.weather_rain_check"}:
        return True
    if not step.when:
        return True
    ref = step_outputs.get(step.when.step_id)
    field = str(step.when.field or "").strip() or "matched"
    field_value = _deep_get(ref, field)
    if field_value is None and isinstance(ref, dict):
        lowered = field.lower()
        if lowered in {"is_rain_expected", "will_rain", "rain_expected", "rainy"} and "matched" in ref:
            field_value = ref.get("matched")

    expected = _resolve_value(step.when.equals, step_outputs)
    if isinstance(expected, list):
        bool_items = [item for item in expected if isinstance(item, bool)]
        if bool_items:
            expected = bool_items[-1]
        elif len(expected) == 1:
            expected = expected[0]
    return field_value == expected


def _shorten_json(value: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _shorten_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _build_step_trace_text(step_trace: list[dict[str, Any]]) -> str:
    if not step_trace:
        return ""
    lines: list[str] = ["执行中间结果："]
    shown = 0
    for row in step_trace:
        if shown >= 6:
            break
        status = str(row.get("status") or "").strip().lower()
        action = str(row.get("action") or "").strip()
        if not action:
            continue
        if status == "success":
            line = f"{shown + 1}. [success] {action}"
            input_text = _shorten_text(row.get("input") or row.get("step_args") or "", 140)
            output_text = _shorten_text(row.get("output") or "", 180)
            if input_text:
                line += f" | 输入: {input_text}"
            if output_text:
                line += f" | 输出: {output_text}"
            lines.append(line)
            shown += 1
            continue
        if status in {"failed", "blocked", "skipped"}:
            reason = _shorten_text(row.get("error") or row.get("reason") or "", 160)
            line = f"{shown + 1}. [{status}] {action}"
            if reason:
                line += f" | 原因: {reason}"
            lines.append(line)
            shown += 1
    if shown == 0:
        return ""
    return "\n".join(lines)


def _build_plan_text(plan: ComplexTaskPlan) -> str:
    steps = list(plan.steps or [])
    if not steps:
        return ""
    lines: list[str] = ["任务规划："]
    for idx, step in enumerate(steps[:8], start=1):
        action = str(step.action or "").strip() or "unknown_action"
        arg_text = _shorten_text(_shorten_json(step.args, 220), 220)
        dep_text = ",".join([str(x) for x in step.depends_on if str(x).strip()])
        line = f"{idx}. {step.step_id} -> {action}"
        if dep_text:
            line += f" | 依赖: {dep_text}"
        if arg_text and arg_text != "{}":
            line += f" | 参数: {arg_text}"
        lines.append(line)
    return "\n".join(lines)


def _infer_pending_topic_from_plan(plan: ComplexTaskPlan, fallback: str = "general") -> str:
    topic = str(fallback or "").strip() or "general"
    for step in list(plan.steps or []):
        action = str(step.action or "").strip().lower()
        if action in {"tool.maps_weather", "logic.weather_rain_check"}:
            return "weather_condition_reminder"
    return topic


def _infer_anchor_target_date_from_plan(plan: ComplexTaskPlan) -> str:
    for step in list(plan.steps or []):
        action = str(step.action or "").strip().lower()
        args = step.args if isinstance(step.args, dict) else {}
        if action == "logic.weather_rain_check":
            target = _coerce_target_date(args.get("target_date"))
            if target:
                return target
        if action == "tool.schedule_insert":
            trigger = str(args.get("trigger_time") or "").strip()
            if " " in trigger:
                trigger = trigger.split(" ", 1)[0].strip()
            target = _coerce_target_date(trigger)
            if target:
                return target
    return ""


async def _execute_step(
    *,
    step: PlanStep,
    base_state: GraphState,
    runtime_tools: list[dict[str, Any]],
    step_outputs: dict[str, Any],
    tool_counters: dict[str, int],
    counter_lock: asyncio.Lock,
) -> dict[str, Any]:
    resolved_args = _resolve_value(step.args, step_outputs)
    action = step.action.strip()
    message = base_state["message"]

    if action.startswith("node."):
        node_name = action.split(".", 1)[1].strip().lower()
        node_fn = NODE_ACTIONS.get(node_name)
        if node_fn is None:
            raise RuntimeError(f"unsupported node action: {action}")
        input_text = str(resolved_args.get("input") or resolved_args.get("content") or "").strip()
        if not input_text:
            input_text = str(message.content or "").strip()
        image_urls: list[str] | None = None
        if isinstance(resolved_args.get("image_urls"), list):
            image_urls = [str(x) for x in resolved_args.get("image_urls") if str(x).strip()]
        sub_state: GraphState = dict(base_state)
        sub_state["message"] = _clone_message(message, content=input_text, image_urls=image_urls)
        result = await node_fn(sub_state)
        responses = [str(x) for x in (result.get("responses") or [])]
        node_extra = result.get("extra")
        return {
            "kind": "node",
            "node": node_name,
            "input": input_text,
            "responses": [str(x) for x in responses],
            "response_text": "\n".join(str(x) for x in responses),
            "extra": (dict(node_extra) if isinstance(node_extra, dict) else {}),
        }

    if action.startswith("tool."):
        tool_name = action.split(".", 1)[1].strip()
        if not tool_name:
            raise RuntimeError("missing tool name in action")

        settings = get_settings()
        async with counter_lock:
            if tool_counters["total"] >= settings.complex_task_tool_call_limit:
                raise RuntimeError("tool call limit reached")
            if tool_counters[f"action:{tool_name.lower()}"] >= settings.complex_task_tool_per_action_limit:
                raise RuntimeError(f"tool per-action call limit reached: {tool_name}")
            tool_counters["total"] += 1
            tool_counters[f"action:{tool_name.lower()}"] += 1

        args_for_tool = dict(resolved_args)
        tool_name_l = tool_name.strip().lower()
        if tool_name_l == "maps_weather":
            city = str(args_for_tool.get("city") or args_for_tool.get("adcode") or "").strip()
            if not city:
                raise RuntimeError("missing_required_arg: maps_weather.city")
            if not _arg_is_grounded_in_state(city, base_state):
                raise RuntimeError("ungrounded_arg: maps_weather.city")
        if tool_name_l == "schedule_insert":
            trigger_time = str(args_for_tool.get("trigger_time") or "").strip()
            content_text = str(args_for_tool.get("content") or "").strip()
            if not trigger_time:
                raise RuntimeError("missing_required_arg: schedule_insert.trigger_time")
            if not content_text:
                raise RuntimeError("missing_required_arg: schedule_insert.content")
        source_hint = str(args_for_tool.pop("_source", "") or "").strip().lower()
        source = source_hint or _resolve_tool_source(tool_name, runtime_tools)
        result = await execute_capability_with_usage(
            source=source,
            name=tool_name,
            args=args_for_tool,
            user_id=int(base_state.get("user_id") or 0),
            platform=str(message.platform or "unknown"),
            conversation_id=int(base_state.get("conversation_id") or 0),
        )
        if not result["ok"]:
            raise RuntimeError(str(result.get("error") or f"tool `{tool_name}` failed"))
        output_data = result.get("output_data")
        output_text = str(result.get("output") or "")
        if output_data is None:
            output_data = output_text
        return {
            "kind": "tool",
            "tool": tool_name,
            "source": source,
            "arguments": args_for_tool,
            "output": output_data,
            "output_text": output_text,
            "latency_ms": int(result.get("latency_ms") or 0),
        }

    if action == "logic.weather_rain_check":
        output = await _execute_logic_weather_rain_check(args=resolved_args, step_outputs=step_outputs)
        return {
            "kind": "logic",
            "logic": "weather_rain_check",
            "result": output,
            "matched": bool(output.get("matched") is True),
        }

    raise RuntimeError(f"unsupported action: {action}")


async def _execute_step_with_retry(
    *,
    step: PlanStep,
    base_state: GraphState,
    runtime_tools: list[dict[str, Any]],
    step_outputs: dict[str, Any],
    tool_counters: dict[str, int],
    counter_lock: asyncio.Lock,
) -> dict[str, Any]:
    attempts = max(1, int(step.retry.max_attempts))
    backoff_ms = max(0, int(step.retry.backoff_ms))
    timeout_sec = max(1.0, float(step.timeout_ms) / 1000.0)

    last_error = ""
    for idx in range(attempts):
        started = time.perf_counter()
        try:
            output = await asyncio.wait_for(
                _execute_step(
                    step=step,
                    base_state=base_state,
                    runtime_tools=runtime_tools,
                    step_outputs=step_outputs,
                    tool_counters=tool_counters,
                    counter_lock=counter_lock,
                ),
                timeout=timeout_sec,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "ok": True,
                "attempt": idx + 1,
                "latency_ms": latency_ms,
                "output": output,
            }
        except Exception as exc:
            last_error = str(exc)
            if idx + 1 < attempts and backoff_ms > 0:
                await asyncio.sleep(backoff_ms / 1000.0)
    return {"ok": False, "attempt": attempts, "latency_ms": 0, "error": last_error}


async def _summarize_complex_result(
    *,
    content: str,
    conversation_context: str,
    plan: ComplexTaskPlan,
    step_trace: list[dict[str, Any]],
    step_outputs: dict[str, Any],
) -> str:
    llm = get_llm(node_name="complex_task")
    system = SystemMessage(
        content=(
            "你是最终答复汇总器。\n"
            "只能使用已执行步骤结果作答，禁止编造执行成功。\n"
            "若关键步骤失败，必须明确说明失败点和下一步建议。\n"
            "若是条件任务，需说明条件是否满足。\n"
            "回答保持简洁、面向用户。"
        )
    )
    trace_payload = {
        "goal": plan.goal,
        "steps": step_trace,
        "outputs": {sid: _shorten_json(value, 1600) for sid, value in step_outputs.items()},
    }
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"执行轨迹:\n{json.dumps(trace_payload, ensure_ascii=False)}"
        )
    )
    response = await asyncio.wait_for(llm.ainvoke([system, human]), timeout=45)
    text = str(response.content or "").strip()
    if text:
        return text

    lines: list[str] = []
    for row in step_trace:
        if str(row.get("status")) != "success":
            continue
        output = step_outputs.get(str(row.get("step_id") or ""))
        if isinstance(output, dict) and output.get("response_text"):
            lines.append(str(output.get("response_text")))
    return "\n".join(lines).strip() or "任务执行完成。"


def _trace_is_completed(step_trace: list[dict[str, Any]]) -> bool:
    if not step_trace:
        return False
    failed = any(str(row.get("status") or "") in {"failed", "blocked"} for row in step_trace)
    if failed:
        return False
    return True


async def _resolve_complex_outcome(
    *,
    content: str,
    conversation_context: str,
    plan: ComplexTaskPlan,
    step_trace: list[dict[str, Any]],
    step_outputs: dict[str, Any],
    draft_response: str,
) -> ComplexTaskOutcomeExtraction:
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ComplexTaskOutcomeExtraction)
    system = SystemMessage(
        content=(
            "你是复杂任务收尾判定器。请仅返回 JSON 结构化字段：completed, final_response, followup_question, reason。\n"
            "输出必须是 json。\n"
            "判定原则：\n"
            "1) 必须依据执行轨迹判断任务是否已完成，禁止关键词匹配推断。\n"
            "2) 若任务完成，completed=true，填写 final_response，followup_question 置空。\n"
            "3) 若任务未完成，completed=false，填写 followup_question 指导下一步，final_response 可为空。\n"
            "4) 不得编造已执行成功的步骤。\n"
            "5) 若已完成部分子任务（如已完成工具查询），在 followup_question 前先简要告知已完成结果，再追问缺失参数。\n"
            "6) 若错误包含 ungrounded_arg: maps_weather.city，followup_question 必须询问地点；"
            "若错误包含 ungrounded_arg: schedule_insert.trigger_time，followup_question 必须询问具体时间。\n"
            "7) followup_question 需要包含一条可见的简短决策说明（不要输出内部推理细节），"
            "格式建议：“我已完成X，当前缺少Y，所以需要你补充Z。”"
            "只输出 JSON，不要输出解释文本。"
        )
    )
    payload = {
        "goal": plan.goal,
        "steps": step_trace,
        "outputs": {sid: _shorten_json(value, 1600) for sid, value in step_outputs.items()},
        "draft_response": draft_response,
    }
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"执行结果:\n{json.dumps(payload, ensure_ascii=False)}"
        )
    )
    try:
        parsed = await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=35)
        if isinstance(parsed, ComplexTaskOutcomeExtraction):
            return parsed
        if isinstance(parsed, dict):
            return ComplexTaskOutcomeExtraction.model_validate(parsed)
    except Exception:
        pass

    completed = _trace_is_completed(step_trace)
    if completed:
        return ComplexTaskOutcomeExtraction(completed=True, final_response=draft_response or "任务执行完成。", reason="fallback_trace_completed")
    return ComplexTaskOutcomeExtraction(
        completed=False,
        followup_question="任务还未完全完成，请根据上一步失败信息补充参数后重试。",
        reason="fallback_trace_incomplete",
    )


def _build_weather_condition_summary(
    *,
    plan: ComplexTaskPlan,
    step_trace: list[dict[str, Any]],
    step_outputs: dict[str, Any],
) -> str | None:
    weather_logic_step: PlanStep | None = None
    reminder_step: PlanStep | None = None
    for step in plan.steps:
        if step.action == "logic.weather_rain_check":
            weather_logic_step = step
        if step.action == "node.schedule_manager" and step.when is not None:
            reminder_step = step
    if weather_logic_step is None or reminder_step is None:
        return None

    weather_output = step_outputs.get(weather_logic_step.step_id)
    if not isinstance(weather_output, dict):
        return None
    result = weather_output.get("result")
    if not isinstance(result, dict):
        return None

    matched = bool(result.get("matched"))
    target_date = str(result.get("target_date") or "").strip()
    dayweather = str(result.get("dayweather") or "").strip()
    nightweather = str(result.get("nightweather") or "").strip()

    status_by_id = {str(row.get("step_id") or ""): str(row.get("status") or "") for row in step_trace}
    reminder_status = status_by_id.get(reminder_step.step_id, "")
    reminder_output = step_outputs.get(reminder_step.step_id)
    reminder_text = ""
    if isinstance(reminder_output, dict):
        reminder_text = str(reminder_output.get("response_text") or "").strip()

    if reminder_status == "success":
        return reminder_text or "条件满足，提醒已创建。"
    if reminder_status == "skipped":
        weather_desc = "、".join([part for part in [dayweather, nightweather] if part]) or "天气信息不足"
        return (
            f"已查询天气：{target_date or '目标日期'} {weather_desc}。"
            f"条件未满足（matched={str(matched).lower()}），本次未创建提醒。"
        )
    if reminder_status in {"failed", "blocked"}:
        return "天气已查询，但提醒创建步骤失败，请重试一次。"
    return None


def _collect_runtime_tools(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        if raw.get("enabled") is False:
            continue
        rows.append(
            {
                "name": str(raw.get("name") or "").strip(),
                "source": str(raw.get("source") or "").strip().lower(),
                "description": str(raw.get("description") or "").strip(),
                "enabled": bool(raw.get("enabled") is True),
            }
        )
    return rows


def _extract_agent_answer_and_trace(result: Any) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(result, dict):
        return "", []
    messages = result.get("messages") or []
    answer = ""
    traces: list[dict[str, str]] = []
    for msg in messages:
        msg_type = str(getattr(msg, "type", "") or "").strip().lower()
        if msg_type == "tool":
            name = str(getattr(msg, "name", "") or "tool").strip() or "tool"
            content = str(getattr(msg, "content", "") or "").strip()
            traces.append({"tool": name, "output": _shorten_text(content, 500)})
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                answer = content.strip()
            elif isinstance(content, list):
                chunks: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            chunks.append(text.strip())
                if chunks:
                    answer = "\n".join(chunks)
    return answer, traces


async def _plan_complex_decision(
    *,
    content: str,
    conversation_context: str,
    pending_context: dict[str, Any] | None,
) -> ComplexPlanDecision:
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ComplexPlanDecision)
    system = SystemMessage(
        content=(
            "你是复杂任务主规划器。只输出 JSON。\n"
            "输出必须是 json。\n"
            "字段: mode, reason, plan_summary, clarify_question, non_complex_node_action, "
            "followup_expected, followup_topic。\n"
            "mode 只能是 execute/clarify/handoff。\n"
            "判定原则:\n"
            "1) 需要多步骤编排、条件判断、外部工具证据+业务动作时 mode=execute。\n"
            "2) 单节点可直接处理时 mode=handoff，non_complex_node_action 只能是 "
            "node.chat_manager/node.ledger_manager/node.schedule_manager/node.skill_manager/node.help_center。\n"
            "   例如：'今天爬山120元' 属于单节点账单写入，必须 handoff 到 node.ledger_manager。\n"
            "   例如：'明天8点提醒我开会' 属于单节点提醒写入，必须 handoff 到 node.schedule_manager。\n"
            "3) 关键参数缺失且当前无法执行时 mode=clarify，clarify_question 只问缺失参数本身。\n"
            "4) pending_context.active=true 时，当前消息优先视为同任务补充输入并与上下文合并。\n"
            "5) 条件提醒（如天气好再提醒）通常 mode=execute。\n"
            "6) 对条件提醒，若地点/时间等关键槽位缺失导致当前不可执行，必须 mode=clarify，"
            "并设置 followup_expected=true。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"pending_context:\n{json.dumps(pending_context or {}, ensure_ascii=False)}"
        )
    )
    timeout_sec = max(2, int(get_settings().complex_task_plan_timeout_sec or 8))
    return await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=timeout_sec)


async def _run_complex_subagent(
    *,
    state: GraphState,
    content: str,
    conversation_context: str,
    plan_summary: str,
    runtime_tools: list[dict[str, Any]],
) -> tuple[str, list[dict[str, str]]]:
    message = state["message"]
    enabled_tool_names = {
        str(item.get("name") or "").strip().lower()
        for item in runtime_tools
        if str(item.get("name") or "").strip()
    }
    tools = build_langchain_tools(enabled_tool_names=enabled_tool_names)
    prompt = (
        "你是复杂任务执行代理。根据用户请求自主选择工具并完成任务。\n"
        "要求：\n"
        "1) 优先依据上下文补全参数，不要重复追问已给出的信息。\n"
        "2) 严禁猜测或默认城市；用户未明确地点时，必须先问地点，且不得调用天气/提醒创建工具。\n"
        "3) 用户只补充地点后，应直接查询该地点天气并继续任务，不要重复问地点。\n"
        "4) 涉及相对时间（如明天/后天）时，可先调用 now_time 再换算绝对时间。\n"
        "5) 若是条件任务（例如天气好再提醒），需先查证据再决定是否创建提醒。\n"
        "   对“天气好”条件，默认按严格标准：无降水且白天天气不为阴/雨/雪，才允许创建提醒。\n"
        "6) 不要询问授权式问题（如“是否需要我查询/是否需要创建”），可执行就直接执行。\n"
        "7) 若缺少提醒时间，可按语境自行决定合理时间并继续，不必要求确认。\n"
        "8) 缺参数时只问一个最关键参数。\n"
        "9) 最终输出自然语言，不要输出 JSON 或内部推理。\n\n"
        f"规划摘要:\n{plan_summary}\n\n"
        f"会话上下文:\n{conversation_context}"
    )
    ctx = AgentToolContext(
        user_id=int(state.get("user_id") or 0) or None,
        platform=str(message.platform or "unknown"),
        conversation_id=int(state.get("conversation_id") or 0) or None,
    )
    agent = create_agent(
        model=get_llm(node_name="complex_task_agent"),
        tools=tools,
        system_prompt=prompt,
        context_schema=AgentToolContext,
        name=f"complex_task_agent_{int(state.get('user_id') or 0)}_{int(state.get('conversation_id') or 0)}",
    )
    result = await agent.ainvoke(
        {
            "messages": [{"role": "user", "content": content}]
        },
        context=ctx,
        config={"recursion_limit": max(4, int(get_settings().complex_task_agent_recursion_limit or 8))},
    )
    return _extract_agent_answer_and_trace(result)


async def _infer_followup_need(
    *,
    user_message: str,
    answer_text: str,
    conversation_context: str,
) -> ComplexFollowupExtraction:
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ComplexFollowupExtraction)
    system = SystemMessage(
        content=(
            "你是复杂任务后续判定器。只输出 JSON。\n"
            "输出必须是 json。\n"
            "字段: followup_expected, followup_topic, reason。\n"
            "当助手回复仍在向用户索取关键参数、任务尚未完成时 followup_expected=true；否则 false。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{user_message}\n\n"
            f"助手回复:\n{answer_text}\n\n"
            f"会话上下文:\n{conversation_context}"
        )
    )
    try:
        timeout_sec = max(2, int(get_settings().complex_task_followup_timeout_sec or 6))
        return await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=timeout_sec)
    except Exception as exc:
        reason = str(exc or "").strip()
        if len(reason) > 160:
            reason = reason[:160]
        return ComplexFollowupExtraction(
            followup_expected=False,
            followup_topic="",
            reason=f"followup_infer_failed:{reason}" if reason else "followup_infer_failed",
        )


async def _audit_planning_json(
    *,
    state: GraphState,
    planning: ComplexTaskPlanExtraction,
) -> None:
    message = state.get("message")
    if message is None:
        return
    user_id = int(state.get("user_id") or 0) or None
    conversation_id = int(state.get("conversation_id") or 0) or None
    parsed_plan = _coerce_plan(planning.plan)
    detail = {
        "conversation_id": conversation_id,
        "need_complex": bool(planning.need_complex),
        "reason": str(planning.reason or ""),
        "plan": parsed_plan.model_dump() if parsed_plan is not None else planning.plan,
        "followup_expected": bool(planning.followup_expected),
        "followup_topic": str(planning.followup_topic or ""),
        "missing_slots": list(planning.missing_slots or []),
    }
    try:
        async with AsyncSessionLocal() as session:
            await log_event(
                session=session,
                action="complex_plan_generated",
                platform=str(message.platform or "unknown"),
                user_id=user_id,
                detail=detail,
            )
    except Exception:
        return


async def _audit_complex_execution_trace(
    *,
    state: GraphState,
    plan_summary: str,
    trace_rows: list[dict[str, str]],
    answer_text: str,
) -> None:
    if not plan_summary and not trace_rows:
        return
    message = state.get("message")
    if message is None:
        return
    user_id = int(state.get("user_id") or 0) or None
    conversation_id = int(state.get("conversation_id") or 0) or None
    detail = {
        "conversation_id": conversation_id,
        "plan_summary": plan_summary,
        "trace": [
            {"tool": str(item.get("tool") or ""), "output": str(item.get("output") or "")}
            for item in trace_rows[:8]
        ],
        "answer_preview": _shorten_text(answer_text, 300),
    }
    try:
        async with AsyncSessionLocal() as session:
            await log_event(
                session=session,
                action="complex_task_execution_trace",
                platform=str(message.platform or "unknown"),
                user_id=user_id,
                detail=detail,
            )
    except Exception:
        logger.exception(
            "complex_task execution trace audit failed: user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )


async def complex_task_node(state: GraphState) -> GraphState:
    message = state["message"]
    content = (message.content or "").strip()
    context_text = render_conversation_context(state)
    session = get_session()
    user = await session.get(User, state["user_id"])
    if user:
        profile_reply = await _try_handle_profile_intent(
            session=session,
            user=user,
            content=content,
            context_text=context_text,
        )
        if profile_reply:
            extra_payload = dict(state.get("extra") or {})
            extra_payload["complex_task_pending"] = {
                "active": False,
                "reason": "",
                "topic": "",
                "anchor_request": "",
                "anchor_target_date": "",
            }
            return {**state, "responses": [profile_reply], "extra": extra_payload}

    extra_payload = dict(state.get("extra") or {})
    pending_payload = extra_payload.get("complex_task_pending")
    hint_payload = extra_payload.get("complex_hint")
    if isinstance(hint_payload, dict):
        direct_question = await _generate_clarification_from_hint(
            content=content,
            conversation_context=context_text,
            hint_payload=hint_payload,
        )
        if direct_question:
            extra_payload["complex_task"] = {
                "reason": str(hint_payload.get("reason") or ""),
                "fallback_mode": "clarify",
                "fallback_node_action": "",
            }
            return {**state, "responses": [direct_question], "extra": extra_payload}

    decision_task: asyncio.Task[ComplexPlanDecision] | None = None
    try:
        decision_task = asyncio.create_task(
            _plan_complex_decision(
                content=content,
                conversation_context=context_text,
                pending_context=(dict(pending_payload) if isinstance(pending_payload, dict) else None),
            )
        )
    except Exception:
        decision_task = None

    try:
        runtime_tools = _collect_runtime_tools(await list_runtime_tool_metas())
    except Exception:
        runtime_tools = []

    plan_summary = ""
    decision: ComplexPlanDecision | None = None
    if decision_task is not None:
        try:
            decision = await asyncio.wait_for(
                asyncio.shield(decision_task),
                timeout=max(1, int(get_settings().complex_task_plan_timeout_sec or 5)),
            )
        except Exception:
            if not decision_task.done():
                decision_task.cancel()
            decision = None

    if decision is not None:
        mode = str(decision.mode or "").strip().lower()
        if mode not in {"execute", "clarify", "handoff"}:
            mode = "execute"
        if mode == "clarify":
            question = str(decision.clarify_question or "").strip()
            if not question:
                question = await _generate_clarification(
                    content=content,
                    conversation_context=context_text,
                    reason=f"clarify_without_question: {str(decision.reason or '')}",
                )
            existing_anchor = ""
            existing_target_date = ""
            if isinstance(pending_payload, dict):
                existing_anchor = str(pending_payload.get("anchor_request") or "").strip()
                existing_target_date = str(pending_payload.get("anchor_target_date") or "").strip()
            extra = dict(state.get("extra") or {})
            extra["complex_task_pending"] = {
                "active": True,
                "reason": str(decision.reason or ""),
                "topic": (str(decision.followup_topic or "").strip() or "general"),
                "anchor_request": (existing_anchor or content),
                "anchor_target_date": existing_target_date,
            }
            extra["complex_task"] = {
                "reason": str(decision.reason or ""),
                "fallback_mode": "clarify",
                "fallback_node_action": "",
            }
            return {**state, "responses": [question], "extra": extra}
        if mode == "handoff":
            node_action = str(decision.non_complex_node_action or "").strip() or "node.chat_manager"
            responses, handoff_extra = await _execute_node_handoff(
                node_action=node_action,
                base_state=state,
                content=content,
            )
            if not responses:
                question = await _generate_clarification(
                    content=content,
                    conversation_context=context_text,
                    reason=f"handoff_failed: {node_action}",
                )
                responses = [question]
            extra = dict(state.get("extra") or {})
            if handoff_extra:
                extra.update(handoff_extra)
            extra["complex_task_pending"] = {
                "active": False,
                "reason": "",
                "topic": "",
                "anchor_request": "",
                "anchor_target_date": "",
            }
            extra["complex_task"] = {
                "reason": str(decision.reason or ""),
                "fallback_mode": "handoff",
                "fallback_node_action": node_action,
            }
            return {**state, "responses": responses, "extra": extra}
        plan_summary = str(decision.plan_summary or "").strip()

    answer_text, trace_rows = await _run_complex_subagent(
        state=state,
        content=content,
        conversation_context=context_text,
        plan_summary=plan_summary,
        runtime_tools=runtime_tools,
    )
    if not answer_text:
        answer_text = await _generate_clarification(
            content=content,
            conversation_context=context_text,
            reason="complex_subagent_empty_answer",
        )
    followup = ComplexFollowupExtraction(
        followup_expected=(
            getattr(decision, "followup_expected", False)
            if decision is not None
            else False
        ),
        followup_topic=(
            str(getattr(decision, "followup_topic", "") or "").strip()
            if decision is not None
            else ""
        ),
        reason=(
            str(getattr(decision, "reason", "") or "").strip()
            if decision is not None
            else ""
        ),
    )
    await _audit_complex_execution_trace(
        state=state,
        plan_summary=plan_summary,
        trace_rows=trace_rows,
        answer_text=answer_text,
    )
    response_text = str(answer_text or "").strip()
    if not response_text:
        response_text = "任务执行完成。"
    extra = dict(state.get("extra") or {})
    existing_anchor = ""
    existing_target_date = ""
    if isinstance(pending_payload, dict):
        existing_anchor = str(pending_payload.get("anchor_request") or "").strip()
        existing_target_date = str(pending_payload.get("anchor_target_date") or "").strip()
    decision_followup_expected = _coerce_bool(
        getattr(decision, "followup_expected", False) if decision is not None else False,
        default=False,
    )
    decision_followup_topic = (
        str(getattr(decision, "followup_topic", "") or "").strip()
        if decision is not None
        else ""
    )
    followup_reason = str(followup.reason or "")
    need_followup = _coerce_bool(
        followup.followup_expected,
        default=decision_followup_expected,
    )
    pending_topic = (
        str(followup.followup_topic or "").strip()
        or (decision_followup_topic if need_followup else "")
        or ("general" if need_followup else "")
    )
    extra["complex_task_pending"] = {
        "active": need_followup,
        "reason": followup_reason,
        "topic": pending_topic,
        "anchor_request": (existing_anchor or content) if need_followup else "",
        "anchor_target_date": existing_target_date if need_followup else "",
    }
    extra["complex_task"] = {
        "reason": str(followup.reason or ""),
        "completed": (not need_followup),
        "fallback_mode": "execute",
        "fallback_node_action": "",
        "trace": [
            {"tool": str(item.get("tool") or ""), "output": str(item.get("output") or "")}
            for item in trace_rows[:8]
        ],
    }
    return {**state, "responses": [response_text], "extra": extra}
