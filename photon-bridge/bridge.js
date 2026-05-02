/**
 * Photon Bridge — connects Spectrum SDK (iMessage/SMS) to the Sentinel FastAPI backend.
 *
 * Inbound:  Spectrum → bridge → POST http://localhost:8001/sms/inbound
 * Outbound: FastAPI   → POST http://localhost:3001/send  → bridge → Spectrum space.send()
 *
 * IMPORTANT: Before calling /fake-alert, text anything to the Photon iMessage number
 * from the on-call phone. This seeds the space cache so outbound alerts can be sent.
 */

import { Spectrum } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import http from "http";

const PROJECT_ID     = process.env.PROJECT_ID     || process.env.HYDRADB_PROJECT_ID;
const PROJECT_SECRET = process.env.PROJECT_SECRET || process.env.HYDRADB_SECRET_KEY;
const FASTAPI_URL    = process.env.FASTAPI_URL    || "http://localhost:8001";
const BRIDGE_PORT    = parseInt(process.env.BRIDGE_PORT || "3001", 10);

if (!PROJECT_ID || !PROJECT_SECRET) {
  console.error("ERROR: PROJECT_ID and PROJECT_SECRET are required");
  process.exit(1);
}

// phone number → Space object (populated when engineer first texts us)
const spaceCache = new Map();

// Extract bare E.164 phone from iMessage chat ID: "any;-;+12135739107" → "+12135739107"
function extractPhone(spaceId) {
  const match = spaceId.match(/\+\d{7,15}$/);
  return match ? match[0] : spaceId;
}

// ── Spectrum connection ──────────────────────────────────────────────────────

console.log("[bridge] Connecting to Spectrum Cloud...");
const app = await Spectrum({
  projectId: PROJECT_ID,
  projectSecret: PROJECT_SECRET,
  providers: [imessage.config()],
});
console.log("[bridge] Connected to Spectrum ✓");

// ── Inbound message loop ─────────────────────────────────────────────────────

async function startMessageLoop() {
  for await (const [space, message] of app.messages) {
    // Cache by full space.id AND bare E.164 phone number extracted from iMessage chat ID
    // iMessage chat IDs look like: "any;-;+12135739107" or "iMessage;-;+12135739107"
    spaceCache.set(space.id, space);
    const barePhone = extractPhone(space.id);
    if (barePhone && barePhone !== space.id) spaceCache.set(barePhone, space);
    console.log(`[bridge] INBOUND space.id=${space.id} bare=${barePhone} direction=${message.direction}`);

    if (message.direction !== "inbound") continue;
    if (message.content.type !== "text") continue;

    const body = message.content.text;
    const from = extractPhone(space.id); // normalize to bare E.164 for FastAPI

    console.log(`[bridge] INBOUND from=${from} body="${body}"`);

    // Special setup handshake — engineer texts to seed the space cache
    if (body.trim().toUpperCase() === "START") {
      await space.send("Sentinel is ready. Waiting for the alert.");
      console.log(`[bridge] SETUP ACK sent to ${from}`);
      continue;
    }

    // Forward to FastAPI
    try {
      const resp = await fetch(`${FASTAPI_URL}/sms/inbound`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from, body }),
      });
      const result = await resp.json();
      console.log(`[bridge] FORWARD OK → ${FASTAPI_URL}/sms/inbound | status=${result.status}`);
    } catch (err) {
      console.error(`[bridge] FORWARD FAILED: ${err.message}`);
    }
  }
}

// Run message loop in background (non-blocking)
startMessageLoop().catch((err) => {
  console.error("[bridge] Message loop crashed:", err);
  process.exit(1);
});

// ── HTTP server (FastAPI calls this to send outbound SMS) ────────────────────

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try { resolve(JSON.parse(raw)); } catch (e) { reject(e); }
    });
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  res.setHeader("Content-Type", "application/json");

  // POST /send  { to: "+15551234567", body: "text" }
  if (req.method === "POST" && req.url === "/send") {
    let payload;
    try {
      payload = await readBody(req);
    } catch {
      res.writeHead(400);
      res.end(JSON.stringify({ error: "invalid JSON body" }));
      return;
    }

    const { to, body } = payload;
    if (!to || !body) {
      res.writeHead(400);
      res.end(JSON.stringify({ error: "to and body are required" }));
      return;
    }

    const space = spaceCache.get(to);
    if (!space) {
      console.warn(`[bridge] SEND FAILED — no space cached for ${to}. Have the engineer text START first.`);
      res.writeHead(404);
      res.end(JSON.stringify({
        error: `No space cached for ${to}. Engineer must text the Photon number first to seed the space.`,
      }));
      return;
    }

    try {
      await space.send(body);
      console.log(`[bridge] OUTBOUND to=${to} body="${body.slice(0, 60)}..."`);
      res.writeHead(200);
      res.end(JSON.stringify({ ok: true, to }));
    } catch (err) {
      console.error(`[bridge] OUTBOUND FAILED: ${err.message}`);
      res.writeHead(500);
      res.end(JSON.stringify({ error: err.message }));
    }
    return;
  }

  // GET /spaces  — debug: list all cached phone numbers
  if (req.method === "GET" && req.url === "/spaces") {
    res.writeHead(200);
    res.end(JSON.stringify({ spaces: [...spaceCache.keys()] }));
    return;
  }

  // GET /health
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200);
    res.end(JSON.stringify({ ok: true, spaces: spaceCache.size }));
    return;
  }

  res.writeHead(404);
  res.end(JSON.stringify({ error: "not found" }));
});

server.listen(BRIDGE_PORT, () => {
  console.log(`[bridge] HTTP server listening on port ${BRIDGE_PORT}`);
  console.log(`[bridge] Waiting for engineer to text the Photon number to seed space cache...`);
});
