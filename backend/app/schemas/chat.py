import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


MAX_CHAT_IMAGES = 6
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CHAT_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
DATA_IMAGE_URL_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)


def _estimate_data_url_bytes(value: str) -> int:
    parts = value.split(",", 1)
    if len(parts) != 2:
        return 0
    payload = parts[1].strip()
    if not payload:
        return 0
    padding = 0
    if payload.endswith("=="):
        padding = 2
    elif payload.endswith("="):
        padding = 1
    return max(0, (len(payload) * 3) // 4 - padding)


class ChatSendRequest(BaseModel):
    content: str
    image_urls: List[str] = Field(default_factory=list)
    source_platform: Optional[str] = None

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(cls, value: List[str]) -> List[str]:
        if len(value) > MAX_CHAT_IMAGES:
            raise ValueError(f"最多上传 {MAX_CHAT_IMAGES} 张图片")

        normalized: List[str] = []
        total_bytes = 0
        for raw in value:
            image_ref = str(raw or "").strip()
            if not image_ref:
                continue
            if image_ref.startswith(("http://", "https://", "feishu://")):
                normalized.append(image_ref)
                continue
            if not DATA_IMAGE_URL_RE.match(image_ref):
                raise ValueError("图片格式不支持，仅支持 data:image、http(s) 或 feishu://")

            image_bytes = _estimate_data_url_bytes(image_ref)
            if image_bytes <= 0:
                raise ValueError("图片数据无效")
            if image_bytes > MAX_CHAT_IMAGE_BYTES:
                raise ValueError("单张图片不能超过 5MB")
            total_bytes += image_bytes
            if total_bytes > MAX_CHAT_TOTAL_IMAGE_BYTES:
                raise ValueError("图片总大小不能超过 20MB")
            normalized.append(image_ref)
        return normalized


class ChatMessage(BaseModel):
    role: str
    content: str
    created_at: str
    image_urls: List[str] = Field(default_factory=list)


class ChatSendResponse(BaseModel):
    responses: List[str]
    debug: Optional[Dict[str, Any]] = None


class ProfileResponse(BaseModel):
    uuid: str
    nickname: str
    ai_name: str
    ai_emoji: str
    platform: str
    email: Optional[str] = None
    residence_city: Optional[str] = None
    residence_province: Optional[str] = None
    residence_country: Optional[str] = None
    has_other_client_accounts: Optional[bool] = None
    setup_stage: int
    binding_stage: int = 0
