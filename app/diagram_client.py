import zlib
import base64
import httpx
import logging

logger = logging.getLogger(__name__)

KROKI_BASE = "https://kroki.io"


def _encode(diagram: str) -> str:
    compressed = zlib.compress(diagram.encode("utf-8"), 9)
    return base64.urlsafe_b64encode(compressed).decode("utf-8")


def mermaid_to_url(mermaid_code: str, fmt: str = "png") -> str:
    encoded = _encode(mermaid_code)
    url = f"{KROKI_BASE}/mermaid/{fmt}/{encoded}"
    logger.info("DIAGRAM URL generated | mermaid_len=%d", len(mermaid_code))
    return url


async def verify_url(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            ok = r.status_code == 200 and "image" in r.headers.get("content-type", "")
            logger.info("DIAGRAM VERIFY | ok=%s | status=%s", ok, r.status_code)
            return ok
    except Exception as exc:
        logger.warning("DIAGRAM VERIFY FAILED | %s", exc)
        return False


def build_error_diagram(k8s: dict) -> str:
    pod = k8s.get("pod", "app-pod")
    ns = k8s.get("namespace", "production")
    mem_used = k8s.get("memory_usage", "498Mi")
    mem_limit = k8s.get("memory_limit", "512Mi")
    exit_code = k8s.get("exit_code", "137")
    deploy = k8s.get("recent_deploy", "recent deploy")
    deployment = k8s.get("deployment", "app")
    cluster = k8s.get("cluster", "gke-prod-cluster")

    # Keep node labels SHORT and single-line — PixVerse corrupts multi-line text
    return f"""%%{{init: {{
  'theme': 'base',
  'themeVariables': {{
    'darkMode': true,
    'background': '#0a0e1a',
    'primaryColor': '#1a1f35',
    'primaryTextColor': '#e2e8f0',
    'primaryBorderColor': '#2d3748',
    'lineColor': '#f87171',
    'secondaryColor': '#1e2a3a',
    'tertiaryColor': '#0d1117',
    'edgeLabelBackground': '#1a1f35',
    'clusterBkg': '#1f0000',
    'titleColor': '#e2e8f0',
    'nodeTextColor': '#e2e8f0'
  }}
}}}}%%
graph TD
  subgraph CLUSTER["{cluster}"]
    subgraph NS["namespace: {ns}"]
      SVC["{deployment}-svc Service"]
      POD["POD: {pod}  CrashLoopBackOff  OOMKilled  {mem_used}/{mem_limit}"]
    end
  end

  INTERNET([Internet]) --> LB[Cloud Load Balancer]
  LB --> INGRESS[Ingress Controller]
  INGRESS --> SVC
  SVC --> POD
  DEPLOY["Deploy: {deploy}"] -->|triggered crash| POD

  classDef critical fill:#7f1d1d,stroke:#ef4444,stroke-width:3px,color:#fee2e2
  classDef svc fill:#1e3a5f,stroke:#3b82f6,stroke-width:2px,color:#dbeafe
  classDef deploy fill:#431407,stroke:#f97316,stroke-width:2px,color:#ffedd5
  classDef net fill:#1a1f2e,stroke:#6b7280,stroke-width:1px,color:#d1d5db

  class POD critical
  class SVC svc
  class DEPLOY deploy
  class LB,INGRESS,INTERNET net"""


def build_resolved_diagram(k8s: dict, resolution: str) -> str:
    pod = k8s.get("pod", "app-pod")
    ns = k8s.get("namespace", "production")
    mem_limit = k8s.get("memory_limit", "512Mi")
    deployment = k8s.get("deployment", "app")
    cluster = k8s.get("cluster", "gke-prod-cluster")

    try:
        val = int("".join(c for c in mem_limit if c.isdigit()))
        unit = "".join(c for c in mem_limit if c.isalpha())
        new_limit = f"{val * 2}{unit}"
    except Exception:
        new_limit = "1Gi"

    # Keep node labels SHORT and single-line — PixVerse corrupts multi-line text
    return f"""%%{{init: {{
  'theme': 'base',
  'themeVariables': {{
    'darkMode': true,
    'background': '#0a0e1a',
    'primaryColor': '#1a1f35',
    'primaryTextColor': '#e2e8f0',
    'primaryBorderColor': '#2d3748',
    'lineColor': '#34d399',
    'secondaryColor': '#1e2a3a',
    'tertiaryColor': '#0d1117',
    'edgeLabelBackground': '#1a1f35',
    'clusterBkg': '#0f1f0f',
    'titleColor': '#e2e8f0',
    'nodeTextColor': '#e2e8f0'
  }}
}}}}%%
graph TD
  subgraph CLUSTER["{cluster}"]
    subgraph NS["namespace: {ns}"]
      SVC["{deployment}-svc Service"]
      POD["POD: {pod}  RUNNING  Memory OK / {new_limit}"]
    end
  end

  INTERNET([Internet]) --> LB[Cloud Load Balancer]
  LB --> INGRESS[Ingress Controller]
  INGRESS --> SVC
  SVC --> POD
  FIX["Fix Applied: memory limit increased to {new_limit}"] -->|resolved| POD

  classDef healthy fill:#14532d,stroke:#22c55e,stroke-width:3px,color:#dcfce7
  classDef svc fill:#1e3a5f,stroke:#3b82f6,stroke-width:2px,color:#dbeafe
  classDef fix fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#d1fae5
  classDef net fill:#1a2e1a,stroke:#4ade80,stroke-width:1px,color:#bbf7d0

  class POD healthy
  class SVC svc
  class FIX fix
  class LB,INGRESS,INTERNET net"""
