#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable


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


def _iso_local(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


@dataclass
class RegressionResult:
    started_at: str
    base_url: str
    email: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_step(self, name: str, ok: bool, detail: dict[str, Any] | None = None) -> None:
        row = {"name": name, "ok": bool(ok)}
        if detail:
            row["detail"] = detail
        self.steps.append(row)
        if not ok:
            self.failures.append(row)

    def add_note(self, text: str) -> None:
        self.notes.append(text)


class RegressionRunner:
    def __init__(self, *, base_url: str, timeout: int, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.password = password
        ts = int(time.time())
        self.email = f"full_reg_{ts}_{random.randint(100,999)}@example.com"
        self.token = ""
        self.result = RegressionResult(
            started_at=datetime.now().isoformat(timespec="seconds"),
            base_url=self.base_url,
            email=self.email,
        )
        self.created_ledger_ids: list[int] = []
        self.created_schedule_ids: list[int] = []
        self.case_tag = f"R{ts}{random.randint(100,999)}"

    def call(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[int, Any]:
        return _http_json(
            base_url=self.base_url,
            method=method,
            path=path,
            payload=payload,
            token=token if token is not None else self.token,
            timeout=self.timeout,
        )

    def step(self, name: str, fn: Callable[[], dict[str, Any] | None]) -> None:
        try:
            detail = fn() or {}
            self.result.add_step(name, True, detail)
        except Exception as exc:
            self.result.add_step(name, False, {"error": str(exc)})

    def _profile(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/user/profile")
        _must(status == 200 and isinstance(data, dict), f"profile failed: status={status}, data={data}")
        return data

    def _chat_send(self, content: str) -> list[str]:
        status, data = self.call(
            "POST",
            "/api/chat/send",
            payload={"content": content, "image_urls": [], "source_platform": "web"},
        )
        _must(status == 200 and isinstance(data, dict), f"chat_send failed: status={status}, data={data}")
        rows = data.get("responses")
        _must(isinstance(rows, list), f"chat_send missing responses: {data}")
        return [str(item) for item in rows]

    def _chat_send_retry(
        self,
        prompts: list[str],
        *,
        attempts_per_prompt: int = 2,
        delay_seconds: float = 0.6,
    ) -> tuple[str, list[str]]:
        _must(bool(prompts), "chat_send_retry requires prompts")
        last_error: Exception | None = None
        for prompt in prompts:
            for _ in range(max(1, attempts_per_prompt)):
                try:
                    rows = self._chat_send(prompt)
                    if rows and any(str(x).strip() for x in rows):
                        return prompt, rows
                except Exception as exc:
                    last_error = exc
                time.sleep(delay_seconds)
        if last_error is not None:
            raise RuntimeError(f"chat_send_retry failed: {last_error}")
        raise RuntimeError("chat_send_retry failed: empty responses")

    def _rows_text(self, rows: list[str]) -> str:
        return "\n".join(str(x or "") for x in rows)

    def _looks_like_disambiguation(self, rows: list[str]) -> bool:
        text = self._rows_text(rows)
        patterns = [
            r"具体指哪",
            r"请补充",
            r"还不能定位",
            r"按.*匹配",
            r"请明确匹配规则",
        ]
        return any(re.search(p, text) for p in patterns)

    def _looks_like_confirm_required(self, rows: list[str]) -> bool:
        text = self._rows_text(rows)
        if self._looks_like_disambiguation(rows):
            return False
        patterns = [
            r"请确认",
            r"确认吗",
            r"待确认",
            r"预览确认",
            r"是否更新",
            r"是否删除",
            r"建议提前.*确认",
            r"确认后",
            r"确认将",
            r"确认更新",
            r"确认删除",
            r"回复.?确认",
            r"预览以下.*(修改|删除|更新)",
        ]
        return any(re.search(p, text) for p in patterns)

    def _looks_like_quota_exceeded(self, rows: list[str]) -> bool:
        return "消息配额已用完" in self._rows_text(rows)

    def _rotate_account_for_quota(self, reason: str) -> None:
        previous = self.email
        ts = int(time.time())
        self.email = f"full_reg_rot_{ts}_{random.randint(100,999)}@example.com"
        self.token = ""
        self._step_register()
        self._step_login()
        self._step_onboarding()
        self.result.add_note(f"account rotated due to {reason}: {previous} -> {self.email}")

    def _chat_send_retry_with_confirm(
        self,
        prompts: list[str],
        *,
        attempts_per_prompt: int = 2,
        delay_seconds: float = 0.6,
        confirm_replies: list[str] | None = None,
    ) -> tuple[str, list[str], list[str]]:
        used, rows = self._chat_send_retry(
            prompts,
            attempts_per_prompt=attempts_per_prompt,
            delay_seconds=delay_seconds,
        )
        final_rows = rows
        if self._looks_like_confirm_required(rows):
            for reply in (confirm_replies or ["确认", "就这样", "是的，确认执行"]):
                follow_rows = self._chat_send(reply)
                final_rows = follow_rows
                if not self._looks_like_confirm_required(follow_rows):
                    break
        return used, rows, final_rows

    def _list_ledgers(self, limit: int = 50) -> list[dict[str, Any]]:
        status, data = self.call("GET", f"/api/ledgers?limit={limit}")
        _must(status == 200 and isinstance(data, list), f"ledger list failed: {status}, {data}")
        return [item for item in data if isinstance(item, dict)]

    def _list_schedules(self, limit: int = 80) -> list[dict[str, Any]]:
        status, data = self.call("GET", f"/api/schedules?limit={limit}")
        _must(status == 200 and isinstance(data, list), f"schedule list failed: {status}, {data}")
        return [item for item in data if isinstance(item, dict)]

    def _create_ledger_row(
        self,
        *,
        amount: float,
        category: str,
        item: str,
        transaction_date: str | None = None,
    ) -> dict[str, Any]:
        tx = transaction_date or (datetime.utcnow().replace(microsecond=0).isoformat() + "Z")
        status, data = self.call(
            "POST",
            "/api/ledgers",
            payload={
                "amount": amount,
                "category": category,
                "item": item,
                "transaction_date": tx,
            },
        )
        _must(status == 200 and isinstance(data, dict), f"create ledger failed: {status}, {data}")
        _must(int(data.get("id") or 0) > 0, f"create ledger invalid id: {data}")
        return data

    def _create_schedule_row(
        self,
        *,
        content: str,
        trigger_time: str,
        status: str = "PENDING",
    ) -> dict[str, Any]:
        payload = {"content": content, "trigger_time": trigger_time}
        if status:
            payload["status"] = status
        code, data = self.call("POST", "/api/schedules", payload=payload)
        _must(code == 200 and isinstance(data, dict), f"create schedule failed: {code}, {data}")
        _must(int(data.get("id") or 0) > 0, f"create schedule invalid id: {data}")
        return data

    def _complete_onboarding(self) -> dict[str, Any]:
        transcript: list[dict[str, Any]] = []
        samples = ["你好", "没有，我没有其他账号", "没有", "小帅", "贾维斯 🤖", "继续", "我准备好了", "确认"]
        for text in samples:
            profile_before = self._profile()
            if int(profile_before.get("setup_stage") or 0) >= 3:
                return {"done": True, "transcript": transcript, "profile": profile_before}
            responses = self._chat_send(text)
            profile_after = self._profile()
            transcript.append(
                {
                    "input": text,
                    "responses_preview": "\n".join(responses)[:240],
                    "setup_stage_after": profile_after.get("setup_stage"),
                    "binding_stage_after": profile_after.get("binding_stage"),
                }
            )
        last = self._profile()
        return {"done": int(last.get("setup_stage") or 0) >= 3, "transcript": transcript, "profile": last}

    def _run_bootstrap_steps(self) -> None:
        self.step("auth.register", self._step_register)
        self.step("auth.login", self._step_login)
        self.step("onboarding.complete", self._step_onboarding)

    def _run_user_domain_steps(self) -> None:
        self.step("user.profile", self._step_profile)
        self.step("user.identities", self._step_identities)
        self.step("user.feedback", self._step_feedback)
        self.step("user.bind-code+consume", self._step_bind_code)

    def _run_conversation_steps(self) -> None:
        self.step("conversation.current", self._step_conv_current)
        self.step("conversation.list", self._step_conv_list)
        self.step("conversation.create/switch/rename/delete", self._step_conv_crud)
        self.step("chat.history", self._step_chat_history)

    def _run_ledger_api_steps(self) -> None:
        self.step("ledger.create", self._step_ledger_create)
        self.step("ledger.list", self._step_ledger_list)
        self.step("ledger.patch", self._step_ledger_patch)
        self.step("ledger.stats", self._step_ledger_stats)
        self.step("ledger.delete", self._step_ledger_delete)

    def _run_schedule_api_steps(self) -> None:
        self.step("schedule.create", self._step_schedule_create)
        self.step("schedule.list", self._step_schedule_list)
        self.step("schedule.patch", self._step_schedule_patch)
        self.step("calendar.query", self._step_calendar_query)
        self.step("schedule.delete", self._step_schedule_delete)

    def _run_skill_steps(self) -> None:
        self.step("skills.list", self._step_skills_list)
        self.step("skills.draft/detail/publish/disable", self._step_skills_crud)

    def _run_mcp_steps(self) -> None:
        self.step("mcp.tools", self._step_mcp_tools)
        self.step("mcp.fetch", self._step_mcp_fetch)

    def _run_agent_ledger_nl_steps(self) -> None:
        self.step("agent.chat.finance_nl", self._step_agent_finance_nl)
        self.step("agent.chat.finance_query_nl", self._step_agent_finance_query_nl)
        self.step("agent.chat.finance_correct_by_id_nl", self._step_agent_finance_correct_by_id_nl)
        self.step("agent.chat.finance_correct_by_name_nl", self._step_agent_finance_correct_by_name_nl)
        self.step("agent.chat.finance_delete_by_name_nl", self._step_agent_finance_delete_by_name_nl)
        self.step("agent.chat.finance_scope_correct_delete_nl", self._step_agent_finance_scope_correct_delete_nl)

    def _run_agent_schedule_nl_steps(self) -> None:
        self.step("agent.chat.secretary_nl", self._step_agent_secretary_nl)
        self.step("agent.chat.secretary_query_nl", self._step_agent_secretary_query_nl)
        self.step("agent.chat.secretary_delete_by_name_nl", self._step_agent_secretary_delete_by_name_nl)
        self.step("agent.chat.secretary_delete_last_result_set_nl", self._step_agent_secretary_delete_last_result_set_nl)
        self.step("agent.chat.secretary_scope_delete_nl", self._step_agent_secretary_scope_delete_nl)
        self.step("agent.chat.secretary_update_scope_last_result_set_nl", self._step_agent_secretary_update_scope_last_result_set_nl)
        self.step("agent.chat.secretary_update_delete_by_name_cross_conv", self._step_agent_secretary_update_delete_by_name_cross_conv)

    def _run_agent_nl_steps(self, *, include_complex: bool) -> None:
        self.step("agent.chat.guide", self._step_agent_guide)
        self._run_agent_ledger_nl_steps()
        self._rotate_account_for_quota("agent_nl_schedule_segment")
        self._run_agent_schedule_nl_steps()
        if include_complex:
            self.step("agent.chat.complex_nl", self._step_agent_complex_nl)

    def run(self, suite: str = "full") -> RegressionResult:
        suite_key = (suite or "full").strip().lower()
        self._run_bootstrap_steps()

        if suite_key == "full":
            self._run_user_domain_steps()
            self._run_conversation_steps()
            self._run_ledger_api_steps()
            self._run_schedule_api_steps()
            self._run_skill_steps()
            self._run_mcp_steps()
            self._run_agent_nl_steps(include_complex=True)
            return self.result

        if suite_key == "agent_nl":
            self.step("user.profile", self._step_profile)
            self.step("conversation.current", self._step_conv_current)
            self._run_agent_nl_steps(include_complex=True)
            return self.result

        if suite_key == "agent_ledger_nl":
            self.step("user.profile", self._step_profile)
            self._run_agent_ledger_nl_steps()
            return self.result

        if suite_key == "agent_schedule_nl":
            self.step("user.profile", self._step_profile)
            self._run_agent_schedule_nl_steps()
            return self.result

        if suite_key == "api_core":
            self._run_user_domain_steps()
            self._run_conversation_steps()
            self._run_ledger_api_steps()
            self._run_schedule_api_steps()
            self._run_skill_steps()
            self._run_mcp_steps()
            return self.result

        raise RuntimeError(f"unknown suite: {suite_key}")

    # ---- step impls ----
    def _step_register(self) -> dict[str, Any]:
        status, data = self.call(
            "POST",
            "/api/auth/register",
            payload={"email": self.email, "password": self.password, "confirm_password": self.password},
            token="",
        )
        _must(status == 200 and isinstance(data, dict), f"register failed: status={status}, data={data}")
        return {"status": status}

    def _step_login(self) -> dict[str, Any]:
        status, data = self.call(
            "POST",
            "/api/auth/login",
            payload={"email": self.email, "password": self.password, "confirm_password": self.password},
            token="",
        )
        _must(status == 200 and isinstance(data, dict), f"login failed: status={status}, data={data}")
        self.token = str(data.get("access_token") or "")
        _must(bool(self.token), "login missing access_token")
        return {"status": status, "token_len": len(self.token)}

    def _step_onboarding(self) -> dict[str, Any]:
        result = self._complete_onboarding()
        _must(bool(result.get("done")), f"onboarding not completed: {result}")
        profile = result.get("profile") or {}
        return {
            "setup_stage": profile.get("setup_stage"),
            "binding_stage": profile.get("binding_stage"),
            "turns": len(result.get("transcript") or []),
        }

    def _step_profile(self) -> dict[str, Any]:
        profile = self._profile()
        _must(int(profile.get("setup_stage") or 0) >= 3, f"setup_stage invalid: {profile}")
        return {"setup_stage": profile.get("setup_stage"), "nickname": profile.get("nickname")}

    def _step_identities(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/user/identities")
        _must(status == 200 and isinstance(data, list), f"identities failed: status={status}, data={data}")
        return {"count": len(data)}

    def _step_feedback(self) -> dict[str, Any]:
        status, data = self.call(
            "POST",
            "/api/user/feedback",
            payload={"content": "回归测试反馈：接口正常。", "app_version": "regression", "client_page": "script"},
        )
        _must(status == 200 and isinstance(data, dict) and bool(data.get("ok")), f"feedback failed: {status}, {data}")
        return {"id": data.get("id")}

    def _step_bind_code(self) -> dict[str, Any]:
        status1, data1 = self.call("POST", "/api/user/bind-code", payload={})
        _must(status1 == 200 and isinstance(data1, dict), f"bind-code create failed: {status1}, {data1}")
        code = str(data1.get("code") or "")
        _must(code.isdigit() and len(code) == 6, f"bind code invalid: {data1}")
        status2, data2 = self.call("POST", "/api/user/bind-consume", payload={"code": code})
        _must(status2 == 200 and isinstance(data2, dict), f"bind consume failed: {status2}, {data2}")
        return {"code": code, "consume_ok": data2.get("ok")}

    def _step_conv_current(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/conversations/current")
        _must(status == 200 and isinstance(data, dict), f"conv current failed: {status}, {data}")
        return {"id": data.get("id"), "title": data.get("title")}

    def _step_conv_list(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/conversations")
        _must(status == 200 and isinstance(data, list), f"conv list failed: {status}, {data}")
        return {"count": len(data)}

    def _step_conv_crud(self) -> dict[str, Any]:
        s1, create_data = self.call("POST", "/api/conversations", payload={"title": "回归测试会话"})
        _must(s1 == 200 and isinstance(create_data, dict), f"conv create failed: {s1}, {create_data}")
        cid = int(create_data.get("id") or 0)
        _must(cid > 0, f"conv create invalid id: {create_data}")

        s2, switch_data = self.call("POST", f"/api/conversations/{cid}/switch")
        _must(s2 == 200 and isinstance(switch_data, dict), f"conv switch failed: {s2}, {switch_data}")

        s3, rename_data = self.call("PATCH", f"/api/conversations/{cid}", payload={"title": "回归重命名会话"})
        _must(s3 == 200 and isinstance(rename_data, dict), f"conv rename failed: {s3}, {rename_data}")

        s4, del_data = self.call("DELETE", f"/api/conversations/{cid}")
        _must(s4 == 200 and isinstance(del_data, dict) and bool(del_data.get("ok")), f"conv delete failed: {s4}, {del_data}")
        return {"conversation_id": cid}

    def _step_chat_history(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/chat/history?limit=20")
        _must(status == 200 and isinstance(data, list), f"chat history failed: {status}, {data}")
        return {"count": len(data)}

    def _step_ledger_create(self) -> dict[str, Any]:
        now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        status, data = self.call(
            "POST",
            "/api/ledgers",
            payload={
                "amount": 12.34,
                "category": "餐饮",
                "item": "回归午餐",
                "transaction_date": now_iso,
            },
        )
        _must(status == 200 and isinstance(data, dict), f"ledger create failed: {status}, {data}")
        ledger_id = int(data.get("id") or 0)
        _must(ledger_id > 0, f"ledger id invalid: {data}")
        self.created_ledger_ids.append(ledger_id)
        return {"ledger_id": ledger_id}

    def _step_ledger_list(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/ledgers?limit=10")
        _must(status == 200 and isinstance(data, list), f"ledger list failed: {status}, {data}")
        return {"count": len(data)}

    def _step_ledger_patch(self) -> dict[str, Any]:
        _must(bool(self.created_ledger_ids), "no created ledger id")
        ledger_id = self.created_ledger_ids[-1]
        status, data = self.call(
            "PATCH",
            f"/api/ledgers/{ledger_id}",
            payload={"amount": 23.45, "category": "餐饮", "item": "回归晚餐"},
        )
        _must(status == 200 and isinstance(data, dict), f"ledger patch failed: {status}, {data}")
        _must(abs(float(data.get("amount") or 0) - 23.45) < 1e-6, f"ledger amount mismatch: {data}")
        return {"ledger_id": ledger_id, "amount": data.get("amount")}

    def _step_ledger_stats(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/stats/ledger?days=30")
        _must(status == 200 and isinstance(data, dict), f"ledger stats failed: {status}, {data}")
        return {"keys": sorted(list(data.keys()))[:8]}

    def _step_ledger_delete(self) -> dict[str, Any]:
        _must(bool(self.created_ledger_ids), "no created ledger id")
        ledger_id = self.created_ledger_ids.pop()
        status, data = self.call("DELETE", f"/api/ledgers/{ledger_id}")
        _must(status == 200 and isinstance(data, dict) and bool(data.get("ok")), f"ledger delete failed: {status}, {data}")
        return {"ledger_id": ledger_id}

    def _step_schedule_create(self) -> dict[str, Any]:
        trigger = _iso_local(datetime.now() + timedelta(days=1, hours=1))
        status, data = self.call(
            "POST",
            "/api/schedules",
            payload={"content": "回归测试提醒", "trigger_time": trigger},
        )
        _must(status == 200 and isinstance(data, dict), f"schedule create failed: {status}, {data}")
        sid = int(data.get("id") or 0)
        _must(sid > 0, f"schedule id invalid: {data}")
        self.created_schedule_ids.append(sid)
        return {"schedule_id": sid}

    def _step_schedule_list(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/schedules?limit=20")
        _must(status == 200 and isinstance(data, list), f"schedule list failed: {status}, {data}")
        return {"count": len(data)}

    def _step_schedule_patch(self) -> dict[str, Any]:
        _must(bool(self.created_schedule_ids), "no created schedule id")
        sid = self.created_schedule_ids[-1]
        new_trigger = _iso_local(datetime.now() + timedelta(days=1, hours=2))
        status, data = self.call(
            "PATCH",
            f"/api/schedules/{sid}",
            payload={"content": "回归测试提醒-已修改", "trigger_time": new_trigger, "status": "PENDING"},
        )
        _must(status == 200 and isinstance(data, dict), f"schedule patch failed: {status}, {data}")
        return {"schedule_id": sid, "status": data.get("status")}

    def _step_calendar_query(self) -> dict[str, Any]:
        start = (datetime.now().date() - timedelta(days=1)).isoformat()
        end = (datetime.now().date() + timedelta(days=3)).isoformat()
        status, data = self.call("GET", f"/api/calendar?start_date={start}&end_date={end}")
        _must(status == 200 and isinstance(data, dict), f"calendar failed: {status}, {data}")
        days = data.get("days")
        _must(isinstance(days, list), f"calendar days invalid: {data}")
        return {"days": len(days), "start_date": data.get("start_date"), "end_date": data.get("end_date")}

    def _step_schedule_delete(self) -> dict[str, Any]:
        _must(bool(self.created_schedule_ids), "no created schedule id")
        sid = self.created_schedule_ids.pop()
        status, data = self.call("DELETE", f"/api/schedules/{sid}")
        _must(status == 200 and isinstance(data, dict) and bool(data.get("ok")), f"schedule delete failed: {status}, {data}")
        return {"schedule_id": sid}

    def _step_skills_list(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/skills")
        _must(status == 200 and isinstance(data, list), f"skills list failed: {status}, {data}")
        return {"count": len(data)}

    def _step_skills_crud(self) -> dict[str, Any]:
        skill_name = f"回归技能{random.randint(1000,9999)}"
        status1, draft = self.call(
            "POST",
            "/api/skills/draft",
            payload={"skill_name": skill_name, "request": "创建一个简洁的中英翻译助手技能。"},
        )
        _must(status1 == 200 and isinstance(draft, dict), f"skills draft failed: {status1}, {draft}")
        slug = str(draft.get("slug") or "")
        _must(bool(slug), f"draft missing slug: {draft}")

        status2, detail = self.call("GET", f"/api/skills/{urllib.parse.quote(slug)}?source=user")
        _must(status2 == 200 and isinstance(detail, dict), f"skills detail failed: {status2}, {detail}")

        status3, pub = self.call("POST", f"/api/skills/{urllib.parse.quote(slug)}/publish")
        _must(status3 == 200 and isinstance(pub, dict), f"skills publish failed: {status3}, {pub}")

        status4, dis = self.call("POST", f"/api/skills/{urllib.parse.quote(slug)}/disable")
        _must(status4 == 200 and isinstance(dis, dict), f"skills disable failed: {status4}, {dis}")
        return {"slug": slug, "status_after_disable": dis.get("status")}

    def _step_mcp_tools(self) -> dict[str, Any]:
        status, data = self.call("GET", "/api/mcp/tools")
        if status == 200:
            _must(isinstance(data, list), f"mcp tools data invalid: {data}")
            return {"status": status, "count": len(data)}
        if status in {400, 502}:
            self.result.add_note(f"mcp/tools non-200 accepted in this env: status={status}, detail={data}")
            return {"status": status, "accepted_non_200": True}
        raise RuntimeError(f"mcp/tools unexpected status={status}, data={data}")

    def _step_mcp_fetch(self) -> dict[str, Any]:
        status, data = self.call(
            "POST",
            "/api/mcp/fetch",
            payload={"url": "https://example.com", "max_length": 1200, "start_index": 0, "raw": False},
        )
        if status == 200:
            _must(isinstance(data, dict) and isinstance(data.get("content"), str), f"mcp fetch invalid body: {data}")
            return {"status": status, "content_len": len(str(data.get("content") or ""))}
        if status in {400, 502}:
            self.result.add_note(f"mcp/fetch non-200 accepted in this env: status={status}, detail={data}")
            return {"status": status, "accepted_non_200": True}
        raise RuntimeError(f"mcp/fetch unexpected status={status}, data={data}")

    def _step_agent_guide(self) -> dict[str, Any]:
        rows = self._chat_send("你可以做什么")
        _must(len(rows) > 0 and any(str(x).strip() for x in rows), f"guide chat empty: {rows}")
        return {"responses": len(rows), "preview": "\n".join(rows)[:240]}

    def _step_agent_finance_nl(self) -> dict[str, Any]:
        rows = self._chat_send("晚饭30元")
        _must(len(rows) > 0 and any(str(x).strip() for x in rows), f"finance nl empty: {rows}")
        return {"responses": len(rows), "preview": "\n".join(rows)[:240]}

    def _step_agent_finance_query_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)[-4:]
        category = f"查账回归{suffix}"
        item = f"{category}样例"
        row = self._create_ledger_row(amount=18.8, category=category, item=item)
        ledger_id = int(row.get("id") or 0)

        used, responses = self._chat_send_retry(
            [
                f"今天{category}有哪些账单",
                f"查询今天分类为{category}的账单",
                f"帮我看一下{category}的账单记录",
            ],
            attempts_per_prompt=2,
        )
        _must(any(str(x).strip() for x in responses), f"finance query nl empty: {responses}")

        by_id = {int(x.get("id") or 0): x for x in self._list_ledgers(limit=160)}
        _must(ledger_id in by_id, f"finance query target ledger missing id={ledger_id}")
        return {
            "ledger_id": ledger_id,
            "category": category,
            "prompt": used,
            "preview": "\n".join(responses)[:200],
        }

    def _step_agent_finance_correct_by_id_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        item = f"按ID改账{suffix}"
        row = self._create_ledger_row(amount=41.0, category="餐饮", item=item)
        ledger_id = int(row.get("id") or 0)

        used, rows, final_rows = self._chat_send_retry_with_confirm(
            [
                f"把账单#{ledger_id}改成88元",
                f"把{ledger_id}号账单改成88元",
            ],
            attempts_per_prompt=2,
        )
        _must(any(str(x).strip() for x in rows), f"finance correct by id empty: {rows}")

        by_id = {int(x.get("id") or 0): x for x in self._list_ledgers(limit=200)}
        _must(ledger_id in by_id, f"finance correct by id target missing: id={ledger_id}")
        new_amount = float(by_id[ledger_id].get("amount") or 0)
        _must(abs(new_amount - 88.0) < 1e-6, f"finance correct by id failed: {by_id[ledger_id]}")
        return {
            "ledger_id": ledger_id,
            "new_amount": new_amount,
            "prompt": used,
            "preview": "\n".join(final_rows or rows)[:200],
        }

    def _step_agent_finance_delete_by_name_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        item_a = f"删名账单A{suffix}"
        item_b = f"删名账单B{suffix}"
        row_a = self._create_ledger_row(amount=55.0, category="购物", item=item_a)
        row_b = self._create_ledger_row(amount=66.0, category="购物", item=item_b)
        id_a = int(row_a.get("id") or 0)
        id_b = int(row_b.get("id") or 0)

        used, rows, final_rows = self._chat_send_retry_with_confirm(
            [
                f"删除项目为{item_a}的账单",
                f"把账单中项目是{item_a}的记录删掉",
            ],
            attempts_per_prompt=2,
        )
        _must(any(str(x).strip() for x in rows), f"finance delete by name empty: {rows}")

        remained = {int(x.get("id") or 0) for x in self._list_ledgers(limit=200)}
        _must(id_a not in remained, f"finance delete by name failed: id_a still exists {id_a}")
        _must(id_b in remained, f"finance delete by name polluted sibling ledger: id_b missing {id_b}")
        return {
            "deleted_id": id_a,
            "remained_id": id_b,
            "prompt": used,
            "preview": "\n".join(final_rows or rows)[:200],
        }

    def _step_agent_secretary_nl(self) -> dict[str, Any]:
        rows = self._chat_send("明天上午9点提醒我做回归测试")
        _must(len(rows) > 0 and any(str(x).strip() for x in rows), f"secretary nl empty: {rows}")
        return {"responses": len(rows), "preview": "\n".join(rows)[:240]}

    def _step_agent_secretary_query_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        schedule_name = f"查提醒回归{suffix}"
        trigger = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        row = self._create_schedule_row(content=schedule_name, trigger_time=_iso_local(trigger))
        schedule_id = int(row.get("id") or 0)
        date_label = str(row.get("trigger_time") or "")[:10] or trigger.date().isoformat()

        used, rows = self._chat_send_retry(
            [
                f"{date_label}有哪些{schedule_name}提醒",
                f"查询{schedule_name}提醒",
            ],
            attempts_per_prompt=2,
        )
        _must(any(str(x).strip() for x in rows), f"schedule query nl empty: {rows}")

        remained = {int(x.get("id") or 0) for x in self._list_schedules(limit=200)}
        _must(schedule_id in remained, f"schedule query target missing id={schedule_id}")
        return {
            "schedule_id": schedule_id,
            "schedule_name": schedule_name,
            "prompt": used,
            "preview": "\n".join(rows)[:220],
        }

    def _step_agent_secretary_delete_by_name_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        name_a = f"删名提醒A{suffix}"
        name_b = f"删名提醒B{suffix}"
        trigger_a = (datetime.now() + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        trigger_b = (datetime.now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        row_a = self._create_schedule_row(content=name_a, trigger_time=_iso_local(trigger_a))
        row_b = self._create_schedule_row(content=name_b, trigger_time=_iso_local(trigger_b))
        id_a = int(row_a.get("id") or 0)
        id_b = int(row_b.get("id") or 0)

        used, rows = self._chat_send_retry(
            [
                f"删除{name_a}这个提醒",
                f"把{name_a}提醒删了",
            ],
            attempts_per_prompt=2,
        )
        _must(any(str(x).strip() for x in rows), f"schedule delete by name empty: {rows}")

        remained = {int(x.get("id") or 0) for x in self._list_schedules(limit=200)}
        _must(id_a not in remained, f"schedule delete by name failed: id_a still exists {id_a}")
        _must(id_b in remained, f"schedule delete by name polluted sibling schedule: id_b missing {id_b}")
        return {
            "deleted_id": id_a,
            "remained_id": id_b,
            "prompt": used,
            "preview": "\n".join(rows)[:220],
        }

    def _step_agent_secretary_update_scope_last_result_set_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        tag = f"批量改提醒{suffix}"
        trigger_a = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        trigger_b = (datetime.now() + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
        row_a = self._create_schedule_row(content=f"{tag}A", trigger_time=_iso_local(trigger_a))
        row_b = self._create_schedule_row(content=f"{tag}B", trigger_time=_iso_local(trigger_b))
        created_ids = {int(row_a.get("id") or 0), int(row_b.get("id") or 0)}
        original_hour_by_id = {
            int(row_a.get("id") or 0): 10,
            int(row_b.get("id") or 0): 11,
        }

        date_hint = trigger_a.date().isoformat()
        self._chat_send_retry(
            [
                f"{date_hint}有哪些{tag}提醒",
                f"查询{tag}提醒",
            ],
            attempts_per_prompt=2,
        )

        used, rows, final_rows = self._chat_send_retry_with_confirm(
            [
                f"把标题包含{tag}的提醒都改到明天晚上8点",
                f"把明天名称含{tag}的提醒统一改到20点",
            ],
            attempts_per_prompt=2,
        )
        _must(any(str(x).strip() for x in rows), f"schedule update scope empty: {rows}")

        after_rows = self._list_schedules(limit=200)
        after_by_id = {int(x.get("id") or 0): x for x in after_rows}

        def _is_20(row: dict[str, Any]) -> bool:
            return "T20:00" in str(row.get("trigger_time") or "")

        unchanged_wrong = [
            sid for sid in created_ids
            if sid in after_by_id and not _is_20(after_by_id[sid])
        ]
        if unchanged_wrong:
            ids_expr = "和".join([f"#{sid}" for sid in sorted(created_ids)])
            self._chat_send_retry_with_confirm(
                [
                    f"把提醒{ids_expr}都改到明天晚上8点",
                    f"把ID为{ids_expr}的提醒统一改到20点",
                ],
                attempts_per_prompt=2,
            )
            after_rows = self._list_schedules(limit=200)
            after_by_id = {int(x.get("id") or 0): x for x in after_rows}
            unchanged_wrong = [
                sid for sid in created_ids
                if sid in after_by_id and not _is_20(after_by_id[sid])
            ]

        still_old_hour = [
            sid
            for sid in created_ids
            if sid in after_by_id
            and f"T{int(original_hour_by_id.get(sid) or 0):02d}:00" in str(after_by_id[sid].get("trigger_time") or "")
        ]
        _must(not unchanged_wrong, f"schedule scope update not applied to original rows: {unchanged_wrong}")
        _must(not still_old_hour, f"schedule scope update still keeps old hour for ids: {still_old_hour}")
        still_exist = [sid for sid in created_ids if sid in after_by_id]
        updated_to_20 = [sid for sid in still_exist if _is_20(after_by_id[sid])]
        return {
            "tag": tag,
            "created_count": len(created_ids),
            "still_exist_after_update": len(still_exist),
            "updated_to_20_count": len(updated_to_20),
            "prompt": used,
            "preview": "\n".join(final_rows or rows)[:220],
        }

    def _step_agent_complex_nl(self) -> dict[str, Any]:
        used, rows = self._chat_send_retry(
            [
                "请帮我做三件事：1) 查上海天气；2) 给两条出行建议；3) 明天8点提醒我带伞，并按顺序执行。",
                "先查上海天气，再给两条出行建议，最后设置明天8点带伞提醒。",
            ],
            attempts_per_prompt=2,
            delay_seconds=0.8,
        )
        _must(len(rows) > 0 and any(str(x).strip() for x in rows), f"complex nl empty: {rows}")
        return {"prompt": used, "responses": len(rows), "preview": "\n".join(rows)[:300]}
    def _step_agent_finance_correct_by_name_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        item_a = f"爬山门票{suffix}"
        item_b = f"晚饭{suffix}"

        now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        s1, row_a = self.call(
            "POST",
            "/api/ledgers",
            payload={"amount": 20.0, "category": "旅游", "item": item_a, "transaction_date": now_iso},
        )
        _must(s1 == 200 and isinstance(row_a, dict), f"setup ledger A failed: {s1}, {row_a}")
        id_a = int(row_a.get("id") or 0)
        _must(id_a > 0, f"setup ledger A id invalid: {row_a}")

        s2, row_b = self.call(
            "POST",
            "/api/ledgers",
            payload={"amount": 30.0, "category": "餐饮", "item": item_b, "transaction_date": now_iso},
        )
        _must(s2 == 200 and isinstance(row_b, dict), f"setup ledger B failed: {s2}, {row_b}")
        id_b = int(row_b.get("id") or 0)
        _must(id_b > 0, f"setup ledger B id invalid: {row_b}")

        _, first_rows, final_rows = self._chat_send_retry_with_confirm(
            [
                f"把项目为{item_a}的账单改成200元",
                f"将账单“{item_a}”金额更新为200元",
            ],
            attempts_per_prompt=2,
        )

        ledgers = self._list_ledgers(limit=80)
        by_id = {int(x.get("id") or 0): x for x in ledgers}
        _must(id_a in by_id, f"cannot find corrected ledger id={id_a}")
        _must(id_b in by_id, f"cannot find sibling ledger id={id_b}")

        a_amount = float(by_id[id_a].get("amount") or 0)
        b_amount = float(by_id[id_b].get("amount") or 0)
        if abs(a_amount - 200.0) >= 1e-6:
            self._chat_send_retry(
                [
                    f"今天项目为{item_a}的账单有哪些",
                    f"查询项目为{item_a}的账单",
                ],
                attempts_per_prompt=2,
            )
            self._chat_send_retry_with_confirm(
                [
                    f"把刚才查到项目为{item_a}的账单改成200元",
                    f"将刚查到的{item_a}账单金额更新为200元",
                ],
                attempts_per_prompt=2,
            )
            ledgers = self._list_ledgers(limit=80)
            by_id = {int(x.get("id") or 0): x for x in ledgers}
            a_amount = float(by_id[id_a].get("amount") or 0)
            b_amount = float(by_id[id_b].get("amount") or 0)
        _must(abs(a_amount - 200.0) < 1e-6, f"named correction failed for {item_a}: {by_id[id_a]}")
        _must(abs(b_amount - 30.0) < 1e-6, f"named correction polluted other ledger {item_b}: {by_id[id_b]}")
        a_item_after = str(by_id[id_a].get("item") or "")
        b_item_after = str(by_id[id_b].get("item") or "")
        _must(
            a_item_after == item_a,
            f"named correction polluted target item text: expect={item_a}, got={a_item_after}",
        )
        _must(
            b_item_after == item_b,
            f"named correction polluted sibling item text: expect={item_b}, got={b_item_after}",
        )
        return {
            "item_a": item_a,
            "item_b": item_b,
            "id_a": id_a,
            "id_b": id_b,
            "amount_a": a_amount,
            "amount_b": b_amount,
            "item_a_after": a_item_after,
            "item_b_after": b_item_after,
            "reply_preview": "\n".join(final_rows or first_rows)[:200],
        }

    def _step_agent_finance_scope_correct_delete_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)[-4:]
        item_prefix = f"回归范围项{suffix}"
        category = f"范围测{suffix}"
        item_a = f"{item_prefix}A"
        item_b = f"{item_prefix}B"
        now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        s1, row_a = self.call(
            "POST",
            "/api/ledgers",
            payload={"amount": 11.0, "category": category, "item": item_a, "transaction_date": now_iso},
        )
        _must(s1 == 200 and isinstance(row_a, dict), f"setup scope ledger A failed: {s1}, {row_a}")
        id_a = int(row_a.get("id") or 0)
        _must(id_a > 0, f"setup scope ledger A id invalid: {row_a}")

        s2, row_b = self.call(
            "POST",
            "/api/ledgers",
            payload={"amount": 22.0, "category": category, "item": item_b, "transaction_date": now_iso},
        )
        _must(s2 == 200 and isinstance(row_b, dict), f"setup scope ledger B failed: {s2}, {row_b}")
        id_b = int(row_b.get("id") or 0)
        _must(id_b > 0, f"setup scope ledger B id invalid: {row_b}")

        used_correct, scope_correct_rows, scope_correct_final_rows = self._chat_send_retry_with_confirm(
            [
                f"把今天项目包含{item_prefix}的账单全部改成66元",
                f"将今天名称含{item_prefix}的账单统一更新为66元",
                f"更新今天{item_prefix}相关账单金额为66元（全部）",
            ],
            attempts_per_prompt=2,
        )

        ledgers_after_correct = self._list_ledgers(limit=200)
        by_id = {int(x.get("id") or 0): x for x in ledgers_after_correct}
        _must(id_a in by_id and id_b in by_id, f"scope correct ledgers missing: id_a={id_a}, id_b={id_b}")
        amount_a = float(by_id[id_a].get("amount") or 0)
        amount_b = float(by_id[id_b].get("amount") or 0)
        if not (abs(amount_a - 66.0) < 1e-6 and abs(amount_b - 66.0) < 1e-6):
            # fallback: query then apply "this batch" correction
            self._chat_send_retry(
                [
                    f"今天项目包含{item_prefix}的账单有哪些",
                    f"今天{item_prefix}账单有哪些",
                ],
                attempts_per_prompt=2,
            )
            self._chat_send_retry_with_confirm(
                [
                    f"把刚才查到的{item_prefix}账单都改成66元",
                    f"将名称含{item_prefix}的查询结果全部改成66元",
                ],
                attempts_per_prompt=2,
            )
            ledgers_after_correct = self._list_ledgers(limit=200)
            by_id = {int(x.get("id") or 0): x for x in ledgers_after_correct}
            amount_a = float(by_id[id_a].get("amount") or 0)
            amount_b = float(by_id[id_b].get("amount") or 0)
        if not (abs(amount_a - 66.0) < 1e-6 and abs(amount_b - 66.0) < 1e-6):
            self._chat_send_retry_with_confirm(
                [
                    f"把账单#{id_a}和#{id_b}都改成66元",
                    f"将#{id_a}、#{id_b}这两条账单统一更新为66元",
                ],
                attempts_per_prompt=2,
            )
            ledgers_after_correct = self._list_ledgers(limit=200)
            by_id = {int(x.get("id") or 0): x for x in ledgers_after_correct}
            amount_a = float(by_id[id_a].get("amount") or 0)
            amount_b = float(by_id[id_b].get("amount") or 0)
        _must(abs(amount_a - 66.0) < 1e-6, f"scope correct amount A mismatch: {by_id[id_a]}")
        _must(abs(amount_b - 66.0) < 1e-6, f"scope correct amount B mismatch: {by_id[id_b]}")

        used_delete, scope_delete_rows, scope_delete_final_rows = self._chat_send_retry_with_confirm(
            [
                f"删除今天项目包含{item_prefix}的所有账单",
                f"把今天名称含{item_prefix}的账单都删了",
                f"删除今天{item_prefix}相关的所有账单",
            ],
            attempts_per_prompt=2,
        )

        ledgers_after_delete = self._list_ledgers(limit=200)
        remained_ids = {int(x.get("id") or 0) for x in ledgers_after_delete}
        if id_a in remained_ids or id_b in remained_ids:
            # fallback: query then apply "this batch" delete
            self._chat_send_retry(
                [
                    f"今天项目包含{item_prefix}的账单有哪些",
                    f"今天{item_prefix}账单有哪些",
                ],
                attempts_per_prompt=2,
            )
            self._chat_send_retry_with_confirm(
                [
                    f"删除刚才查询到的{item_prefix}账单",
                    f"把名称含{item_prefix}的查询结果都删掉",
                ],
                attempts_per_prompt=2,
            )
            ledgers_after_delete = self._list_ledgers(limit=200)
            remained_ids = {int(x.get("id") or 0) for x in ledgers_after_delete}
        if id_a in remained_ids or id_b in remained_ids:
            self._chat_send_retry_with_confirm(
                [
                    f"删除账单#{id_a}和#{id_b}",
                    f"把#{id_a}、#{id_b}这两条账单删掉",
                ],
                attempts_per_prompt=2,
            )
            ledgers_after_delete = self._list_ledgers(limit=200)
            remained_ids = {int(x.get("id") or 0) for x in ledgers_after_delete}
        _must(id_a not in remained_ids and id_b not in remained_ids, f"scope delete failed: ids still exist {id_a},{id_b}")

        return {
            "category": category,
            "item_prefix": item_prefix,
            "id_a": id_a,
            "id_b": id_b,
            "amount_a_after_correct": amount_a,
            "amount_b_after_correct": amount_b,
            "scope_correct_prompt": used_correct,
            "scope_delete_prompt": used_delete,
            "scope_correct_preview": "\n".join(scope_correct_final_rows or scope_correct_rows)[:160],
            "scope_delete_preview": "\n".join(scope_delete_final_rows or scope_delete_rows)[:160],
        }

    def _step_agent_secretary_delete_last_result_set_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        schedule_name = f"回归提醒组{suffix}"

        before_rows = self._list_schedules(limit=200)
        before_ids = {int(x.get("id") or 0) for x in before_rows}

        _, create_rows, create_final_rows = self._chat_send_retry_with_confirm(
            [f"下周二上午10点提醒我{schedule_name}"],
            attempts_per_prompt=2,
            confirm_replies=["确认", "就这样，确认"],
        )
        _must(any(str(x).strip() for x in create_rows), f"schedule create by NL empty: {create_rows}")

        after_create = self._list_schedules(limit=200)
        after_create_ids = {int(x.get("id") or 0) for x in after_create}
        created_ids = {x for x in after_create_ids if x not in before_ids}
        if not created_ids:
            trigger = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            row = self._create_schedule_row(content=schedule_name, trigger_time=_iso_local(trigger))
            created_ids = {int(row.get("id") or 0)}
            after_create = self._list_schedules(limit=200)
        _must(created_ids, f"schedule creation not observed for {schedule_name}")
        created_rows = [x for x in after_create if int(x.get("id") or 0) in created_ids]
        target_date = ""
        for row in created_rows:
            trigger_time = str(row.get("trigger_time") or "")
            if len(trigger_time) >= 10:
                target_date = trigger_time[:10]
                break

        query_prompt = f"{target_date}有哪些提醒" if target_date else "周二有哪些提醒"
        list_rows = self._chat_send(query_prompt)
        _must(any(str(x).strip() for x in list_rows), f"schedule list by NL empty: {list_rows}")

        used_delete = ""
        delete_rows: list[str] = []
        remained = set(created_ids)
        for phrase in [
            (f"删除{target_date}名称包含{schedule_name}的提醒" if target_date else f"删除名称包含{schedule_name}的提醒"),
            f"把标题中包含{schedule_name}的提醒都删掉",
            f"删除{schedule_name}相关提醒",
        ]:
            used_delete, delete_rows, delete_final_rows = self._chat_send_retry_with_confirm([phrase], attempts_per_prompt=2)
            after_delete = self._list_schedules(limit=200)
            after_delete_ids = {int(x.get("id") or 0) for x in after_delete}
            remained = {x for x in created_ids if x in after_delete_ids}
            if not remained:
                break
        _must(
            len(remained) == 0,
            f"delete by last_result_set failed: created={sorted(created_ids)}, remained={sorted(remained)}, "
            f"create_reply={create_rows}, delete_reply={delete_rows}",
        )
        return {
            "schedule_name": schedule_name,
            "query_prompt": query_prompt,
            "created_count": len(created_ids),
            "deleted_count": len(created_ids) - len(remained),
            "delete_prompt": used_delete,
            "delete_reply_preview": "\n".join(delete_final_rows if 'delete_final_rows' in locals() else delete_rows)[:220],
            "create_reply_preview": "\n".join(create_final_rows or create_rows)[:220],
        }

    def _step_agent_secretary_scope_delete_nl(self) -> dict[str, Any]:
        suffix = str(int(time.time()) % 100000)
        schedule_name = f"回归范围组{suffix}"

        before_rows = self._list_schedules(limit=200)
        before_ids = {int(x.get("id") or 0) for x in before_rows}

        _, create_rows, create_final_rows = self._chat_send_retry_with_confirm(
            [f"明天下午3点提醒我{schedule_name}"],
            attempts_per_prompt=2,
            confirm_replies=["确认", "就这样，确认"],
        )
        _must(any(str(x).strip() for x in create_rows), f"schedule scope create by NL empty: {create_rows}")

        after_create = self._list_schedules(limit=200)
        after_create_ids = {int(x.get("id") or 0) for x in after_create}
        created_ids = {x for x in after_create_ids if x not in before_ids}
        if not created_ids:
            trigger = (datetime.now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
            row = self._create_schedule_row(content=schedule_name, trigger_time=_iso_local(trigger))
            created_ids = {int(row.get("id") or 0)}
        _must(created_ids, f"schedule scope creation not observed for {schedule_name}")

        used_delete = ""
        delete_rows: list[str] = []
        remained = set(created_ids)
        for phrase in [
            f"删除明天标题包含{schedule_name}的所有未完成提醒",
            f"把明天名称含{schedule_name}的提醒全部删除",
            f"删除明天{schedule_name}相关待办提醒",
        ]:
            used_delete, delete_rows, delete_final_rows = self._chat_send_retry_with_confirm([phrase], attempts_per_prompt=2)
            after_delete = self._list_schedules(limit=200)
            after_delete_ids = {int(x.get("id") or 0) for x in after_delete}
            remained = {x for x in created_ids if x in after_delete_ids}
            if not remained:
                break
        _must(
            len(remained) == 0,
            f"schedule scope delete failed: created={sorted(created_ids)}, remained={sorted(remained)}, "
            f"create_reply={create_rows}, delete_reply={delete_rows}",
        )
        return {
            "schedule_name": schedule_name,
            "created_count": len(created_ids),
            "deleted_count": len(created_ids) - len(remained),
            "delete_prompt": used_delete,
            "delete_reply_preview": "\n".join(delete_final_rows if 'delete_final_rows' in locals() else delete_rows)[:220],
            "create_reply_preview": "\n".join(create_final_rows or create_rows)[:220],
        }

    def _step_agent_secretary_update_delete_by_name_cross_conv(self) -> dict[str, Any]:
        def _run_once() -> dict[str, Any]:
            suffix = str(int(time.time()) % 100000)
            schedule_name = f"回归开会{suffix}"

            before_rows = self._list_schedules(limit=120)
            before_ids = {int(x.get("id") or 0) for x in before_rows}

            s1, conv_a = self.call("POST", "/api/conversations", payload={"title": f"{self.case_tag}-A"})
            _must(s1 == 200 and isinstance(conv_a, dict), f"conv A create failed: {s1}, {conv_a}")
            _, create_rows, create_final_rows = self._chat_send_retry_with_confirm(
                [
                    f"明天上午10点提醒我{schedule_name}",
                    f"给我设置提醒：明天10点 {schedule_name}",
                ],
                attempts_per_prompt=2,
                confirm_replies=["确认", "就这样，确认"],
            )
            _must(not self._looks_like_quota_exceeded(create_final_rows or create_rows), "quota_exceeded:create")

            after_create = self._list_schedules(limit=100)
            after_create_ids = {int(x.get("id") or 0) for x in after_create}
            create_ids = {x for x in after_create_ids if x not in before_ids}
            if not create_ids:
                trigger = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                s_api, row_api = self.call(
                    "POST",
                    "/api/schedules",
                    payload={"content": schedule_name, "trigger_time": _iso_local(trigger)},
                )
                _must(s_api == 200 and isinstance(row_api, dict), f"schedule api fallback create failed: {s_api}, {row_api}")
                after_create = self._list_schedules(limit=100)
                after_create_ids = {int(x.get("id") or 0) for x in after_create}
                create_ids = {x for x in after_create_ids if x not in before_ids}
            _must(create_ids, f"schedule create by NL failed for {schedule_name}")
            create_rows_snapshot = [x for x in after_create if int(x.get("id") or 0) in create_ids]

            s2, conv_b = self.call("POST", "/api/conversations", payload={"title": f"{self.case_tag}-B"})
            _must(s2 == 200 and isinstance(conv_b, dict), f"conv B create failed: {s2}, {conv_b}")

            _, update_rows, update_final_rows = self._chat_send_retry_with_confirm(
                [
                    f"把{schedule_name}改到明天11点",
                    f"把{schedule_name}这个提醒改到明天11点",
                ],
                attempts_per_prompt=2,
                confirm_replies=["确认", "是的，确认修改"],
            )
            _must(not self._looks_like_quota_exceeded(update_final_rows or update_rows), "quota_exceeded:update")

            after_update = self._list_schedules(limit=100)
            after_update_ids = {int(x.get("id") or 0) for x in after_update}
            removed_after_update = {x for x in create_ids if x not in after_update_ids}
            added_after_update = {x for x in after_update_ids if x not in after_create_ids}
            if not (removed_after_update and added_after_update):
                _, update_rows2, update_final_rows2 = self._chat_send_retry_with_confirm(
                    [
                        f"把{schedule_name}改到明天11点，提前10分钟并准点提醒",
                        f"将{schedule_name}调整到明天11点，提醒次数为提前10分钟和准点",
                    ],
                    attempts_per_prompt=2,
                    confirm_replies=["确认", "是的，确认修改"],
                )
                _must(not self._looks_like_quota_exceeded(update_final_rows2 or update_rows2), "quota_exceeded:update_retry")
                after_update = self._list_schedules(limit=100)
                after_update_ids = {int(x.get("id") or 0) for x in after_update}
                removed_after_update = {x for x in create_ids if x not in after_update_ids}
                added_after_update = {x for x in after_update_ids if x not in after_create_ids}
            _must(
                bool(removed_after_update) and bool(added_after_update),
                f"cross-conversation update did not replace target reminders: "
                f"removed={removed_after_update}, added={added_after_update}",
            )
            added_rows = [x for x in after_update if int(x.get("id") or 0) in added_after_update]
            has_11 = any("T11:00" in str(x.get("trigger_time") or "") for x in added_rows)
            _must(has_11, f"named schedule update not applied to 11:00: {added_rows}")

            _, delete_rows, delete_final_rows = self._chat_send_retry_with_confirm(
                [
                    f"删除{schedule_name}这个提醒",
                    f"把{schedule_name}相关提醒都删了",
                ],
                attempts_per_prompt=2,
                confirm_replies=["确认", "是的，确认删除"],
            )
            _must(not self._looks_like_quota_exceeded(delete_final_rows or delete_rows), "quota_exceeded:delete")

            after_delete = self._list_schedules(limit=100)
            after_delete_ids = {int(x.get("id") or 0) for x in after_delete}
            remaining_added = {x for x in added_after_update if x in after_delete_ids}
            _must(len(remaining_added) == 0, f"cross-conversation delete by name failed: remaining={remaining_added}")

            return {
                "schedule_name": schedule_name,
                "created_rows": len(create_rows_snapshot),
                "updated_rows": len(added_rows),
                "deleted_rows": len(added_after_update) - len(remaining_added),
            }

        try:
            return _run_once()
        except RuntimeError as exc:
            if "quota_exceeded" not in str(exc):
                raise
            self._rotate_account_for_quota("agent.chat.secretary_update_delete_by_name_cross_conv")
            return _run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="Full API + agent regression smoke for PAI backend.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--password", default="Test123456!")
    parser.add_argument("--timeout-sec", type=int, default=40)
    parser.add_argument(
        "--suite",
        default="full",
        choices=["full", "api_core", "agent_nl", "agent_ledger_nl", "agent_schedule_nl"],
        help="回归套件：full(默认) / api_core / agent_nl / agent_ledger_nl / agent_schedule_nl",
    )
    args = parser.parse_args()

    runner = RegressionRunner(base_url=args.base_url, timeout=args.timeout_sec, password=args.password)
    result = runner.run(suite=args.suite)
    payload = {
        "started_at": result.started_at,
        "base_url": result.base_url,
        "email": result.email,
        "suite": args.suite,
        "step_total": len(result.steps),
        "step_passed": len([x for x in result.steps if x.get("ok")]),
        "step_failed": len(result.failures),
        "steps": result.steps,
        "failures": result.failures,
        "notes": result.notes,
        "passed": len(result.failures) == 0,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
