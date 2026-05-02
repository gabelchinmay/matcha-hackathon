import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import FakeAlertRequest, PhotonInbound, PixVerseWebhook
from app import agent, store, pixverse_client, hydradb_client
from app.agent import K8S_SCENARIO_1, K8S_SCENARIO_2
from app.memory_seed import seed_hydradb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Sentinel starting up")
    try:
        await hydradb_client.ensure_tenant()
    except Exception as exc:
        logger.warning("HydraDB tenant ensure failed at startup: %s", exc)
    yield
    logger.info("Sentinel shutting down")


app = FastAPI(
    title="Sentinel",
    description="SMS-native incident command agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "service": "sentinel",
        "config": {
            "gmi_base_url": settings.gmi_base_url,
            "hydradb_base_url": settings.hydradb_base_url,
            "pixverse_base_url": settings.pixverse_base_url,
            "photon_base_url": settings.photon_base_url,
            "on_call_phone": settings.on_call_phone,
        },
    }


@app.post("/seed-memory")
async def seed_memory():
    """Seed HydraDB with the demo incident memories (INC-214, runbooks, profiles)."""
    logger.info("SEED MEMORY requested")
    results = await seed_hydradb()
    ok = sum(1 for r in results if r["status"] == "ok")
    return {"seeded": ok, "total": len(results), "results": results}


@app.post("/fake-alert")
async def fake_alert(body: FakeAlertRequest = FakeAlertRequest()):
    """Trigger the demo P1 alert — sends a real Photon SMS to the on-call engineer."""
    settings = get_settings()
    phone = body.phone or settings.on_call_phone
    if not phone:
        raise HTTPException(400, "on_call_phone not configured and no phone in request body")

    logger.info("FAKE ALERT triggered for phone=%s", phone)
    try:
        incident = await agent.trigger_alert(phone)
    except Exception as exc:
        logger.error("fake-alert failed: %s", exc)
        raise HTTPException(500, f"Alert trigger failed: {exc}") from exc

    return {
        "incident_id": incident.incident_id,
        "state": incident.state,
        "phone": phone,
        "sms_sent": incident.alert_text,
    }


@app.post("/sms/inbound")
async def sms_inbound(request: Request, background_tasks: BackgroundTasks):
    """Photon inbound webhook — receives engineer SMS replies and dispatches to agent."""
    # Accept both JSON and form-encoded (Twilio-style) payloads
    # TODO: Confirm Photon webhook payload format (JSON vs form-encoded)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    logger.info("PHOTON INBOUND WEBHOOK | raw=%s", str(data)[:200])

    # Normalize field names
    # TODO: Confirm Photon field names: "From" vs "from" vs "sender"
    from_number = (
        data.get("from") or data.get("From") or data.get("sender") or data.get("from_number", "")
    )
    body = data.get("body") or data.get("Body") or data.get("text") or data.get("message", "")

    if not from_number or not body:
        logger.warning("Inbound webhook missing from/body: %s", data)
        return JSONResponse({"status": "ignored", "reason": "missing from or body"})

    background_tasks.add_task(agent.dispatch_inbound, from_number, body)
    return JSONResponse({"status": "accepted"})


@app.post("/pixverse/webhook")
async def pixverse_webhook(payload: PixVerseWebhook, background_tasks: BackgroundTasks):
    """Optional PixVerse callback webhook for when video completes."""
    # TODO: Confirm PixVerse webhook payload schema and auth header
    logger.info("PIXVERSE WEBHOOK | job_id=%s | status=%s | url=%s",
                payload.task_id, payload.status, payload.url)

    incident = store.get_incident_by_pixverse_job(payload.task_id)
    if not incident:
        logger.warning("No incident found for PixVerse job_id=%s", payload.task_id)
        return {"status": "no_incident"}

    if payload.url and payload.status in ("success", "completed", "finished"):
        background_tasks.add_task(agent._deliver_video, incident, payload.url)

    return {"status": "accepted"}


@app.get("/pixverse/status/{job_id}")
async def pixverse_status(job_id: str):
    """Manually poll PixVerse status for a job ID — useful for debugging."""
    try:
        result = await pixverse_client.get_video_status(job_id)
    except Exception as exc:
        raise HTTPException(502, f"PixVerse poll failed: {exc}") from exc

    incident = store.get_incident_by_pixverse_job(job_id)
    return {
        "job_id": job_id,
        "pixverse": result,
        "incident_id": incident.incident_id if incident else None,
    }


@app.get("/incident/{incident_id}")
async def get_incident(incident_id: str):
    """Return full incident state — for judge demo / debugging."""
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")
    return incident.model_dump()


@app.post("/reset-k8s-demo")
async def reset_k8s_demo():
    """Full demo reset: new HydraDB session (Round 1 sees no memory) + clear in-memory incidents.
    Round 1 → provides steps → stored in new session.
    Round 2 → same session → finds Round 1 memory automatically."""
    result = store.reset_k8s_demo()
    return result


@app.post("/fakeerror1")
async def fake_k8s_error_1(body: FakeAlertRequest = FakeAlertRequest()):
    """Trigger K8s scenario 1 — payment-service OOMKilled (no prior memory, will ask for manual triage)."""
    settings = get_settings()
    phone = body.phone or settings.on_call_phone
    if not phone:
        raise HTTPException(400, "on_call_phone not configured")
    try:
        incident = await agent.trigger_k8s_alert(phone, K8S_SCENARIO_1)
    except Exception as exc:
        raise HTTPException(500, f"Alert trigger failed: {exc}") from exc
    return {"incident_id": incident.incident_id, "state": incident.state, "scenario": "k8s_scenario_1"}


@app.post("/fakeerror2")
async def fake_k8s_error_2(body: FakeAlertRequest = FakeAlertRequest()):
    """Trigger K8s scenario 2 — same OOMKilled pattern (memory exists, auto-resolution suggested)."""
    settings = get_settings()
    phone = body.phone or settings.on_call_phone
    if not phone:
        raise HTTPException(400, "on_call_phone not configured")
    try:
        incident = await agent.trigger_k8s_alert(phone, K8S_SCENARIO_2)
    except Exception as exc:
        raise HTTPException(500, f"Alert trigger failed: {exc}") from exc
    return {"incident_id": incident.incident_id, "state": incident.state, "scenario": "k8s_scenario_2"}


@app.get("/incidents")
async def list_incidents():
    """List all in-memory incidents."""
    return [i.model_dump() for i in store.list_incidents()]
