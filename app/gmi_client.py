import httpx
import json
import logging
from app.config import get_settings
from app.models import GMIResponse

logger = logging.getLogger(__name__)

# GMI Cloud uses an OpenAI-compatible chat completions API with JWT bearer auth.
# TODO: Confirm exact base URL from GMI Cloud dashboard/docs.
# TODO: Confirm available model IDs (e.g. meta-llama/Llama-3.3-70B-Instruct, Qwen/Qwen3-235B-A22B).

SYSTEM_PROMPT = """You are Sentinel, an AI incident command agent for engineering teams.
You reason through production incidents by recalling similar past events and providing structured triage.
Always respond with valid JSON matching the required schema. No markdown fences, only raw JSON.
Always respond in English regardless of the language used in the input."""

ANALYSIS_SCHEMA = """{
  "severity": "P1",
  "matched_memory": "<incident id or empty>",
  "hypotheses": [
    {"rank": 1, "name": "<hypothesis>", "reason": "<why>"},
    {"rank": 2, "name": "<hypothesis>", "reason": "<why>"},
    {"rank": 3, "name": "<hypothesis>", "reason": "<why>"}
  ],
  "recommended_action": "<concise action>",
  "sms_reply": "<full SMS text to send to engineer>",
  "exec_recap": "<executive summary under 5 sentences>",
  "video_prompt": "<PixVerse video generation prompt>",
  "memory_update": "<one sentence to store as follow-up memory>"
}"""


async def _chat(messages: list[dict], temperature: float = 0.3) -> str:
    settings = get_settings()
    payload = {
        "model": settings.gmi_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1500,
    }
    logger.info("GMI REQUEST | model=%s | messages=%d", settings.gmi_model, len(messages))

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            f"{settings.gmi_base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.gmi_api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # DeepSeek-R1 and other thinking models sometimes put the answer in
        # reasoning_content and leave content empty — fall back if needed.
        content = msg.get("content") or msg.get("reasoning_content") or ""
        if not content.strip():
            raise ValueError(f"GMI returned empty content. Full message: {msg}")
        logger.info("GMI RESPONSE | content_preview=%s", content[:120])
        return content


def _parse_gmi_json(raw: str) -> GMIResponse:
    # Strip markdown fences if model wrapped anyway
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    parsed = json.loads(cleaned)
    return GMIResponse(**parsed)


async def analyze_incident(alert_text: str, memories: list[dict]) -> GMIResponse:
    memory_context = json.dumps(memories, indent=2) if memories else "No prior incidents found."
    user_prompt = f"""Alert received:
{alert_text}

Relevant memory from HydraDB:
{memory_context}

Respond ONLY with JSON matching this schema:
{ANALYSIS_SCHEMA}

Fill sms_reply with the triage SMS to send the engineer (hypotheses + most likely cause).
Leave exec_recap and video_prompt empty for now."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages)
    result = _parse_gmi_json(raw)
    logger.info("GMI ANALYSIS | severity=%s | matched=%s | hypotheses=%d",
                result.severity, result.matched_memory, len(result.hypotheses))
    return result


async def generate_recap_and_video_prompt(incident_summary: str, memories: list[dict]) -> GMIResponse:
    memory_context = json.dumps(memories, indent=2) if memories else ""
    user_prompt = f"""Incident resolved. Generate the executive recap and PixVerse video prompt.

Incident summary:
{incident_summary}

Prior incident memories:
{memory_context}

Respond ONLY with JSON matching this schema:
{ANALYSIS_SCHEMA}

Rules:
- exec_recap: under 5 sentences, non-technical, executive-friendly.
- video_prompt: detailed PixVerse scene-by-scene prompt for a 5-8 second isometric SaaS-style video.
- sms_reply: the full SMS text announcing recap and video generation.
- Leave hypotheses as empty array."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages)
    result = _parse_gmi_json(raw)
    logger.info("GMI RECAP | exec_recap_len=%d | video_prompt_len=%d",
                len(result.exec_recap), len(result.video_prompt))
    return result


INTENTS = ["TRIAGE", "RECALL", "VIDEO", "VIDEO_STATUS", "SAVE_FOLLOWUP", "PROVIDE_STEPS", "CONFIRM", "UNKNOWN"]

