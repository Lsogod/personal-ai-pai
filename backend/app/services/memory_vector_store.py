from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.models.memory import LongTermMemory

logger = logging.getLogger(__name__)


def _datetime_to_ts(value: datetime | None) -> int:
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def milvus_enabled() -> bool:
    settings = get_settings()
    return bool(settings.milvus_enabled and str(settings.milvus_uri or "").strip())


def _is_local_milvus_uri(uri: str) -> bool:
    raw = str(uri or "").strip().lower()
    if not raw:
        return False
    return not raw.startswith(("http://", "https://", "tcp://", "grpc://"))


@lru_cache
def _get_milvus_client() -> Any:
    settings = get_settings()
    from pymilvus import MilvusClient

    kwargs: dict[str, Any] = {"uri": settings.milvus_uri}
    if str(settings.milvus_token or "").strip():
        kwargs["token"] = settings.milvus_token
    return MilvusClient(**kwargs)


def _build_filter(*, user_id: int, now_ts: int) -> str:
    return f"user_id == {int(user_id)} && is_active == true && (expires_at_ts == 0 || expires_at_ts > {int(now_ts)})"


async def ensure_memory_vector_collection() -> None:
    if not milvus_enabled():
        return

    settings = get_settings()
    client = _get_milvus_client()
    if client.has_collection(collection_name=settings.milvus_collection):
        return

    from pymilvus import DataType

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="memory_id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="user_id", datatype=DataType.INT64)
    schema.add_field(field_name="memory_type", datatype=DataType.VARCHAR, max_length=40)
    schema.add_field(field_name="memory_key", datatype=DataType.VARCHAR, max_length=160)
    schema.add_field(field_name="importance", datatype=DataType.INT16)
    schema.add_field(field_name="confidence", datatype=DataType.FLOAT)
    schema.add_field(field_name="is_active", datatype=DataType.BOOL)
    schema.add_field(field_name="expires_at_ts", datatype=DataType.INT64)
    schema.add_field(field_name="updated_at_ts", datatype=DataType.INT64)
    schema.add_field(field_name="raw_text", datatype=DataType.VARCHAR, max_length=2048)
    schema.add_field(
        field_name="dense_text",
        datatype=DataType.FLOAT_VECTOR,
        dim=max(1, int(settings.memory_embedding_dim or 1536)),
    )
    schema.add_field(field_name="embedding_version", datatype=DataType.INT16)
    schema.add_field(field_name="content_hash", datatype=DataType.VARCHAR, max_length=64)

    index_params = client.prepare_index_params()
    if _is_local_milvus_uri(settings.milvus_uri):
        index_params.add_index(
            field_name="dense_text",
            index_name="idx_dense_text",
            index_type="AUTOINDEX",
            metric_type="COSINE",
            params={},
        )
    else:
        index_params.add_index(
            field_name="dense_text",
            index_name="idx_dense_text",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )

    client.create_collection(
        collection_name=settings.milvus_collection,
        schema=schema,
        index_params=index_params,
    )
    load_collection = getattr(client, "load_collection", None)
    if callable(load_collection):
        load_collection(collection_name=settings.milvus_collection)


def _row_to_doc(row: LongTermMemory, vector: list[float]) -> dict[str, Any]:
    return {
        "memory_id": int(row.id or 0),
        "user_id": int(row.user_id or 0),
        "memory_type": str(row.memory_type or "fact"),
        "memory_key": str(row.memory_key or ""),
        "importance": int(row.importance or 3),
        "confidence": float(row.confidence or 0.0),
        "is_active": bool(getattr(row, "is_active", True)),
        "expires_at_ts": _datetime_to_ts(getattr(row, "expires_at", None)),
        "updated_at_ts": _datetime_to_ts(getattr(row, "updated_at", None)),
        "raw_text": str(row.content or "")[:2048],
        "dense_text": vector,
        "embedding_version": int(get_settings().memory_vector_version or 1),
        "content_hash": str(getattr(row, "vector_text_hash", "") or ""),
    }


async def upsert_memory_vectors(rows: list[LongTermMemory], vectors: list[list[float]]) -> None:
    if not milvus_enabled() or not rows:
        return
    if len(rows) != len(vectors):
        raise ValueError("rows and vectors length mismatch")

    settings = get_settings()
    expected_dim = max(1, int(settings.memory_embedding_dim or 1536))
    data: list[dict[str, Any]] = []
    for row, vector in zip(rows, vectors, strict=False):
        if row.id is None:
            continue
        if len(vector) != expected_dim:
            raise ValueError(
                f"embedding dim mismatch for memory_id={row.id}: expected {expected_dim}, got {len(vector)}"
            )
        data.append(_row_to_doc(row, vector))
    if not data:
        return

    await ensure_memory_vector_collection()
    client = _get_milvus_client()
    client.upsert(collection_name=settings.milvus_collection, data=data)


async def delete_memory_vectors(memory_ids: list[int]) -> None:
    if not milvus_enabled():
        return
    ids = [int(memory_id) for memory_id in memory_ids if int(memory_id) > 0]
    if not ids:
        return
    settings = get_settings()
    await ensure_memory_vector_collection()
    client = _get_milvus_client()
    client.delete(collection_name=settings.milvus_collection, ids=ids)


async def search_memory_vectors(
    *,
    user_id: int,
    query_vector: list[float],
    limit: int,
) -> list[dict[str, Any]]:
    if not milvus_enabled():
        return []

    settings = get_settings()
    await ensure_memory_vector_collection()
    client = _get_milvus_client()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    raw = client.search(
        collection_name=settings.milvus_collection,
        data=[query_vector],
        anns_field="dense_text",
        limit=max(1, limit),
        filter=_build_filter(user_id=user_id, now_ts=now_ts),
        output_fields=[
            "memory_id",
            "memory_key",
            "memory_type",
            "importance",
            "confidence",
            "updated_at_ts",
            "content_hash",
        ],
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
    )
    hits = raw[0] if raw else []
    result: list[dict[str, Any]] = []
    for item in hits:
        entity = dict(item.get("entity") or {})
        entity["score"] = float(item.get("distance") or item.get("score") or 0.0)
        result.append(entity)
    return result


try:
    from pymilvus import MilvusClient
except Exception:  # pragma: no cover - optional at import time during bootstrap
    MilvusClient = None  # type: ignore[assignment]
