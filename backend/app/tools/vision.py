import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm import get_llm
from app.core.config import get_settings
from app.services.platforms.feishu import fetch_image_data_url


async def analyze_receipt(image_url: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        return {"confidence": 0.0, "reason": "missing_api_key"}

    if image_url.startswith("feishu://"):
        try:
            _, payload = image_url.split("://", 1)
            message_id, image_key = payload.split("/", 1)
            resolved = await fetch_image_data_url(message_id, image_key)
            if not resolved:
                return {"confidence": 0.0, "reason": "feishu_image_fetch_failed"}
            image_url = resolved
        except Exception:
            return {"confidence": 0.0, "reason": "invalid_feishu_image_ref"}

    llm = get_llm(model=settings.vision_model)
    system = SystemMessage(
        content=(
            "分析图片，提取总金额、商户、分类。返回 JSON，"
            "字段: amount(float), merchant(str), category(str), item(str), confidence(float 0-1)."
        )
    )
    human = HumanMessage(
        content=[
            {"type": "text", "text": "请解析这张小票。"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    )

    response = await llm.ainvoke([system, human])
    try:
        data = json.loads(response.content)
        if not isinstance(data, dict):
            raise ValueError("invalid json")
        return data
    except Exception:
        return {"confidence": 0.0, "raw": response.content}
