from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.graph.context import render_conversation_context
from app.graph.nodes.chat_manager import chat_manager_node
from app.graph.nodes.help_center import help_center_node
from app.graph.nodes.ledger_manager import ledger_manager_node
from app.graph.nodes.schedule_manager import schedule_manager_node
from app.graph.nodes.skill_manager import skill_manager_node
from app.graph.state import GraphState
from app.schemas.unified import UnifiedMessage
from app.services.audit import log_event
from app.services.llm import get_llm
from app.services.tool_executor import execute_capability_with_usage
from app.services.tool_registry import list_runtime_tool_metas


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
    final_response_style: str = Field(default="concise")


class ComplexTaskPlanExtraction(BaseModel):
    need_complex: bool = Field(default=True)
    reason: str = Field(default="")
    plan: ComplexTaskPlan | None = None


class NonComplexDecision(BaseModel):
    mode: str = Field(default="clarify")
    node_action: str = Field(default="node.chat_manager")
    clarify_question: str = Field(default="")
    reason: str = Field(default="")


class ClarificationReply(BaseModel):
    question: str = Field(default="")


NODE_ACTIONS: dict[str, Callable[[GraphState], Awaitable[GraphState]]] = {
    "ledger_manager": ledger_manager_node,
    "schedule_manager": schedule_manager_node,
    "chat_manager": chat_manager_node,
    "skill_manager": skill_manager_node,
    "help_center": help_center_node,
}

PRECIPITATION_TOKENS = (
    "雨",
    "雷阵雨",
    "阵雨",
    "中雨",
    "大雨",
    "暴雨",
    "雪",
    "雨夹雪",
    "shower",
    "rain",
    "snow",
    "sleet",
    "thunderstorm",
)


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
        rows.append(
            {
                "action": f"tool.{name}",
                "description": f"调用运行时工具 `{name}`（source={source}）。",
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
) -> ComplexTaskPlanExtraction:
    settings = get_settings()
    tz = settings.timezone
    now_local = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
    action_catalog = _build_action_catalog(runtime_tools)

    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(ComplexTaskPlanExtraction)
    system = SystemMessage(
        content=(
            "你是复杂任务规划器。请仅返回 schema 定义的结构化字段。\n"
            "判断该请求是否需要复杂 DAG 执行（need_complex=true）。\n"
            "当任务具备以下任一特征时使用 need_complex=true：多目标、跨域、依赖链、条件执行。\n"
            "当 need_complex=true 时，必须给出 plan。\n"
            "规划规则：\n"
            "1) 每个步骤的 step_id 必须唯一。\n"
            "2) 只能使用给定动作目录中的 action。\n"
            "3) 用 depends_on 表达执行顺序。\n"
            "4) 条件分支用 when={step_id, field, equals}。\n"
            "5) args 中可用占位符传递结果，格式为 $<step_id> 或 $<step_id>.<field_path>。\n"
            "6) 总步骤数不超过 8。\n"
            "7) 优先使用工具证据，再执行业务动作，最后由引擎汇总。\n"
            "8) 严禁编造工具输出。\n"
            "9) 对需要 city/adcode 的天气工具，仅当当前用户消息或最近上下文明确给出时才填写；禁止默认城市。\n"
            "若城市未知，不要调用工具，应先澄清。\n"
            "10) 条件型计划必须先用 logic 步骤判断条件；条件满足后，下游业务动作输入必须是无条件描述，"
            "不要包含“如果/下雨就”之类条件措辞。\n"
            "11) when.field 必须引用真实输出字段（天气降水判断使用 matched）。\n"
            "12) when.equals 必须是标量字面量（布尔/字符串/数字），不能是数组或对象。\n"
            f"用户时区：{tz}。当前本地时间：{now_local}。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"动作目录:\n{json.dumps(action_catalog, ensure_ascii=False)}"
        )
    )
    return await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=45)


