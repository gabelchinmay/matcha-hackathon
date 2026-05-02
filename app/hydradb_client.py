import httpx
import logging
import json
from typing import Any, Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

# HydraDB API: https://api.hydradb.com
# Auth: Authorization: Bearer <api_key>
# Tenant model: tenant_id (= project_id) + sub_tenant_id
# Store:  POST /memories/add_memory  — body.memories[].text
# Recall: POST /recall/recall_preferences — returns body.chunks[]


def _headers() -> dict:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.hydradb_api_key}",
        "Content-Type": "application/json",
    }


def _tenant(settings, sub_tenant: str = "sentinel") -> dict:
    return {
        "tenant_id": settings.hydradb_project_id,
        "sub_tenant_id": sub_tenant,
    }


async def ensure_tenant() -> None:
    """Create the tenant if it doesn't exist yet (idempotent)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{settings.hydradb_base_url}/tenants/create",
            json={"tenant_id": settings.hydradb_project_id},
            headers=_headers(),
        )
        # 200 = created/accepted, 409 = already exists — both are fine
        if r.status_code not in (200, 409):
            logger.warning("HYDRADB TENANT CREATE unexpected status=%s body=%s", r.status_code, r.text[:200])
        else:
            logger.info("HYDRADB TENANT OK | status=%s", r.status_code)


async def store_memory(memory_type: str, content: dict, sub_tenant: str = "sentinel") -> dict:
    settings = get_settings()
    memory_text = json.dumps({**content, "type": memory_type}, ensure_ascii=False)
    payload = {
        **_tenant(settings, sub_tenant),
        "memories": [{"text": memory_text}],
    }
    label = content.get("id") or content.get("service") or content.get("team") or memory_type
    logger.info("HYDRADB WRITE | type=%s | label=%s", memory_type, label)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.hydradb_base_url}/memories/add_memory",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("HYDRADB WRITE OK | label=%s | queued=%s", label, data.get("success"))
        return data


async def query_memory(query: str, filters: Optional[dict] = None, top_k: int = 5, sub_tenant: str = "sentinel") -> list[dict]:
    settings = get_settings()
    payload: dict[str, Any] = {
        **_tenant(settings, sub_tenant),
        "query": query,
    }
    logger.info("HYDRADB QUERY | query=%s", query[:80])

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.hydradb_base_url}/recall/recall_preferences",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        # Response: {"chunks": [{"chunk_content": "...", "relevancy_score": ...}], ...}
        chunks = data.get("chunks") or []
        results = [{"summary": c.get("chunk_content", ""), "score": c.get("relevancy_score")} for c in chunks]
        logger.info("HYDRADB QUERY OK | returned %d chunks", len(results))
        return results


async def store_incident_timeline(incident_id: str, incident_dict: dict) -> dict:
    logger.info("HYDRADB WRITE | incident_timeline | id=%s", incident_id)
    return await store_memory("incident_timeline", {"id": incident_id, "data": incident_dict})
