from typing import TypedDict, List, Any

from app.schemas.unified import UnifiedMessage


class GraphState(TypedDict, total=False):
    user_id: int
    user_setup_stage: int
    message: UnifiedMessage
    responses: List[str]
    intent: str
    extra: dict[str, Any]
