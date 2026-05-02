import uuid
from typing import Dict, Optional
from app.models import Incident

# In-memory incident store
_incidents: Dict[str, Incident] = {}
_phone_to_incident: Dict[str, str] = {}
_pixverse_to_incident: Dict[str, str] = {}

# K8s demo session — changes on every reset so HydraDB sub-tenant is always fresh
_k8s_session_id: str = uuid.uuid4().hex[:8]


def get_k8s_session() -> str:
    return f"sentinel-k8s-{_k8s_session_id}"


def reset_k8s_demo() -> dict:
    """Generate a new session ID and remove all K8s incidents from memory."""
    global _k8s_session_id
    old_session = _k8s_session_id
    _k8s_session_id = uuid.uuid4().hex[:8]

    removed = []
    for inc_id, inc in list(_incidents.items()):
        if inc.k8s is not None:
            removed.append(inc_id)
            _incidents.pop(inc_id, None)
            _phone_to_incident.pop(inc.phone, None)

    return {
        "old_session": f"sentinel-k8s-{old_session}",
        "new_session": get_k8s_session(),
        "incidents_cleared": removed,
    }


def get_incident(incident_id: str) -> Optional[Incident]:
    return _incidents.get(incident_id)


def save_incident(incident: Incident) -> None:
    _incidents[incident.incident_id] = incident
    _phone_to_incident[incident.phone] = incident.incident_id


def get_incident_by_phone(phone: str) -> Optional[Incident]:
    inc_id = _phone_to_incident.get(phone)
    if inc_id:
        return _incidents.get(inc_id)
    return None


def register_pixverse_job(job_id: str, incident_id: str) -> None:
    _pixverse_to_incident[job_id] = incident_id


def get_incident_by_pixverse_job(job_id: str) -> Optional[Incident]:
    inc_id = _pixverse_to_incident.get(job_id)
    if inc_id:
        return _incidents.get(inc_id)
    return None


def list_incidents() -> list:
    return list(_incidents.values())
