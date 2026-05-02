from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime


class IncidentState(str, Enum):
    ALERT_SENT = "ALERT_SENT"
    AWAITING_MANUAL_TRIAGE = "AWAITING_MANUAL_TRIAGE"  # no memory found, waiting for engineer steps
    MANUAL_TRIAGE_STORED = "MANUAL_TRIAGE_STORED"       # engineer steps saved to HydraDB
    RESOLUTION_SUGGESTED = "RESOLUTION_SUGGESTED"       # memory found, resolution proposed
    TRIAGED = "TRIAGED"
    RECALLED = "RECALLED"
    RECAP_SENT = "RECAP_SENT"
    VIDEO_GENERATING = "VIDEO_GENERATING"
    VIDEO_COMPLETE = "VIDEO_COMPLETE"
    COMPLETE = "COMPLETE"


class Hypothesis(BaseModel):
    rank: int
    name: str
    reason: str


class GMIResponse(BaseModel):
    severity: str = "P1"
    matched_memory: str = ""
    hypotheses: List[Hypothesis] = []
    recommended_action: str = ""
    sms_reply: str = ""
    exec_recap: str = ""
    video_prompt: str = ""
    memory_update: str = ""


class TimelineEvent(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    event: str
    detail: str = ""


class K8sContext(BaseModel):
    cluster: str = ""
    namespace: str = ""
    pod: str = ""
    deployment: str = ""
    exit_code: str = ""
    error_type: str = ""
    memory_usage: str = ""
    memory_limit: str = ""
    node: str = ""
    recent_deploy: str = ""


class Incident(BaseModel):
    incident_id: str
    service: str = "checkout-api"
    alert_text: str = ""
    state: IncidentState = IncidentState.ALERT_SENT
    phone: str = ""
    k8s: Optional[K8sContext] = None          # K8s-specific context for richer video prompt
    manual_triage_steps: str = ""              # steps engineer provided on first run
    gmi_analysis: Optional[GMIResponse] = None
    pixverse_job_id: Optional[str] = None
    pixverse_video_url: Optional[str] = None
    exec_recap: str = ""
    timeline: List[TimelineEvent] = []
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def add_event(self, event: str, detail: str = "") -> None:
        self.timeline.append(TimelineEvent(event=event, detail=detail))
        self.updated_at = datetime.utcnow().isoformat()


class FakeAlertRequest(BaseModel):
    phone: Optional[str] = None  # override on-call number for testing


class PhotonInbound(BaseModel):
    from_number: str = Field(alias="from", default="")
    to_number: str = Field(alias="to", default="")
    body: str = ""
    message_id: str = ""

    class Config:
        populate_by_name = True


class PixVerseWebhook(BaseModel):
    task_id: str = ""
    status: str = ""
    url: Optional[str] = None
    error: Optional[str] = None
