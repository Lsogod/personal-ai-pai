import json
import re
import base64
import mimetypes
from typing import Any

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.services.llm import get_llm
from app.core.config import get_settings
from app.services.platforms.feishu import fetch_image_data_url


AMOUNT_TEXT_PATTERN = re.compile(r"\d+(?:\.\d{1,2})?")
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_amount_candidates(text: str) -> list[float]:
    candidates: list[float] = []
    for token in AMOUNT_TEXT_PATTERN.findall(text or ""):
        value = _as_float(token)
        if value is None:
            continue
        if value <= 0:
            continue
        if value not in candidates:
            candidates.append(value)
    return candidates[:5]


class VisionExtraction(BaseModel):
    image_type: str = Field(default="other")
    amount: float | None = Field(default=None)
    amount_candidates: list[float] = Field(default_factory=list)
    merchant: str = Field(default="")
    category: str = Field(default="其他")
    item: str = Field(default="")
    confidence: float = Field(default=0.0)
    evidence_text: str = Field(default="")
    notes: str = Field(default="")


def _normalize_mime_type(content_type: str | None, image_ref: str) -> str:
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw.startswith("image/"):
        return raw
    guessed, _ = mimetypes.guess_type(image_ref)
    if guessed and guessed.startswith("image/"):
        return guessed
    return "image/jpeg"


def _bytes_to_data_url(content: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(content).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


async def _resolve_image_ref_to_data_url(image_ref: str) -> tuple[str | None, str | None]:
    image_ref = (image_ref or "").strip()
    if not image_ref:
        return None, "empty_image_ref"
    if image_ref.startswith("data:image/"):
        return image_ref, None
    if image_ref.startswith("feishu://"):
        try:
            _, payload = image_ref.split("://", 1)
            message_id, image_key = payload.split("/", 1)
            resolved = await fetch_image_data_url(message_id, image_key)
            if not resolved:
                return None, "feishu_image_fetch_failed"
            return resolved, None
        except Exception:
            return None, "invalid_feishu_image_ref"
    if image_ref.startswith("http://") or image_ref.startswith("https://"):
        headers = {
            "User-Agent": "PAI-VisionFetcher/1.0",
            "Accept": "image/*,*/*;q=0.8",
        }
        timeout = httpx.Timeout(connect=8, read=20, write=8, pool=8)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(image_ref, headers=headers)
        except Exception:
            return None, "image_download_failed"
        if resp.status_code != 200:
            return None, f"image_http_{resp.status_code}"
        body = resp.content or b""
        if not body:
            return None, "empty_image_body"
        if len(body) > MAX_IMAGE_BYTES:
            return None, "image_too_large"
        mime_type = _normalize_mime_type(resp.headers.get("content-type"), image_ref)
        return _bytes_to_data_url(body, mime_type), None
    return None, "unsupported_image_ref"


def _normalize_vision_output(data: dict[str, Any], raw: str) -> dict[str, Any]:
    image_type = str(data.get("image_type") or "other").strip().lower()
    if image_type not in {"receipt", "payment_screenshot", "other"}:
        image_type = "other"

    amount_candidates: list[float] = []
    raw_candidates = data.get("amount_candidates")
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            value = _as_float(item)
            if value is None or value <= 0:
                continue
            if value not in amount_candidates:
                amount_candidates.append(value)

    amount = _as_float(data.get("amount"))
    if amount is None or amount <= 0:
        if amount_candidates:
            amount = amount_candidates[0]
        else:
            from_text = _extract_amount_candidates(raw)
            if from_text:
                amount = from_text[0]
                for value in from_text:
                    if value not in amount_candidates:
                        amount_candidates.append(value)

    confidence = _as_float(data.get("confidence")) or 0.0
    if confidence < 0:
        confidence = 0.0
    if confidence > 1:
        confidence = 1.0

    merchant = str(data.get("merchant") or "").strip()
    category = str(data.get("category") or "其他").strip() or "其他"
    item = str(data.get("item") or "").strip()
    if not item:
        item = merchant or "消费"

    result: dict[str, Any] = {
        "image_type": image_type,
        "amount": amount,
        "amount_candidates": amount_candidates[:5],
        "merchant": merchant,
        "category": category,
        "item": item,
        "confidence": confidence,
    }
    if data.get("evidence_text"):
        result["evidence_text"] = str(data.get("evidence_text"))
    if data.get("notes"):
        result["notes"] = str(data.get("notes"))
    return result


async def analyze_receipt(image_url: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        return {"confidence": 0.0, "reason": "missing_api_key"}

    image_data_url, reason = await _resolve_image_ref_to_data_url(image_url)
    if not image_data_url:
        return {"confidence": 0.0, "reason": reason or "image_resolve_failed"}

    llm = get_llm(model=settings.vision_model, node_name="vision")
    runnable = llm.with_structured_output(VisionExtraction)
    system = SystemMessage(
        content=(
            "你是记账视觉解析器。你会收到一张图片，可能是小票，也可能是支付截图。"
            "请返回结构化字段。字段："
            "image_type(receipt|payment_screenshot|other), "
            "amount(float|null), amount_candidates(float[]), merchant(str), "
            "category(str), item(str), confidence(float 0-1), evidence_text(str), notes(str)。"
            "规则："
            "1) 若是支付截图，优先识别“实付/支付金额/总计/合计”作为 amount；"
            "2) 如果存在多个可能金额，把候选放入 amount_candidates；"
            "3) 无法确认时 amount=null、confidence<0.8；"
            "4) 不要输出 markdown，不要解释文字。"
        )
    )
    human = HumanMessage(
        content=[
            {"type": "text", "text": "请解析这张图片用于记账。"},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    )

    try:
        parsed = await runnable.ainvoke([system, human])
    except Exception as exc:
        return {"confidence": 0.0, "reason": "vision_invoke_failed", "error": str(exc)}
    if isinstance(parsed, BaseModel):
        data = parsed.model_dump()
    elif isinstance(parsed, dict):
        data = parsed
    else:
        data = {}
    if not data:
        return {"confidence": 0.0, "reason": "invalid_vision_json", "raw": ""}
    normalized = _normalize_vision_output(data, json.dumps(data, ensure_ascii=False))
    return normalized