async def classify_intent(message: str, current_state: str) -> str:
    """Use GMI to classify free-form engineer message into a known intent."""
    user_prompt = f"""Current incident state: {current_state}
Engineer message: "{message}"

Classify the message into exactly one intent from this list:
- TRIAGE: engineer wants to start triage / investigate the alert
- RECALL: engineer is asking what was done before, prior mitigation, last time, history
- PROVIDE_STEPS: engineer is describing what they did / triage steps / investigation findings (only when state is AWAITING_MANUAL_TRIAGE)
- CONFIRM: engineer is confirming a suggested resolution (yes, apply, do it, confirmed, sounds right)
- VIDEO: engineer says incident is resolved / mitigated and wants exec recap or video
- VIDEO_STATUS: engineer is asking for video status or progress
- SAVE_FOLLOWUP: engineer wants to save a note, follow-up, or action item
- UNKNOWN: none of the above

Reply with ONLY the intent word, nothing else. No explanation."""

    messages = [
        {"role": "system", "content": "You are an intent classifier. Reply with exactly one word from the allowed list."},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages, temperature=0.0)
    intent = raw.strip().upper().split()[0]
    if intent not in INTENTS:
        intent = "UNKNOWN"
    logger.info("INTENT CLASSIFY | message=%s | state=%s | intent=%s", message[:60], current_state, intent)
    return intent


async def analyze_k8s_incident(alert_text: str, k8s_context: dict, memories: list[dict]) -> dict:
    """
    Analyze a K8s incident. Returns:
      - needs_manual_triage: True if no memory found and engineer must provide steps
      - sms_reply: what to send back
      - resolution: suggested fix if memory found
      - matched_memory_id: the memory ID that matched
    """
    memory_context = json.dumps(memories, indent=2) if memories else "No prior incidents found."
    user_prompt = f"""K8s production incident alert:
{alert_text}

K8s context:
{json.dumps(k8s_context, indent=2)}

Prior incident memories from HydraDB:
{memory_context}

Analyze and respond with JSON:
{{
  "needs_manual_triage": true/false,
  "matched_memory_id": "<id or empty>",
  "root_cause": "<inferred root cause>",
  "resolution": "<specific fix steps if memory found, else empty>",
  "confidence": "high/medium/low",
  "sms_reply": "<full SMS to send engineer>"
}}

Rules:
- If memories contain a matching prior incident (same service, same error type): set needs_manual_triage=false, fill resolution with the prior fix steps, set confidence.
- If no matching memory: set needs_manual_triage=true, sms_reply should ask engineer to describe what they see and the steps they took.
- Be specific about K8s resources (pod names, namespaces, exit codes).
Reply ONLY with raw JSON, no markdown."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages)
    cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(cleaned)
    logger.info("GMI K8S ANALYSIS | needs_manual=%s | matched=%s | confidence=%s",
                result.get("needs_manual_triage"), result.get("matched_memory_id"), result.get("confidence"))
    return result


async def extract_triage_steps(raw_steps: str, k8s_context: dict, incident_id: str) -> dict:
    """Parse engineer's free-form triage description into structured memory."""
    user_prompt = f"""Engineer described their triage steps for incident {incident_id}:
"{raw_steps}"

K8s context: {json.dumps(k8s_context)}

Extract and respond with JSON:
{{
  "root_cause": "<concise root cause>",
  "fix_steps": ["step1", "step2", ...],
  "commands_used": ["kubectl ...", ...],
  "resolution_summary": "<one sentence>",
  "memory_text": "<full text to store in HydraDB for future recall>"
}}

Reply ONLY with raw JSON."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages)
    cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    result = json.loads(cleaned)
    logger.info("GMI EXTRACT STEPS | root_cause=%s | steps=%d", result.get("root_cause"), len(result.get("fix_steps", [])))
    return result


async def generate_k8s_mermaid(k8s_ctx: dict, state: str, triage_steps: str = "", resolution: str = "") -> str:
    """Generate a Mermaid diagram for a K8s incident in error or resolved state."""
    pod = k8s_ctx.get("pod", "app-pod")
    ns = k8s_ctx.get("namespace", "production")
    mem_used = k8s_ctx.get("memory_usage", "")
    mem_limit = k8s_ctx.get("memory_limit", "")
    exit_code = k8s_ctx.get("exit_code", "137")
    node = k8s_ctx.get("node", "gke-node")
    deploy = k8s_ctx.get("recent_deploy", "")
    deployment = k8s_ctx.get("deployment", "app")

    if state == "error":
        context = f"""K8s error state:
- Pod: {pod} | Namespace: {ns}
- Status: CrashLoopBackOff | Exit {exit_code} (OOMKilled)
- Memory: {mem_used} / {mem_limit}
- Node: {node}
- Triggered by: {deploy}"""
    else:
        context = f"""K8s resolved state:
- Same pod/service now healthy
- Fix applied: {resolution or triage_steps}
- Memory limit increased
- Service restored"""

    user_prompt = f"""Generate a Mermaid flowchart diagram for this K8s incident ({state} state).

{context}

