# Sentinel

Contributors - Chinmay Gabel & Sanmati Rajiv Sawalwade

**Sentinel remembers so your team doesn't have to.**

SMS-native incident command agent for engineering teams.

Built with **Photon** · **HydraDB** · **GMI Cloud** · **PixVerse**

---

## 🎬 Demo Video

[Watch the full demo](https://drive.google.com/file/d/1EYYmyACnN4U0aIr5kLIDhAXuoUpqgq86/view?usp=sharing)

---

## Description

Sentinel is an SMS-native incident command agent for engineering teams. When production breaks, Sentinel pages your on-call engineer through **Photon** over iMessage, queries **HydraDB** to recall similar past incidents and stored mitigations, uses **GMI Cloud** to reason through triage and rank hypotheses, and generates a live executive recap video with **PixVerse** — all without leaving SMS. The more incidents it handles, the smarter it gets.

---

## 60-Second Pitch

Most tools only page you when production breaks. Sentinel remembers with you. It uses Photon as the conversation layer, HydraDB as incident memory, GMI Cloud for reasoning and agent orchestration, and PixVerse to generate a real executive recap video from the incident timeline. The engineer can triage, recall past mitigations, generate stakeholder updates, and create a video recap — without leaving SMS.

---

## Stack

| Layer | Technology |
|---|---|
| SMS / iMessage | Photon (Spectrum SDK) |
| Incident memory | HydraDB |
| LLM reasoning + intent | GMI Cloud (`anthropic/claude-haiku-4.5`) |
| Video generation | PixVerse via GMI Cloud (`pixverse-v5.6-transition`) |
| Architecture diagrams | Mermaid + Kroki.io |
| Backend | FastAPI + Python 3.11 |
| Package manager | uv |
| Photon bridge | Node.js + Spectrum SDK |

---

## Architecture

```
Engineer Phone (iMessage)
        │
        ▼
  Photon (Spectrum)
        │  bridge.js forwards inbound
        ▼
  POST /sms/inbound
        │
        ▼
  agent.dispatch_inbound()
  (LLM intent detection via GMI Cloud)
        │
   ┌────┴────────────────┐
   ▼                     ▼
HydraDB              GMI Cloud
(recall / store)     (triage / recap)
   │                     │
   └────────┬────────────┘
            ▼
    Mermaid → Kroki.io
    (error + resolved diagrams)
            │
            ▼
       PixVerse (via GMI Cloud)
    (transition: red → green)
            │
            ▼
  Photon → video URL to engineer
```

---

## Setup

### 1. Clone and install

```bash
git clone <repo>
cd matcha-hackthon

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in your credentials
```

| Variable | Description |
|---|---|
| `GMI_API_KEY` | GMI Cloud inference JWT (scope: ie_model) |
| `GMI_INFRA_API_KEY` | GMI Cloud infra JWT (scope: ce_resource) — for PixVerse |
| `GMI_MODEL` | e.g. `anthropic/claude-haiku-4.5` |
| `HYDRADB_API_KEY` | HydraDB API key (`sk_live_...`) |
| `HYDRADB_PROJECT_ID` | HydraDB project UUID |
| `HYDRADB_SECRET_KEY` | HydraDB secret key |
| `PROJECT_ID` | Photon Spectrum project ID |
| `PROJECT_SECRET` | Photon Spectrum project secret |
| `ON_CALL_PHONE` | Engineer's phone number (E.164, e.g. `+12135739107`) |

### 3. Start the servers

**Terminal 1 — FastAPI backend**
```bash
uv run uvicorn app.main:app --reload --port 8001
```

**Terminal 2 — Photon bridge**
```bash
cd photon-bridge
PROJECT_ID=<your_project_id> PROJECT_SECRET=<your_project_secret> node bridge.js
```

---

## Demo

### Before every demo — reset + seed

```bash
# Seed HydraDB (first time only)
curl -s -X POST http://localhost:8001/seed-memory | python3 -m json.tool

# Reset K8s demo session (clean slate)
curl -s -X POST http://localhost:8001/reset-k8s-demo | python3 -m json.tool

# Text START from your phone to the Photon iMessage number
# Wait for: "Sentinel is ready. Waiting for the alert."
```

---

### Round 1 — K8s incident, no prior memory

```bash
curl -s -X POST http://localhost:8001/fakeerror1 | python3 -m json.tool
```

Phone receives:
```
🚨 K8s ALERT | payment-service | production
Pod: payment-svc-7d8f9b-xkp2j
Status: CrashLoopBackOff (restarts: 8)
Exit Code: 137 (OOMKilled)
Memory: 498Mi / 512Mi limit
```

Reply flow:
1. `triage this`
2. `checked logs, found OOMKilled exit 137, increased memory limit from 512Mi to 1Gi, redeployed payment-svc`
3. `generate video`

Sentinel stores the triage steps to HydraDB. PixVerse generates a before/after architecture diagram video automatically.

---

### Round 2 — same error, memory found, auto-resolution

```bash
curl -s -X POST http://localhost:8001/fakeerror2 | python3 -m json.tool
```

Reply flow:
1. `triage`
2. `yes apply it`

Sentinel finds the stored steps in HydraDB, suggests the fix automatically, and generates the video.

---

### Original checkout-api demo

```bash
curl -s -X POST http://localhost:8001/fake-alert | python3 -m json.tool
```

Reply flow:
1. `TRIAGE`
2. `what did we do last time?`
3. `mitigated, generate exec recap video`
4. `save: add Redis saturation check before checkout deploys`

---

### Show judges the incident state

```bash
curl -s http://localhost:8001/incidents | python3 -m json.tool
```

---

## API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/seed-memory` | Seed HydraDB with demo memories |
| `POST` | `/reset-k8s-demo` | Reset K8s demo session (clean HydraDB namespace) |
| `POST` | `/fake-alert` | Trigger checkout-api P1 alert |
| `POST` | `/fakeerror1` | Trigger K8s OOMKilled alert (no prior memory) |
| `POST` | `/fakeerror2` | Trigger K8s OOMKilled alert (memory exists) |
| `POST` | `/sms/inbound` | Photon inbound webhook |
| `GET` | `/incident/{id}` | Get full incident state |
| `GET` | `/incidents` | List all incidents |
| `GET` | `/pixverse/status/{job_id}` | Poll PixVerse job |

---

## How the Video Works

1. GMI generates Mermaid diagram code for the **error state** (red pod, CrashLoopBackOff)
2. GMI generates Mermaid diagram code for the **resolved state** (green pod, Running)
3. Both are rendered to PNG via **Kroki.io**
4. **PixVerse transition model** animates between the two diagrams
5. Video URL is sent to the engineer's phone via Photon when ready

---

## Error Handling

- **GMI fails** → SMS: "Sentinel reasoning failed. Please triage manually."
- **PixVerse slow** → Background polling every 15s, video URL sent automatically when ready.
- **PixVerse fails** → SMS with failure status; incident recap text preserved.
- **HydraDB unreachable** → Falls back to baked-in INC-214 knowledge; logs warning.
- **No space cached in Photon bridge** → Text START to the Photon number first.
