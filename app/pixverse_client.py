import httpx
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)

# PixVerse via GMI Cloud
# Endpoint: POST https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey/requests
# Auth:     Authorization: Bearer <gmi_infra_api_key>
# Status:   GET  https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey/requests/{request_id}

GMI_VIDEO_BASE = "https://console.gmicloud.ai/api/v1/ie/requestqueue/apikey"


def _headers() -> dict:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.gmi_infra_api_key}",
        "Content-Type": "application/json",
    }


async def create_video(prompt: str) -> str:
    """Submit a PixVerse t2v job via GMI Cloud. Returns request_id."""
    payload = {
        "model": "pixverse-v5.6-t2v",
        "payload": {
            "prompt": prompt,
            "duration": "5",
            "quality": "540p",
            "aspect_ratio": "16:9",
        },
    }
    logger.info("PIXVERSE CREATE VIDEO | prompt_preview=%s", prompt[:100])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GMI_VIDEO_BASE}/requests",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id", "")
        if not request_id:
            raise ValueError(f"No request_id in response: {data}")
        logger.info("PIXVERSE JOB CREATED | request_id=%s | status=%s", request_id, data.get("status"))
        return request_id


async def create_i2v(prompt: str, image_url: str) -> str:
    """Submit a PixVerse image-to-video job anchored on a diagram image. Returns request_id."""
    payload = {
        "model": "pixverse-v5.6-i2v",
        "payload": {
            "image_url": image_url,
            "prompt": prompt,
            "duration": "8",
            "quality": "720p",
            "aspect_ratio": "16:9",
            "negative_prompt": (
                "extra nodes, new elements, hallucinated content, invented text, "
                "garbled text, distorted text, non-English text, foreign characters, "
                "fast motion, abrupt cut, blurry, flickering, pixelated, watermark"
            ),
        },
    }
    logger.info("PIXVERSE I2V | image=%s | prompt_preview=%s", image_url[:60], prompt[:80])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GMI_VIDEO_BASE}/requests",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id", "")
        if not request_id:
            raise ValueError(f"No request_id in response: {data}")
        logger.info("PIXVERSE I2V JOB CREATED | request_id=%s", request_id)
        return request_id


async def create_transition_video(prompt: str, first_frame_url: str, last_frame_url: str) -> str:
    """Submit a PixVerse image-to-image transition job. Returns request_id."""
    payload = {
        "model": "pixverse-v5.6-transition",
        "payload": {
            "prompt": prompt,
            "first_frame_image": first_frame_url,
            "last_frame_image": last_frame_url,
            "duration": "8",
            "quality": "720p",
            "negative_prompt": (
                "fast motion, abrupt cut, jump cut, flickering, strobing, "
                "blurry, low quality, text artifacts, pixelated, jerky, "
                "distorted, noisy, compressed artifacts, watermark"
            ),
        },
    }
    logger.info("PIXVERSE TRANSITION VIDEO | first=%s | last=%s", first_frame_url[:60], last_frame_url[:60])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GMI_VIDEO_BASE}/requests",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id", "")
        if not request_id:
            raise ValueError(f"No request_id in response: {data}")
        logger.info("PIXVERSE TRANSITION JOB CREATED | request_id=%s | status=%s", request_id, data.get("status"))
        return request_id


async def get_video_status(request_id: str) -> dict:
    """Poll GMI Cloud for PixVerse job status. Returns dict: status, url, done, failed."""
    logger.info("PIXVERSE STATUS POLL | request_id=%s", request_id)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GMI_VIDEO_BASE}/requests/{request_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "")
        outcome = data.get("outcome") or {}
        # GMI Cloud returns media_urls list
        media_urls = outcome.get("media_urls") or []
        url = (media_urls[0].get("url") if media_urls else None) or outcome.get("video_url")

        logger.info("PIXVERSE STATUS | request_id=%s | status=%s | url=%s", request_id, status, url)
        return {
            "status": status,
            "url": url,
            "done": status == "success",
            "failed": status in ("failed", "cancelled"),
            "raw": data,
        }
