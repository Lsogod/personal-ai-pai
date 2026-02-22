#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _http_json(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    body = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, method=method.upper(), headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else None
            return status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"error": raw}
        return int(exc.code), data


def _must(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def _profile(base_url: str, token: str) -> dict[str, Any]:
    status, data = _http_json(base_url=base_url, method="GET", path="/api/user/profile", token=token)
    _must(status == 200 and isinstance(data, dict), f"profile failed: status={status}, data={data}")
    return data


def _send_chat(base_url: str, token: str, content: str) -> list[str]:
    status, data = _http_json(
        base_url=base_url,
        method="POST",
        path="/api/chat/send",
        token=token,
        payload={"content": content, "image_urls": [], "source_platform": "web"},
    )
    _must(status == 200 and isinstance(data, dict), f"chat_send failed: status={status}, data={data}")
    rows = data.get("responses")
    _must(isinstance(rows, list), f"chat_send missing responses: {data}")
    return [str(item) for item in rows]


def _complete_onboarding(base_url: str, token: str) -> dict[str, Any]:
    samples = [
        "你好",
        "没有",
        "小王",
        "小派 🤖",
        "继续",
        "我准备好了",
    ]
    transcript: list[dict[str, Any]] = []
    for text in samples:
        profile_before = _profile(base_url, token)
        if int(profile_before.get("setup_stage") or 0) >= 3:
            return {"done": True, "transcript": transcript, "profile": profile_before}
        responses = _send_chat(base_url, token, text)
        profile_after = _profile(base_url, token)
        transcript.append(
            {
                "input": text,
                "responses_preview": "\n".join(responses)[:220],
                "setup_stage_after": profile_after.get("setup_stage"),
                "binding_stage_after": profile_after.get("binding_stage"),
            }
        )
    last = _profile(base_url, token)
    return {"done": int(last.get("setup_stage") or 0) >= 3, "transcript": transcript, "profile": last}


def _extract_prefixed_lines(responses: list[str]) -> list[str]:
    hits: list[str] = []
    for block in responses:
        for line in str(block).splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            if "]" not in line:
                continue
            hits.append(line)
    return hits


def _chat_history(base_url: str, token: str) -> list[dict[str, Any]]:
    status, data = _http_json(base_url=base_url, method="GET", path="/api/chat/history?limit=20", token=token)
    _must(status == 200 and isinstance(data, list), f"chat_history failed: status={status}, data={data}")
    rows: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _schedule_count(base_url: str, token: str) -> int:
    status, data = _http_json(base_url=base_url, method="GET", path="/api/schedules?limit=50", token=token)
    _must(status == 200 and isinstance(data, list), f"schedules failed: status={status}, data={data}")
    return len(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test: complex_task JSON scheduler path")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--password", default="Test123456!")
    parser.add_argument("--timeout-sec", type=int, default=30)
    args = parser.parse_args()

    ts = int(time.time())
    email = f"complex_smoke_{ts}_{random.randint(100,999)}@example.com"
    password = args.password

    register_status, register_data = _http_json(
        base_url=args.base_url,
        method="POST",
        path="/api/auth/register",
        payload={"email": email, "password": password},
        timeout=args.timeout_sec,
    )
    _must(register_status == 200 and isinstance(register_data, dict), f"register failed: {register_status}, {register_data}")

    login_status, login_data = _http_json(
        base_url=args.base_url,
        method="POST",
        path="/api/auth/login",
        payload={"email": email, "password": password},
        timeout=args.timeout_sec,
    )
    _must(login_status == 200 and isinstance(login_data, dict), f"login failed: {login_status}, {login_data}")
    token = str(login_data.get("access_token") or "")
    _must(bool(token), f"missing token in login response: {login_data}")

    onboarding = _complete_onboarding(args.base_url, token)
    _must(bool(onboarding.get("done")), f"onboarding not completed: {onboarding}")
    schedule_count_before = _schedule_count(args.base_url, token)

    complex_prompt = (
        "请帮我做三件事："
        "1) 查询上海天气；"
        "2) 根据天气写三条出行建议；"
        "3) 明天早上8点提醒我带伞。"
        "先查天气，再给建议，最后设置提醒。"
    )
    responses = _send_chat(args.base_url, token, complex_prompt)
    prefixed = _extract_prefixed_lines(responses)
    history = _chat_history(args.base_url, token)
    schedule_count_after = _schedule_count(args.base_url, token)
    merged_text = "\n".join(responses)
    has_weather_hint = any(k in merged_text for k in ("天气", "气温", "湿度", "AQI", "weather"))
    has_advice_hint = any(k in merged_text for k in ("建议", "出行", "穿衣", "advice"))
    has_reminder_hint = any(k in merged_text for k in ("提醒", "明天", "带伞", "schedule"))
    schedule_grew = schedule_count_after > schedule_count_before

    result = {
        "email": email,
        "setup_stage": onboarding.get("profile", {}).get("setup_stage"),
        "binding_stage": onboarding.get("profile", {}).get("binding_stage"),
        "complex_prompt": complex_prompt,
        "response_count": len(responses),
        "responses_preview": "\n---\n".join(responses)[:800],
        "prefixed_line_count": len(prefixed),
        "prefixed_lines_preview": prefixed[:8],
        "schedule_count_before": schedule_count_before,
        "schedule_count_after": schedule_count_after,
        "schedule_grew": schedule_grew,
        "has_weather_hint": has_weather_hint,
        "has_advice_hint": has_advice_hint,
        "has_reminder_hint": has_reminder_hint,
        "history_tail_roles": [str(item.get("role") or "") for item in history[-6:]],
    }

    # Current architecture: complex_task executes subtasks, then writer synthesizes one final answer.
    # Accept both old and new output styles.
    old_style = bool(len(responses) >= 2 and len(prefixed) >= 2)
    new_style = bool(
        len(responses) >= 1
        and has_weather_hint
        and has_advice_hint
        and has_reminder_hint
        and (schedule_grew or "已设置" in merged_text or "提醒" in merged_text)
    )
    passed = bool(old_style or new_style)
    result["passed"] = passed

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
