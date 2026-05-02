import asyncio
import logging
import uuid
from app import store, hydradb_client, gmi_client, photon_client, pixverse_client
from app.gmi_client import classify_intent
from app.models import Incident, IncidentState, K8sContext
from app import diagram_client
from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Original checkout-api demo ────────────────────────────────────────────────
ALERT_SMS = (
    "🚨 P1: checkout-api 500s spiking.\n"
    "Current error rate: 18%.\n"
    "Recent deploy: checkout-api v2.8.1, 8 min ago.\n"
    "Reply TRIAGE to start."
)

# ── K8s scenarios ─────────────────────────────────────────────────────────────
K8S_SCENARIO_1 = {
    "service": "payment-service",
    "sms": (
        "🚨 K8s ALERT | payment-service | production\n"
        "Pod: payment-svc-7d8f9b-xkp2j\n"
        "Status: CrashLoopBackOff (restarts: 8)\n"
        "Exit Code: 137 (OOMKilled)\n"
        "Memory: 498Mi / 512Mi limit\n"
        "Node: gke-prod-nodepool-abc123\n"
        "Deploy: payment-svc v3.2.1 — 15 min ago\n"
        "Reply TRIAGE to investigate."
    ),
    "k8s": K8sContext(
        cluster="gke-prod-cluster",
        namespace="production",
        pod="payment-svc-7d8f9b-xkp2j",
        deployment="payment-svc",
        exit_code="137",
        error_type="OOMKilled",
        memory_usage="498Mi",
        memory_limit="512Mi",
        node="gke-prod-nodepool-abc123",
        recent_deploy="payment-svc v3.2.1 (15 min ago)",
    ),
}

K8S_SCENARIO_2 = {
    "service": "payment-service",
    "sms": (
        "🚨 K8s ALERT | payment-service | production\n"
        "Pod: payment-svc-9f4c2d-mnp8k\n"
        "Status: CrashLoopBackOff (restarts: 5)\n"
        "Exit Code: 137 (OOMKilled)\n"
        "Memory: 501Mi / 512Mi limit\n"
        "Node: gke-prod-nodepool-def456\n"
        "Deploy: payment-svc v3.2.3 — 10 min ago\n"
        "Reply TRIAGE to investigate."
    ),
    "k8s": K8sContext(
        cluster="gke-prod-cluster",
        namespace="production",
        pod="payment-svc-9f4c2d-mnp8k",
        deployment="payment-svc",
        exit_code="137",
        error_type="OOMKilled",
        memory_usage="501Mi",
        memory_limit="512Mi",
        node="gke-prod-nodepool-def456",
        recent_deploy="payment-svc v3.2.3 (10 min ago)",
    ),
}


