from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime, timezone


def get_current_iso_time() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditFields(BaseModel):
    case_ids: List[str] = Field(default_factory=list, description="List of case IDs this entity belongs to")
    source_record_ids: List[str] = Field(default_factory=list, description="List of source record IDs supporting this entity")
    created_at: str = Field(default_factory=get_current_iso_time, description="Creation ISO timestamp")
    updated_at: str = Field(default_factory=get_current_iso_time, description="Last updated ISO timestamp")


class ErrorDetail(BaseModel):
    error: str
    message: str
    detail: Optional[Any] = None
    code: Optional[int] = None

