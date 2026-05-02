# Sentinel

**SMS-native incident command agent for engineering teams.**

Built with Photon · HydraDB · GMI Cloud · PixVerse

---

## 60-Second Pitch

Sentinel is an SMS-native incident command agent for engineering teams. Most tools only page you when production breaks. Sentinel remembers with you. It uses Photon as the conversation layer, HydraDB as incident memory, GMI Cloud for reasoning and agent orchestration, and PixVerse to generate a real executive recap video from the incident timeline. The engineer can triage, recall past mitigations, generate stakeholder updates, and create a video recap without leaving SMS.

---

## Stack

| Layer | Sponsor |
|---|---|
| SMS conversation | Photon |
| Incident memory | HydraDB |
| LLM reasoning | GMI Cloud |
| Executive video | PixVerse |
| Backend | FastAPI + httpx |

---

## Setup

### 1. Clone and install

```bash
git clone <repo>
cd matcha-hackthon

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env  # or restart shell

# Install all dependencies into a managed .venv
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Source |
|---|---|
| `GMI_API_KEY` | GMI Cloud dashboard — JWT bearer token |
| `GMI_MODEL` | GMI Cloud dashboard — confirm available model IDs |
| `HYDRADB_API_KEY` | HydraDB sponsor credentials |
| `HYDRADB_PROJECT_ID` | HydraDB project ID |
| `HYDRADB_SECRET_KEY` | HydraDB secret key |
| `PIXVERSE_API_KEY` | PixVerse API token (may match GMI token — confirm with sponsor) |
| `PHOTON_API_KEY` | Photon sponsor dashboard |
| `PHOTON_FROM_NUMBER` | Photon sender number (E.164) |
| `ON_CALL_PHONE` | Your real phone number for SMS demo (E.164) |

> **TODOs to confirm with sponsors at the booth:**
> - GMI Cloud: exact base URL and available model names
> - HydraDB: exact base URL, auth header name, endpoint paths
> - PixVerse: whether it uses the GMI JWT or a separate token; exact endpoint paths
> - Photon: base URL, auth header, webhook payload field names

### 3. Start the server

```bash
uv run uvicorn app.main:app --reload --port 8000
```

### 4. Expose localhost with ngrok

```bash
ngrok http 8000
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`).

### 5. Configure Photon webhook

In the Photon dashboard, set the inbound webhook URL to:

```
https://abc123.ngrok.io/sms/inbound
```

> TODO: Confirm the exact field name and format in the Photon dashboard.

---

## Seeding HydraDB

Run this once before the demo to load prior incident memories:

```bash
curl -s -X POST http://localhost:8000/seed-memory | jq .
```

Expected output:
```json
{
  "seeded": 5,
  "total": 5,
  "results": [...]
}
```

---

## Triggering the Demo Alert

```bash
# Use ON_CALL_PHONE from .env
curl -s -X POST http://localhost:8000/fake-alert | jq .

# Override phone number
curl -s -X POST http://localhost:8000/fake-alert \
  -H "Content-Type: application/json" \
  -d '{"phone": "+15555550100"}' | jq .
```

---

## API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check + config |
| `POST` | `/seed-memory` | Seed HydraDB demo memories |
| `POST` | `/fake-alert` | Trigger P1 alert via Photon |
| `POST` | `/sms/inbound` | Photon inbound webhook |
| `POST` | `/pixverse/webhook` | PixVerse completion webhook |
| `GET` | `/pixverse/status/{job_id}` | Poll PixVerse job status |
| `GET` | `/incident/{incident_id}` | Get full incident state (JSON) |
| `GET` | `/incidents` | List all active incidents |

---

## Demo Script (2-Minute Judge Demo)

### Step 0 — Prep (before judges arrive)
```bash
# Terminal 1: FastAPI backend
uv run uvicorn app.main:app --reload --port 8001

# Terminal 2: Photon bridge
cd photon-bridge
PROJECT_ID=e712449d-e8cc-4637-9293-77924f869f80 \
PROJECT_SECRET=Jq8SJjiQeDl92ZKuvr59SYS_ngXR1Bl5HbJdSRtrQs0 \
node bridge.js

# Seed HydraDB
curl -s -X POST http://localhost:8001/seed-memory | jq .
```

**One-time space warm-up — engineer texts the Photon iMessage number:**
```
START
```
Bridge replies: "Sentinel is ready." — this seeds the space cache so outbound SMS works.

---

### Step 1 — Trigger the alert
```bash
curl -s -X POST http://localhost:8000/fake-alert | jq .
```

**SMS received on your phone:**
```
🚨 P1: checkout-api 500s spiking.
Current error rate: 18%.
Recent deploy: checkout-api v2.8.1, 8 min ago.
Reply TRIAGE to start.
```

---

### Step 2 — Reply: `TRIAGE`

Sentinel queries HydraDB, calls GMI Cloud, replies:

```
Top hypotheses from incident memory:

1. Redis connection pool exhaustion — Matches prior incident #214
2. Bad checkout-api v2.8.1 deploy — Recent deploy 8 minutes ago
3. Payment provider timeout — Possible dependency issue

Most likely: #1 because Redis latency and connection timeout errors match March.
Ask: "What did we do last time?" to recall the previous mitigation.
```

---

### Step 3 — Reply: `What did we do last time?`

```
Incident #214 mitigation:
- rollback canary from 50% to 10%
- increase Redis max connections from 100 → 300
- restart checkout-api workers
- monitor checkout_5xx_rate for 10 min

Suggested first action: rollback canary to 10%.
```

---

### Step 4 — Reply: `Mitigated. Generate exec recap video.`

Immediate SMS:
```
Mitigation recorded.

Exec recap:
Checkout errors spiked after the v2.8.1 deploy. Sentinel matched the pattern
to March incident #214: Redis connection pool exhaustion. Canary traffic was
reduced and checkout stabilized. Follow-up: add Redis saturation checks before
checkout deploys.

Generating live PixVerse video now...
```

Then PixVerse job SMS:
```
PixVerse job started: 7382910
Reply VIDEO STATUS to check progress.
```

---

### Step 5 — Reply: `VIDEO STATUS`

```
PixVerse status: processing. Job ID: 7382910.
```

*(When complete, auto-delivers:)*
```
Exec recap video ready:
https://cdn.pixverse.ai/videos/7382910.mp4
```

---

### Step 6 — Reply: `Save follow-up: add Redis saturation check before checkout deploys`

```
Saved. Future checkout-api deploy incidents will check Redis saturation
before rollback recommendations.
```

---

### Show judges the incident state
```bash
curl -s http://localhost:8000/incidents | jq '.[0]'
```

---

## Architecture

```
Phone (SMS)
    │
    ▼
Photon ──► POST /sms/inbound
                │
                ▼
          agent.dispatch_inbound()
                │
         ┌──────┴──────┐
         ▼             ▼
    HydraDB         GMI Cloud
  (recall memory)  (reason + generate)
         │             │
         └──────┬──────┘
                ▼
           PixVerse
        (video generation)
                │
                ▼
        Photon (reply SMS)
```

---

## Error Handling

- **GMI fails** → SMS: "Sentinel reasoning failed. Please triage manually."
- **PixVerse slow** → Sends real job ID; SMS when polled or when webhook fires.
- **PixVerse fails** → SMS with actual failure status; incident recap text preserved.
- **HydraDB unreachable** → Falls back to baked-in INC-214 knowledge; logs warning.
- **No active incident for phone** → SMS: "No active incident found."