async def trigger_alert(phone: str) -> Incident:
    """Create a new incident and send the initial P1 alert via Photon."""
    incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    incident = Incident(
        incident_id=incident_id,
        service="checkout-api",
        alert_text=ALERT_SMS,
        state=IncidentState.ALERT_SENT,
        phone=phone,
    )
    incident.add_event("alert_triggered", ALERT_SMS)
    store.save_incident(incident)

    await photon_client.send_sms(phone, ALERT_SMS)
    incident.add_event("alert_sms_sent", phone)

    # Persist initial state to HydraDB
    try:
        await hydradb_client.store_incident_timeline(incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (alert): %s", exc)

    logger.info("INCIDENT CREATED | id=%s | phone=%s", incident_id, phone)
    return incident


async def trigger_k8s_alert(phone: str, scenario: dict) -> Incident:
    """Create a K8s incident and send the alert SMS."""
    incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    incident = Incident(
        incident_id=incident_id,
        service=scenario["service"],
        alert_text=scenario["sms"],
        state=IncidentState.ALERT_SENT,
        phone=phone,
        k8s=scenario["k8s"],
    )
    incident.add_event("k8s_alert_triggered", scenario["sms"])
    store.save_incident(incident)

    await photon_client.send_sms(phone, scenario["sms"])
    incident.add_event("alert_sms_sent", phone)

    try:
        await hydradb_client.store_incident_timeline(incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (k8s alert): %s", exc)

    logger.info("K8S INCIDENT CREATED | id=%s | service=%s", incident_id, scenario["service"])
    return incident


async def handle_k8s_triage(incident: Incident) -> None:
    """K8s triage: query HydraDB, call GMI with K8s context. Route to manual triage or auto-resolution."""
    logger.info("AGENT K8S TRIAGE | incident=%s", incident.incident_id)
    incident.add_event("triage_requested")

    k8s_ctx = incident.k8s.model_dump() if incident.k8s else {}
    query = f"{incident.service} {k8s_ctx.get('error_type','')} {k8s_ctx.get('exit_code','')} OOMKilled CrashLoopBackOff"

    memories = []
    try:
        memories = await hydradb_client.query_memory(query, sub_tenant=store.get_k8s_session())
        logger.info("HYDRADB QUERY | returned %d chunks", len(memories))
    except Exception as exc:
        logger.warning("HydraDB query failed (k8s triage): %s", exc)

    try:
        result = await gmi_client.analyze_k8s_incident(incident.alert_text, k8s_ctx, memories)
    except Exception as exc:
        logger.error("GMI k8s analysis failed: %s", exc)
        await photon_client.send_sms(incident.phone, f"⚠️ Sentinel reasoning failed: {str(exc)[:100]}")
        return

    if result.get("needs_manual_triage"):
        incident.state = IncidentState.AWAITING_MANUAL_TRIAGE
        incident.add_event("awaiting_manual_triage")
        sms = result.get("sms_reply") or (
            f"No prior incident found for {incident.service} OOMKilled.\n\n"
            "Please describe what you're seeing and any triage steps you've taken. "
            "I'll store your findings for future incidents."
        )
    else:
        incident.state = IncidentState.RESOLUTION_SUGGESTED
        incident.add_event("resolution_suggested", result.get("matched_memory_id", ""))
        incident.gmi_analysis = gmi_client.GMIResponse(
            matched_memory=result.get("matched_memory_id", ""),
            recommended_action=result.get("resolution", ""),
        )
        sms = result.get("sms_reply") or (
            f"Found matching prior incident.\n"
            f"Root cause: {result.get('root_cause')}\n"
            f"Suggested fix:\n{result.get('resolution')}\n\n"
            "Reply 'yes' or 'apply' to confirm, or describe what you're doing."
        )

    await photon_client.send_sms(incident.phone, sms)
    incident.add_event("triage_sms_sent")

    try:
        await hydradb_client.store_incident_timeline(incident.incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (k8s triage): %s", exc)

    store.save_incident(incident)


async def handle_manual_triage_input(incident: Incident, raw_steps: str) -> None:
    """Engineer described their triage steps — extract, store to HydraDB, confirm."""
    logger.info("AGENT MANUAL TRIAGE INPUT | incident=%s", incident.incident_id)
    incident.manual_triage_steps = raw_steps
    incident.add_event("manual_triage_received", raw_steps[:80])

    k8s_ctx = incident.k8s.model_dump() if incident.k8s else {}

    try:
        extracted = await gmi_client.extract_triage_steps(raw_steps, k8s_ctx, incident.incident_id)
    except Exception as exc:
        logger.error("GMI extract steps failed: %s", exc)
        extracted = {"memory_text": raw_steps, "root_cause": "unknown", "fix_steps": []}

    memory_text = extracted.get("memory_text") or raw_steps

    try:
        await hydradb_client.store_memory("k8s_triage", {
            "incident_id": incident.incident_id,
            "service": incident.service,
            "error_type": k8s_ctx.get("error_type", ""),
            "exit_code": k8s_ctx.get("exit_code", ""),
            "root_cause": extracted.get("root_cause", ""),
            "fix_steps": extracted.get("fix_steps", []),
            "resolution_summary": extracted.get("resolution_summary", ""),
            "summary": memory_text,
            "text": memory_text,
        }, sub_tenant=store.get_k8s_session())
        logger.info("HYDRADB WRITE | k8s_triage stored | incident=%s", incident.incident_id)
    except Exception as exc:
        logger.error("HydraDB write failed (manual triage): %s", exc)

    incident.state = IncidentState.MANUAL_TRIAGE_STORED
    incident.add_event("manual_triage_stored", extracted.get("root_cause", ""))
    store.save_incident(incident)

    steps_formatted = "\n".join(f"• {s}" for s in extracted.get("fix_steps", [raw_steps]))
    confirm_sms = (
        f"Stored ✓\n"
        f"Root cause: {extracted.get('root_cause', 'recorded')}\n"
        f"Fix steps saved:\n{steps_formatted}\n\n"
        "Future incidents on this service will get this resolution automatically.\n"
        "Reply 'generate video' for an exec recap, or close the incident."
    )
    await photon_client.send_sms(incident.phone, confirm_sms)
    incident.add_event("manual_triage_confirm_sent")


async def handle_k8s_resolution_confirmed(incident: Incident) -> None:
    """Engineer confirmed the suggested resolution — generate K8s architecture video."""
    logger.info("AGENT K8S RESOLUTION CONFIRMED | incident=%s", incident.incident_id)
    incident.add_event("resolution_confirmed")
    incident.state = IncidentState.RECALLED

    resolution = incident.gmi_analysis.recommended_action if incident.gmi_analysis else "Resolution applied."
    k8s_ctx = incident.k8s.model_dump() if incident.k8s else {}

    await photon_client.send_sms(
        incident.phone,
        f"Resolution applied ✓\n{resolution}\n\nGenerating incident visualization video..."
    )
    await handle_generate_video(incident)


async def handle_triage(incident: Incident) -> None:
    """Engineer replied TRIAGE — query memory, call GMI, send hypotheses."""
    logger.info("AGENT TRIAGE | incident=%s", incident.incident_id)
    incident.add_event("triage_requested")

    # Query HydraDB for relevant memories
    memories = []
    try:
        memories = await hydradb_client.query_memory(
            "checkout-api 500s Redis connection pool exhaustion",
            filters={"service": "checkout-api"},
        )
        logger.info("HYDRADB QUERY | returned %d memories", len(memories))
    except Exception as exc:
        logger.warning("HydraDB query failed (triage): %s", exc)

    # GMI Cloud analysis
    try:
        analysis = await gmi_client.analyze_incident(incident.alert_text, memories)
    except Exception as exc:
        logger.error("GMI analysis failed: %s", exc)
        await photon_client.send_sms(
            incident.phone,
            "⚠️ Sentinel reasoning failed. Please triage manually.\nError: " + str(exc)[:100],
        )
        return

    incident.gmi_analysis = analysis
    incident.state = IncidentState.TRIAGED
    incident.add_event("triage_complete", f"matched={analysis.matched_memory}")

    sms = analysis.sms_reply or _default_triage_sms(analysis)
    await photon_client.send_sms(incident.phone, sms)
    incident.add_event("triage_sms_sent")

    try:
        await hydradb_client.store_incident_timeline(incident.incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (triage): %s", exc)

    store.save_incident(incident)


async def handle_recall(incident: Incident) -> None:
    """Engineer asked 'What did we do last time?' — recall mitigation from memory."""
    logger.info("AGENT RECALL | incident=%s", incident.incident_id)
    incident.add_event("recall_requested")

    # Pull INC-214 mitigation details from HydraDB
    memories = []
    try:
        memories = await hydradb_client.query_memory(
            "INC-214 mitigation steps Redis rollback canary",
            filters={"type": "prior_incident"},
        )
    except Exception as exc:
        logger.warning("HydraDB query failed (recall): %s", exc)

    mitigation_sms = _build_mitigation_sms(memories)
    await photon_client.send_sms(incident.phone, mitigation_sms)

    incident.state = IncidentState.RECALLED
    incident.add_event("recall_sms_sent")

    try:
        await hydradb_client.store_incident_timeline(incident.incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (recall): %s", exc)

    store.save_incident(incident)


async def handle_generate_video(incident: Incident) -> None:
    """Generate exec recap + PixVerse architecture video. K8s-aware when k8s context present."""
    logger.info("AGENT VIDEO | incident=%s | k8s=%s", incident.incident_id, bool(incident.k8s))
    incident.add_event("video_requested")

    k8s_ctx = incident.k8s.model_dump() if incident.k8s else {}
    triage_steps = incident.manual_triage_steps or (
        incident.gmi_analysis.recommended_action if incident.gmi_analysis else ""
    )
    resolution = (incident.gmi_analysis.recommended_action if incident.gmi_analysis else "") or triage_steps

    # Generate exec recap
    incident_summary = (
        f"Service: {incident.service}\n"
        f"Alert: {incident.alert_text}\n"
        f"K8s context: {k8s_ctx}\n"
        f"Triage/resolution: {triage_steps or 'engineer mitigated'}"
    )
    memories = []
    try:
        memories = await hydradb_client.query_memory(f"{incident.service} exec recap stakeholder")
    except Exception as exc:
        logger.warning("HydraDB query failed (recap): %s", exc)

    exec_recap = ""
    video_prompt = ""

    if incident.k8s:
        # K8s path: generate real architecture diagrams → PixVerse transition
        exec_recap = (
            f"{incident.service} pod ({k8s_ctx.get('pod','')}) entered CrashLoopBackOff "
            f"with OOMKilled (exit 137). Memory limit {k8s_ctx.get('memory_limit','')} was "
            f"exhausted after deploy {k8s_ctx.get('recent_deploy','')}. "
            f"Resolution: {resolution or 'engineer applied fix'}. "
            "Future incidents will be auto-resolved from memory."
        )
        incident.exec_recap = exec_recap

        recap_sms = (
            "Incident resolved ✓\n\n"
            f"Exec recap:\n{exec_recap}\n\n"
            "Generating incident recap video with PixVerse..."
        )
        await photon_client.send_sms(incident.phone, recap_sms)
        incident.add_event("recap_sms_sent", exec_recap[:80])
        incident.state = IncidentState.RECAP_SENT
        store.save_incident(incident)

        # Build both diagrams — error (red) and resolved (green)
        error_diagram_url = diagram_client.mermaid_to_url(
            diagram_client.build_error_diagram(k8s_ctx)
        )
        resolved_diagram_url = diagram_client.mermaid_to_url(
            diagram_client.build_resolved_diagram(k8s_ctx, resolution or triage_steps)
        )
        logger.info("DIAGRAMS | error=%s | resolved=%s", error_diagram_url[:60], resolved_diagram_url[:60])

        # Short, focused transition prompt
        svc = incident.service
        video_prompt = (
            f"Kubernetes incident recovery for {svc}. "
            f"Animate from the error state diagram to the resolved state diagram. "
            f"Red nodes turn green. CrashLoopBackOff pod recovers to Running and healthy. "
            f"Slow, smooth transition. Dark background. All text in English, crisp and readable. "
            f"Professional enterprise ops style. Do not add any new elements."
        )
        logger.info("VIDEO PROMPT | %s", video_prompt)

        # Submit to PixVerse transition — error diagram → resolved diagram
        try:
            job_id = await pixverse_client.create_transition_video(video_prompt, error_diagram_url, resolved_diagram_url)
        except Exception as exc:
            logger.error("PixVerse i2v failed: %s", exc)
            await photon_client.send_sms(incident.phone, f"⚠️ Video generation failed: {str(exc)[:120]}")
            return

        incident.pixverse_job_id = job_id
        incident.state = IncidentState.VIDEO_GENERATING
        incident.add_event("pixverse_transition_job_created", job_id)
        store.register_pixverse_job(job_id, incident.incident_id)

        await photon_client.send_sms(
            incident.phone,
            f"PixVerse video job started: {job_id}\nI'll send the link as soon as it's ready."
        )
        incident.add_event("pixverse_job_sms_sent", job_id)

        try:
            await hydradb_client.store_incident_timeline(incident.incident_id, incident.model_dump())
        except Exception as exc:
            logger.warning("HydraDB write failed (k8s video): %s", exc)

        store.save_incident(incident)
        asyncio.create_task(_poll_until_ready(incident, job_id))
        return  # K8s path exits here

    else:
        # Original checkout-api path — t2v with generated prompt
        try:
            recap_data = await gmi_client.generate_recap_and_video_prompt(incident_summary, memories)
            exec_recap = recap_data.exec_recap or _default_exec_recap()
            video_prompt = recap_data.video_prompt or _default_video_prompt()
        except Exception as exc:
            logger.error("GMI recap generation failed: %s", exc)
            await photon_client.send_sms(incident.phone, "⚠️ Sentinel failed to generate exec recap.\nError: " + str(exc)[:100])
            return

        incident.exec_recap = exec_recap
        recap_sms = (
            "Mitigation recorded.\n\n"
            f"Exec recap:\n{exec_recap}\n\n"
            "Generating live PixVerse video now..."
        )
        await photon_client.send_sms(incident.phone, recap_sms)
        incident.add_event("recap_sms_sent", exec_recap[:80])
        incident.state = IncidentState.RECAP_SENT
        store.save_incident(incident)

        try:
            job_id = await pixverse_client.create_video(video_prompt)
        except Exception as exc:
            logger.error("PixVerse create video failed: %s", exc)
            await photon_client.send_sms(incident.phone, f"⚠️ PixVerse video generation failed: {str(exc)[:120]}\nRecap text has been saved.")
            return

        incident.pixverse_job_id = job_id
        incident.state = IncidentState.VIDEO_GENERATING
        incident.add_event("pixverse_job_created", job_id)
        store.register_pixverse_job(job_id, incident.incident_id)

        await photon_client.send_sms(incident.phone, f"PixVerse job started: {job_id}\nI'll send the video link as soon as it's ready.")
        incident.add_event("pixverse_job_sms_sent", job_id)
        asyncio.create_task(_poll_until_ready(incident, job_id))

    try:
        await hydradb_client.store_incident_timeline(incident.incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (video): %s", exc)

    store.save_incident(incident)


async def handle_video_status(incident: Incident) -> None:
    """Engineer asked VIDEO STATUS — poll PixVerse and report real status."""
    if not incident.pixverse_job_id:
        await photon_client.send_sms(incident.phone, "No active PixVerse job found for this incident.")
        return

    logger.info("AGENT VIDEO STATUS | job_id=%s", incident.pixverse_job_id)
    try:
        result = await pixverse_client.get_video_status(incident.pixverse_job_id)
    except Exception as exc:
        logger.error("PixVerse status poll failed: %s", exc)
        await photon_client.send_sms(
            incident.phone,
            f"⚠️ Could not reach PixVerse: {str(exc)[:100]}",
        )
        return

    status = result.get("status", "unknown")
    video_url = result.get("url")

    if video_url and status in ("success", "completed", "finished"):
        await _deliver_video(incident, video_url)
    else:
        status_sms = f"PixVerse status: {status}. Job ID: {incident.pixverse_job_id}."
        await photon_client.send_sms(incident.phone, status_sms)
        incident.add_event("pixverse_status_checked", status)
        store.save_incident(incident)


async def _poll_until_ready(incident: Incident, job_id: str, interval: int = 15, max_attempts: int = 40) -> None:
    """Background task: poll PixVerse every `interval` seconds and deliver when ready."""
    logger.info("PIXVERSE POLL START | job_id=%s | interval=%ds", job_id, interval)
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(interval)
        try:
            result = await pixverse_client.get_video_status(job_id)
        except Exception as exc:
            logger.warning("PIXVERSE POLL ERROR | attempt=%d | %s", attempt, exc)
            continue

        status = result.get("status", "")
        logger.info("PIXVERSE POLL | attempt=%d | job_id=%s | status=%s", attempt, job_id, status)

        if result.get("done") and result.get("url"):
            await _deliver_video(incident, result["url"])
            return

        if result.get("failed"):
            logger.error("PIXVERSE JOB FAILED | job_id=%s | status=%s", job_id, status)
            await photon_client.send_sms(
                incident.phone,
                f"⚠️ PixVerse video generation failed (status: {status}).\nRecap text has been saved."
            )
            return

    logger.error("PIXVERSE POLL TIMEOUT | job_id=%s after %d attempts", job_id, max_attempts)
    await photon_client.send_sms(
        incident.phone,
        f"⚠️ PixVerse video timed out after {max_attempts * interval}s.\nJob ID: {job_id}"
    )


async def _deliver_video(incident: Incident, video_url: str) -> None:
    """Video is ready — send the real URL via Photon."""
    incident.pixverse_video_url = video_url
    incident.state = IncidentState.VIDEO_COMPLETE
    incident.add_event("pixverse_video_ready", video_url)

    sms = f"Exec recap video ready:\n{video_url}"
    await photon_client.send_sms(incident.phone, sms)
    incident.add_event("video_url_sms_sent", video_url)

    try:
        await hydradb_client.store_incident_timeline(incident.incident_id, incident.model_dump())
    except Exception as exc:
        logger.warning("HydraDB write failed (video complete): %s", exc)

    store.save_incident(incident)
    logger.info("PIXVERSE VIDEO DELIVERED | job_id=%s | url=%s", incident.pixverse_job_id, video_url)


async def handle_save_followup(incident: Incident, follow_up_text: str) -> None:
    """Engineer sent a follow-up to save — write to HydraDB and confirm."""
    logger.info("AGENT SAVE FOLLOWUP | incident=%s | text=%s", incident.incident_id, follow_up_text[:60])
    incident.add_event("followup_requested", follow_up_text)

    # GMI to distill the follow-up into a clean memory sentence
    try:
        memory_sentence = await gmi_client.generate_memory_update(follow_up_text, incident.incident_id)
    except Exception as exc:
        logger.warning("GMI memory update failed: %s", exc)
        memory_sentence = follow_up_text

    try:
        await hydradb_client.store_memory("follow_up", {
            "incident_id": incident.incident_id,
            "service": incident.service,
            "summary": memory_sentence,
            "raw_input": follow_up_text,
        })
        logger.info("HYDRADB WRITE | follow_up saved | incident=%s", incident.incident_id)
    except Exception as exc:
        logger.error("HydraDB write failed (follow-up): %s", exc)
        await photon_client.send_sms(
            incident.phone,
            f"⚠️ Could not save follow-up to memory: {str(exc)[:100]}",
        )
        return

    incident.state = IncidentState.COMPLETE
    incident.add_event("followup_saved", memory_sentence)
    store.save_incident(incident)

    confirm_sms = (
        "Saved. Future checkout-api deploy incidents will check Redis saturation "
        "before rollback recommendations."
    )
    await photon_client.send_sms(incident.phone, confirm_sms)


async def dispatch_inbound(from_number: str, body: str) -> None:
    """Route an inbound SMS using LLM intent detection — no exact string matching."""
    logger.info("PHOTON INBOUND | from=%s | body=%s", from_number, body[:80])

    incident = store.get_incident_by_phone(from_number)
    if not incident:
        logger.warning("No active incident for phone=%s", from_number)
        await photon_client.send_sms(
            from_number,
            "No active incident found. Use POST /fake-alert to start a new scenario.",
        )
        return

    # LLM classifies free-form message into a structured intent
    intent = await classify_intent(body, incident.state.value)

    if intent == "TRIAGE" and incident.state == IncidentState.ALERT_SENT:
        # Route to K8s triage if incident has K8s context
        if incident.k8s:
            await handle_k8s_triage(incident)
        else:
            await handle_triage(incident)

    elif intent == "PROVIDE_STEPS" and incident.state == IncidentState.AWAITING_MANUAL_TRIAGE:
        await handle_manual_triage_input(incident, body)

    elif intent == "CONFIRM" and incident.state == IncidentState.RESOLUTION_SUGGESTED:
        await handle_k8s_resolution_confirmed(incident)

    elif intent == "RECALL" and incident.state == IncidentState.TRIAGED:
        await handle_recall(incident)

    elif intent == "VIDEO" and incident.state in (
        IncidentState.RECALLED, IncidentState.TRIAGED,
        IncidentState.MANUAL_TRIAGE_STORED, IncidentState.RESOLUTION_SUGGESTED,
    ):
        await handle_generate_video(incident)

    elif intent == "VIDEO_STATUS" and incident.state in (
        IncidentState.VIDEO_GENERATING, IncidentState.RECAP_SENT
    ):
        await handle_video_status(incident)

    elif intent == "SAVE_FOLLOWUP":
        text = body
        for prefix in ("save follow-up:", "save followup:", "save:", "follow-up:", "followup:"):
            if body.lower().startswith(prefix):
                text = body[len(prefix):].strip()
                break
        await handle_save_followup(incident, text)

    else:
        logger.info("UNMATCHED INTENT | state=%s | intent=%s | body=%s", incident.state, intent, body[:60])
        await photon_client.send_sms(
            from_number,
            f"Got it (state: {incident.state.value}). "
            "Try: TRIAGE · confirm resolution · generate video · VIDEO STATUS · save follow-up",
        )


# ── Fallback text builders ──────────────────────────────────────────────────

def _default_triage_sms(analysis) -> str:
    hyps = "\n".join(
        f"{h.rank}. {h.name} — {h.reason}" for h in analysis.hypotheses
    )
    return (
        "Top hypotheses from incident memory:\n\n"
        f"{hyps}\n\n"
        f"Most likely: #1 because Redis latency and connection timeout errors match March.\n"
        'Ask: "What did we do last time?" to recall the previous mitigation.'
    )


def _build_mitigation_sms(memories: list) -> str:
    # Look for INC-214 in returned memories
    for mem in memories:
        if "INC-214" in str(mem.get("id", "")) or "INC-214" in str(mem.get("summary", "")):
            mitigation = mem.get("mitigation", "")
            if mitigation:
                steps = mitigation.replace(", ", "\n- ")
                return (
                    "Incident #214 mitigation:\n"
                    f"- {steps}\n\n"
                    "Suggested first action: rollback canary to 10%."
                )

    # Fallback to baked-in knowledge when HydraDB returns empty
    return (
        "Incident #214 mitigation:\n"
        "- rollback canary from 50% to 10%\n"
        "- increase Redis max connections from 100 → 300\n"
        "- restart checkout-api workers\n"
        "- monitor checkout_5xx_rate for 10 min\n\n"
        "Suggested first action: rollback canary to 10%."
    )


def _default_k8s_video_prompt(k8s_ctx: dict, triage_steps: str) -> str:
    pod = k8s_ctx.get("pod", "payment-svc-pod")
    ns = k8s_ctx.get("namespace", "production")
    cluster = k8s_ctx.get("cluster", "gke-prod-cluster")
    mem_limit = k8s_ctx.get("memory_limit", "512Mi")
    mem_used = k8s_ctx.get("memory_usage", "498Mi")
    exit_code = k8s_ctx.get("exit_code", "137")
    deploy = k8s_ctx.get("recent_deploy", "recent deploy")
    return (
        f"Create a 7-second dark-theme technical incident visualization video. "
        f"Scene 1 (0-1.5s): K8s cluster architecture diagram. Show {cluster} with node graph. "
        f"Namespace '{ns}' glows red. Pod '{pod}' pulses with red CrashLoopBackOff badge. "
        f"Scene 2 (1.5-3s): Zoom into pod. Memory gauge fills to {mem_used}/{mem_limit}. "
        f"Red flash: OOMKilled | Exit {exit_code}. Pod icon shatters and respawns in loop. "
        f"Scene 3 (3-4.5s): SRE terminal view. kubectl logs command. "
        f"Log line highlighted: java.lang.OutOfMemoryError. Memory profile graph spiking. "
        f"Scene 4 (4.5-6s): Resolution animation. kubectl patch command on screen — "
        f"memory limit increasing. Rolling restart: pods turn green one by one. "
        f"Scene 5 (6-7s): Cluster overview — all pods green. "
        f"Overlay text: Resolved | Root cause: OOMKilled | Deploy: {deploy}. "
        "Style: dark cyber enterprise ops, node graph aesthetic, clean technical diagrams."
    )


def _default_exec_recap() -> str:
    return (
        "Checkout errors spiked after the v2.8.1 deploy. "
        "Sentinel matched the pattern to March incident #214: Redis connection pool exhaustion. "
        "Canary traffic was reduced and checkout stabilized. "
        "Follow-up: add Redis saturation checks before checkout deploys."
    )


def _default_video_prompt() -> str:
    return (
        "Create a 5-8 second executive incident recap video in a clean isometric SaaS style. "
        "Scene 1: checkout-api deploy starts — green deploy pipeline, version badge v2.8.1. "
        "Scene 2: error rate graph spikes to 18% — red alert overlay. "
        "Scene 3: memory graph highlights prior incident INC-214 — glowing archive node. "
        "Scene 4: engineer rolls back canary traffic from 50% to 10% — traffic flow animation. "
        "Scene 5: dashboard turns green with follow-up action text: Redis saturation check. "
        "Style: professional, calm, executive-friendly, polished modern enterprise operations briefing."
    )