Requirements:
- Use %%{{init:{{'theme':'dark'}}}}%% at top
- Show: Internet → Load Balancer → Service → Pod → Node
- Show the recent deploy or fix as a dashed edge
- For error state: pod node should show CrashLoopBackOff/OOMKilled in red
- For resolved state: pod node should show Running/healthy in green
- Use classDef with appropriate colors (error=red, resolved=green, svc=blue, node=gray)
- Include the actual pod name, namespace, memory values from context
- Keep it compact — max 12 nodes

Reply ONLY with the raw Mermaid code. No markdown fences, no explanation."""

    messages = [
        {"role": "system", "content": "You generate valid Mermaid diagram code. Reply only with the diagram, no markdown fences."},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages, temperature=0.2)
    # Strip any accidental fences
    code = raw.strip()
    for fence in ("```mermaid", "```", "`"):
        code = code.strip(fence).strip()
    logger.info("GMI MERMAID | state=%s | lines=%d", state, len(code.splitlines()))
    return code


async def generate_k8s_video_prompt(incident: dict, k8s_ctx: dict, triage_steps: str, resolution: str) -> str:
    """Generate a detailed, incident-accurate PixVerse t2v prompt from real incident data."""

    pod = k8s_ctx.get("pod", "app-pod")
    ns = k8s_ctx.get("namespace", "production")
    cluster = k8s_ctx.get("cluster", "gke-prod-cluster")
    mem_used = k8s_ctx.get("memory_usage", "498Mi")
    mem_limit = k8s_ctx.get("memory_limit", "512Mi")
    exit_code = k8s_ctx.get("exit_code", "137")
    node = k8s_ctx.get("node", "gke-node")
    deploy = k8s_ctx.get("recent_deploy", "recent deploy")
    service = incident.get("service", "payment-service")

    user_prompt = f"""You are writing a PixVerse text-to-video prompt for an executive incident recap video.

This is the REAL incident that happened:
- Service: {service}
- Cluster: {cluster} | Namespace: {ns}
- Pod: {pod} on node {node}
- What broke: CrashLoopBackOff, exit code {exit_code} (OOMKilled) — pod consumed {mem_used} hitting the {mem_limit} memory limit
- Triggered by: {deploy}
- How it was diagnosed: {triage_steps}
- How it was fixed: {resolution}

Write a single PixVerse video generation prompt (no JSON, no headers, just the prompt text) for a 8-second cinematic executive recap video that:

1. Opens with a dark, cinematic establishing shot of the production Kubernetes cluster "{cluster}" — glowing node graph, deep navy/obsidian background, professional enterprise ops aesthetic
2. Zooms into namespace "{ns}" — one pod node "{pod}" begins pulsing bright red with alert indicators, memory gauge filling to critical ({mem_used} / {mem_limit})
3. Displays a clean terminal overlay showing OOMKilled exit {exit_code} — text appears letter-by-letter like a real log stream
4. Shows the engineer's fix: memory limit increasing on screen, a kubectl patch animation, pod restarting
5. Pod node transitions from red to green — health check indicators turning green one by one
6. Final wide shot: entire cluster glows healthy green, metrics normalize, text overlay fades in: "Resolved — {service} healthy"

Style requirements:
- Dark enterprise ops aesthetic, Blade Runner meets Apple keynote
- Slow, deliberate, cinematic pacing — no fast cuts
- Volumetric glow on node connections
- Color language: deep red = crisis, emerald green = resolution
- Professional, charming, executive-ready — something a CTO would be proud to show in a postmortem

Write the prompt now. Be vivid, specific, and cinematic. Use the exact names from the incident."""

    messages = [
        {
            "role": "system",
            "content": (
                "You are a world-class video director writing PixVerse generation prompts. "
                "Your prompts are vivid, technically accurate, and cinematically beautiful. "
                "Always write in English. Be specific with real incident data. No JSON, no markdown — just the prompt."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages, temperature=0.5)
    prompt = raw.strip()
    logger.info("GMI K8S VIDEO PROMPT | length=%d | preview=%s", len(prompt), prompt[:100])
    return prompt


async def generate_memory_update(follow_up_text: str, incident_id: str) -> str:
    user_prompt = f"""The engineer saved this follow-up for incident {incident_id}:
"{follow_up_text}"

Respond ONLY with JSON matching this schema:
{ANALYSIS_SCHEMA}

Only fill memory_update with a concise one-sentence memory to store. Leave all other fields at defaults."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    raw = await _chat(messages)
    result = _parse_gmi_json(raw)
    logger.info("GMI MEMORY UPDATE | update=%s", result.memory_update)
    return result.memory_update or follow_up_text
