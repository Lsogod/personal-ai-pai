from __future__ import annotations

import asyncio
import logging
import secrets
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

import redis.asyncio as redis

from app.core.config import get_settings


logger = logging.getLogger(__name__)
_settings = get_settings()
_redis = redis.from_url(_settings.redis_url, decode_responses=True)


PURPOSE_REGISTER = "register"
PURPOSE_LOGIN = "login"
PURPOSE_RESET_PASSWORD = "reset_password"
ALLOWED_PURPOSES = {PURPOSE_REGISTER, PURPOSE_LOGIN, PURPOSE_RESET_PASSWORD}

_PURPOSE_LABEL = {
    PURPOSE_REGISTER: "注册",
    PURPOSE_LOGIN: "登录",
    PURPOSE_RESET_PASSWORD: "重置密码",
}


class EmailCodeError(Exception):
    pass


class EmailServiceNotConfiguredError(EmailCodeError):
    pass


class EmailCodeCooldownError(EmailCodeError):
    def __init__(self, retry_after: int):
        super().__init__("verification code send too frequently")
        self.retry_after = max(1, int(retry_after))


class EmailCodeExpiredError(EmailCodeError):
    pass


class EmailCodeInvalidError(EmailCodeError):
    pass


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def _smtp_ready() -> bool:
    host = str(_settings.smtp_host or "").strip()
    from_email = str(_settings.smtp_from_email or _settings.smtp_user or "").strip()
    return bool(host and from_email)


def _code_key(email: str, purpose: str) -> str:
    return f"pai:auth:email_code:{purpose}:{email}"


def _cooldown_key(email: str, purpose: str) -> str:
    return f"pai:auth:email_code_cd:{purpose}:{email}"


def _attempt_key(email: str, purpose: str) -> str:
    return f"pai:auth:email_code_attempt:{purpose}:{email}"


def _generate_code() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def _render_email_subject(purpose: str) -> str:
    label = _PURPOSE_LABEL.get(purpose, "验证")
    return f"PAI {label}验证码"


def _render_email_text(*, email: str, purpose: str, code: str, ttl_sec: int) -> str:
    label = _PURPOSE_LABEL.get(purpose, "验证")
    ttl_min = max(1, int(ttl_sec // 60))
    return (
        f"您好，\n\n"
        f"您正在进行 PAI 账号{label}操作。\n"
        f"本次验证码：{code}\n"
        f"有效期：{ttl_min} 分钟\n\n"
        f"如非本人操作，请忽略此邮件。\n"
        f"收件邮箱：{email}\n"
    )


def _send_email_blocking(*, to_email: str, subject: str, text: str) -> None:
    settings = get_settings()
    host = str(settings.smtp_host or "").strip()
    if not host:
        raise EmailServiceNotConfiguredError("email service not configured")

    from_email = str(settings.smtp_from_email or settings.smtp_user or "").strip()
    if not from_email:
        raise EmailServiceNotConfiguredError("email service not configured")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((str(settings.smtp_from_name or "PAI"), from_email))
    msg["To"] = to_email
    msg.set_content(text)

    timeout = 20
    port = int(settings.smtp_port or 465)
    user = str(settings.smtp_user or "").strip()
    password = str(settings.smtp_password or "")
    use_ssl = bool(settings.smtp_use_ssl)
    use_starttls = bool(settings.smtp_use_starttls)

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        if use_starttls:
            server.starttls()
        if user:
            server.login(user, password)
        server.send_message(msg)


async def send_email_code(*, email: str, purpose: str) -> dict[str, int]:
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError("unsupported purpose")
    if not _smtp_ready():
        raise EmailServiceNotConfiguredError("email service not configured")

    normalized_email = normalize_email(email)
    code = _generate_code()
    ttl_sec = max(60, int(_settings.auth_email_code_ttl_sec or 600))
    cooldown_sec = max(10, int(_settings.auth_email_code_cooldown_sec or 60))
    code_key = _code_key(normalized_email, purpose)
    cooldown_key = _cooldown_key(normalized_email, purpose)

    cooldown_ttl = await _redis.ttl(cooldown_key)
    if cooldown_ttl and cooldown_ttl > 0:
        raise EmailCodeCooldownError(cooldown_ttl)

    await _redis.set(code_key, code, ex=ttl_sec)
    await _redis.set(cooldown_key, "1", ex=cooldown_sec)
    await _redis.delete(_attempt_key(normalized_email, purpose))

    try:
        await asyncio.to_thread(
            _send_email_blocking,
            to_email=normalized_email,
            subject=_render_email_subject(purpose),
            text=_render_email_text(email=normalized_email, purpose=purpose, code=code, ttl_sec=ttl_sec),
        )
    except Exception:
        await _redis.delete(code_key)
        logger.exception("send email code failed: purpose=%s email=%s", purpose, normalized_email)
        raise

    return {"expire_seconds": ttl_sec, "cooldown_seconds": cooldown_sec}


async def verify_email_code(*, email: str, purpose: str, code: str) -> None:
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError("unsupported purpose")

    normalized_email = normalize_email(email)
    normalized_code = normalize_code(code)
    if len(normalized_code) != 6:
        raise EmailCodeInvalidError("verification code incorrect")

    code_key = _code_key(normalized_email, purpose)
    attempt_key = _attempt_key(normalized_email, purpose)
    stored = await _redis.get(code_key)
    if not stored:
        raise EmailCodeExpiredError("verification code expired")

    if str(stored).strip() != normalized_code:
        attempts = int(await _redis.incr(attempt_key))
        if attempts == 1:
            ttl = await _redis.ttl(code_key)
            if ttl and ttl > 0:
                await _redis.expire(attempt_key, int(ttl))
        max_attempts = max(1, int(_settings.auth_email_code_max_verify_attempts or 8))
        if attempts >= max_attempts:
            await _redis.delete(code_key)
            await _redis.delete(attempt_key)
            raise EmailCodeExpiredError("verification code expired")
        raise EmailCodeInvalidError("verification code incorrect")

    await _redis.delete(code_key)
    await _redis.delete(attempt_key)

