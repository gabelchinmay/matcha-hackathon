import asyncio
import logging
from app.hydradb_client import store_memory

logger = logging.getLogger(__name__)

SEED_MEMORIES = [
    {
        "type": "prior_incident",
        "id": "INC-214",
        "service": "checkout-api",
        "summary": "March incident: checkout-api 500s caused by Redis connection pool exhaustion after canary deploy.",
        "mitigation": "Rollback canary to 10%, increase Redis max connections from 100 to 300, restart workers.",
        "impact": "18% checkout failures for 11 minutes.",
    },
    {
        "type": "runbook",
        "service": "checkout-api",
        "summary": (
            "For checkout-api 500s after deploy, check deploy version, Redis latency, "
            "payment provider latency, and error logs."
        ),
    },
    {
        "type": "team_preference",
        "team": "commerce-platform",
        "summary": (
            "For P1 incidents, send concise executive updates with impact, cause, "
            "mitigation, and follow-up."
        ),
    },
    {
        "type": "service_profile",
        "service": "checkout-api",
        "summary": (
            "checkout-api depends on Redis session store and payment-gateway. "
            "Redis saturation often appears as connection timeout errors."
        ),
    },
    {
        "type": "stakeholder_profile",
        "audience": "executives",
        "summary": "Executives prefer non-technical summaries under 5 sentences.",
    },
]


async def seed_hydradb() -> list[dict]:
    results = []
    for mem in SEED_MEMORIES:
        mem_type = mem["type"]
        try:
            result = await store_memory(mem_type, mem)
            results.append({"status": "ok", "type": mem_type, "id": mem.get("id", mem.get("service", mem.get("team", "?")))})
            logger.info("SEED | stored type=%s", mem_type)
        except Exception as exc:
            logger.error("SEED | failed type=%s | error=%s", mem_type, exc)
            results.append({"status": "error", "type": mem_type, "error": str(exc)})
    return results


if __name__ == "__main__":
    asyncio.run(seed_hydradb())