async def _decide_non_complex_handling(
    *,
    content: str,
    conversation_context: str,
    planner_reason: str,
) -> NonComplexDecision:
    llm = get_llm(node_name="complex_task")
    runnable = llm.with_structured_output(NonComplexDecision)
    system = SystemMessage(
        content=(
            "你是智能体图的兜底决策器。\n"
            "请仅返回 schema 定义的结构化字段。\n"
            "只能二选一：\n"
            "1) handoff：路由到单个节点动作；\n"
            "2) clarify：提出一个简短澄清问题。\n"
            "允许的 node_action: node.chat_manager, node.ledger_manager, node.schedule_manager, node.skill_manager, node.help_center。\n"
            "当关键参数缺失或意图仍不明确时使用 clarify。\n"
            "在没有工具证据时，禁止输出最终事实结论。"
        )
    )
    human = HumanMessage(
        content=(
            f"用户消息:\n{content}\n\n"
            f"会话上下文:\n{conversation_context}\n\n"
            f"规划器原因:\n{planner_reason}"
        )
    )
    try:
        return await asyncio.wait_for(runnable.ainvoke([system, human]), timeout=25)
    except Exception:
        return NonComplexDecision(mode="clarify", node_action="node.chat_manager", clarify_question="")


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
            "不要暴露内部错误，不要编造事实。"
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


def _normalize_weather_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "date": str(row.get("date") or "").strip(),
        "dayweather": str(row.get("dayweather") or "").strip(),
        "nightweather": str(row.get("nightweather") or "").strip(),
    }


def _weather_has_precipitation(text: str) -> bool:
    lower = (text or "").strip().lower()
    return any(token.lower() in lower for token in PRECIPITATION_TOKENS)


def _select_forecast_row(forecasts: list[dict[str, Any]], target_date: str) -> dict[str, Any] | None:
    if not forecasts:
        return None
    if target_date:
        for row in forecasts:
            if str(row.get("date") or "").strip() == target_date:
                return row
    return forecasts[0]


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

    weather_text = str(weather_output or "")
    weather_obj = _parse_json_object(weather_text)
    forecasts_raw = weather_obj.get("forecasts") if isinstance(weather_obj, dict) else None
    forecasts: list[dict[str, Any]] = []
    if isinstance(forecasts_raw, list):
        forecasts = [row for row in forecasts_raw if isinstance(row, dict)]

    target_date = _coerce_target_date(args.get("target_date"))
    if not target_date:
        tz = ZoneInfo(get_settings().timezone)
        tomorrow = (datetime.now(tz).date() + timedelta(days=1)).isoformat()
        target_date = tomorrow

    row = _select_forecast_row(forecasts, target_date)
    if not row:
        return {
            "matched": False,
            "unknown": True,
            "reason": "missing_weather_forecast",
            "target_date": target_date,
        }

    normalized = _normalize_weather_row(row)
    period = str(args.get("period") or "").strip().lower()
    dayweather = normalized["dayweather"]
    nightweather = normalized["nightweather"]

    if period in {"afternoon", "day", "daytime"}:
        rainy = _weather_has_precipitation(dayweather)
    elif period in {"night", "evening", "nighttime"}:
        rainy = _weather_has_precipitation(nightweather)
    else:
        rainy = _weather_has_precipitation(dayweather) or _weather_has_precipitation(nightweather)

    return {
        "matched": bool(rainy),
        "is_rain_expected": bool(rainy),
        "unknown": False,
        "target_date": normalized["date"] or target_date,
        "dayweather": dayweather,
        "nightweather": nightweather,
        "period": period or "all",
    }


def _should_run_step(step: PlanStep, step_outputs: dict[str, Any]) -> bool:
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
        responses = result.get("responses") or []
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
        if tool_name.strip().lower() == "maps_weather":
            city = str(args_for_tool.get("city") or args_for_tool.get("adcode") or "").strip()
            if not city:
                raise RuntimeError("missing required arg: city")
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


