import httpx
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)

# Photon bridge runs on localhost:3001 (photon-bridge/bridge.js).
# FastAPI calls POST /send → bridge forwards to Spectrum SDK → iMessage/SMS.
# Inbound messages arrive at POST /sms/inbound via the bridge forwarding them.


async def send_sms(to: str, body: str) -> dict:
    settings = get_settings()
    bridge_url = getattr(settings, "photon_bridge_url", "http://localhost:3001")
    payload = {"to": to, "body": body}
    logger.info("PHOTON OUTBOUND | to=%s | body=%s", to, body[:80])

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{bridge_url}/send",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("PHOTON OUTBOUND OK | to=%s", to)
        return data