def _render_planning_json(planning: ComplexTaskPlanExtraction) -> str:
    payload = planning.model_dump()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) > 12000:
        text = text[:12000] + "\n... (truncated)"
    return f"```json\n{text}\n```"


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
    detail = {
        "conversation_id": conversation_id,
        "need_complex": bool(planning.need_complex),
        "reason": str(planning.reason or ""),
        "plan": planning.plan.model_dump() if planning.plan is not None else None,
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


async def complex_task_node(state: GraphState) -> GraphState:
    message = state["message"]
    content = (message.content or "").strip()
    context_text = render_conversation_context(state)

    try:
        runtime_tools = _collect_runtime_tools(await list_runtime_tool_metas())
    except Exception:
        runtime_tools = []

    try:
        planning = await _plan_complex_task(
            content=content,
            conversation_context=context_text,
            runtime_tools=runtime_tools,
        )
    except ValidationError as exc:
        question = await _generate_clarification(
            content=content,
            conversation_context=context_text,
            reason=f"planner_validation_error: {exc.errors()[0].get('msg', 'unknown')}",
        )
        return {
            **state,
            "responses": [question],
        }
    except Exception as exc:
        question = await _generate_clarification(
            content=content,
            conversation_context=context_text,
            reason=f"planner_runtime_error: {exc}",
        )
        return {**state, "responses": [question]}

    plan_json_reply = _render_planning_json(planning)
    await _audit_planning_json(state=state, planning=planning)

    if not bool(planning.need_complex):
        fallback = await _decide_non_complex_handling(
            content=content,
            conversation_context=context_text,
            planner_reason=str(planning.reason or ""),
        )
        responses: list[str] = []
        handoff_extra: dict[str, Any] = {}
        if str(fallback.mode or "").strip().lower() == "handoff":
            responses, handoff_extra = await _execute_node_handoff(
                node_action=str(fallback.node_action or ""),
                base_state=state,
                content=content,
            )
        if not responses:
            question = str(fallback.clarify_question or "").strip()
            if not question:
                question = await _generate_clarification(
                    content=content,
                    conversation_context=context_text,
                    reason=f"need_complex_false: {str(planning.reason or '')}",
                )
            responses = [question]
        extra = dict(state.get("extra") or {})
        if handoff_extra:
            extra.update(handoff_extra)
        extra["complex_task"] = {
            "reason": str(planning.reason or ""),
            "fallback_mode": str(fallback.mode or ""),
            "fallback_node_action": str(fallback.node_action or ""),
        }
        return {**state, "responses": [plan_json_reply, *responses], "extra": extra}

    plan = planning.plan
    if plan is None:
        question = await _generate_clarification(
            content=content,
            conversation_context=context_text,
            reason="planner_returned_no_plan",
        )
        return {
            **state,
            "responses": [plan_json_reply, question],
        }

    is_valid, reason = _validate_plan(plan)
    if not is_valid:
        question = await _generate_clarification(
            content=content,
            conversation_context=context_text,
            reason=f"invalid_plan: {reason}",
        )
        return {
            **state,
            "responses": [plan_json_reply, question],
            "extra": {
                **dict(state.get("extra") or {}),
                "complex_task": {
                    "reason": str(planning.reason or ""),
                    "plan": plan.model_dump(),
                    "validation_error": reason,
                },
            },
        }

    settings = get_settings()
    step_map: dict[str, PlanStep] = {step.step_id: step for step in plan.steps}
    pending: set[str] = set(step_map.keys())
    statuses: dict[str, str] = {sid: "pending" for sid in step_map}
    step_outputs: dict[str, Any] = {}
    step_trace: list[dict[str, Any]] = []
    tool_counters: dict[str, int] = defaultdict(int)
    counter_lock = asyncio.Lock()

    max_parallel = max(1, int(settings.complex_task_max_parallel))
    wait_cycles = 0
    dependency_wait_cycles = max(0, int(settings.complex_task_dependency_wait_cycles))
    dependency_wait_ms = max(0, int(settings.complex_task_dependency_wait_ms))

    async def _run_one(step: PlanStep) -> tuple[str, dict[str, Any]]:
        result = await _execute_step_with_retry(
            step=step,
            base_state=state,
            runtime_tools=runtime_tools,
            step_outputs=step_outputs,
            tool_counters=tool_counters,
            counter_lock=counter_lock,
        )
        return step.step_id, result

    while pending:
        ready_ids: list[str] = []
        for sid in list(pending):
            step = step_map[sid]
            dep_statuses = [statuses.get(dep, "pending") for dep in step.depends_on]
            if any(dep_status in {"failed", "blocked"} for dep_status in dep_statuses):
                statuses[sid] = "blocked"
                pending.remove(sid)
                step_trace.append(
                    {
                        "step_id": sid,
                        "action": step.action,
                        "status": "blocked",
                        "reason": "dependency_failed",
                    }
                )
                continue
            if not all(dep_status in {"success", "skipped"} for dep_status in dep_statuses):
                continue
            if not _should_run_step(step, step_outputs):
                statuses[sid] = "skipped"
                pending.remove(sid)
                step_trace.append(
                    {
                        "step_id": sid,
                        "action": step.action,
                        "status": "skipped",
                        "reason": "condition_not_met",
                    }
                )
                continue
            ready_ids.append(sid)

        if not ready_ids:
            wait_cycles += 1
            if wait_cycles > dependency_wait_cycles:
                for sid in list(pending):
                    step = step_map[sid]
                    statuses[sid] = "failed"
                    step_trace.append(
                        {
                            "step_id": sid,
                            "action": step.action,
                            "status": "failed",
                            "reason": "dependency_wait_exceeded",
                        }
                    )
                    pending.remove(sid)
                break
            if dependency_wait_ms > 0:
                await asyncio.sleep(dependency_wait_ms / 1000.0)
            continue

        wait_cycles = 0
        batch_steps = [step_map[sid] for sid in ready_ids]
        for sid in ready_ids:
            statuses[sid] = "running"
            pending.remove(sid)

        semaphore = asyncio.Semaphore(max_parallel)

        async def _run_guarded(step: PlanStep) -> tuple[str, dict[str, Any]]:
            async with semaphore:
                return await _run_one(step)

        batch_results = await asyncio.gather(*[_run_guarded(step) for step in batch_steps], return_exceptions=True)
        for step, item in zip(batch_steps, batch_results):
            sid = step.step_id
            if isinstance(item, Exception):
                statuses[sid] = "failed"
                step_trace.append(
                    {
                        "step_id": sid,
                        "action": step.action,
                        "status": "failed",
                        "attempt": 1,
                        "error": str(item),
                    }
                )
                continue

            _, result = item
            if bool(result.get("ok")):
                statuses[sid] = "success"
                output = result.get("output")
                step_outputs[sid] = output
                step_trace.append(
                    {
                        "step_id": sid,
                        "action": step.action,
                        "status": "success",
                        "attempt": int(result.get("attempt") or 1),
                        "latency_ms": int(result.get("latency_ms") or 0),
                    }
                )
            else:
                statuses[sid] = "failed"
                step_trace.append(
                    {
                        "step_id": sid,
                        "action": step.action,
                        "status": "failed",
                        "attempt": int(result.get("attempt") or 1),
                        "error": str(result.get("error") or ""),
                    }
                )

    summary = await _summarize_complex_result(
        content=content,
        conversation_context=context_text,
        plan=plan,
        step_trace=step_trace,
        step_outputs=step_outputs,
    )

    extra = dict(state.get("extra") or {})
    # Preserve extra state produced by delegated node steps (e.g. last query result sets).
    for row in step_trace:
        if str(row.get("status") or "") != "success":
            continue
        step_id = str(row.get("step_id") or "")
        output = step_outputs.get(step_id)
        if isinstance(output, dict):
            node_extra = output.get("extra")
            if isinstance(node_extra, dict):
                extra.update(node_extra)
    extra["complex_task"] = {
        "reason": str(planning.reason or ""),
        "plan": plan.model_dump(),
        "trace": step_trace,
        "tool_calls_total": int(tool_counters.get("total", 0)),
    }
    return {**state, "responses": [plan_json_reply, summary], "extra": extra}
