"""
Incremental event model (Phase 2).

An *event* is one record of a kind the bulk ingestion path already persists,
addressed to one existing case. Payloads are the **same Pydantic models the
bulk path uses** (``backend.models.entity`` / ``backend.models.relationship``), so an
event validates exactly like the corresponding element of a ``CaseData``
payload, and is written with exactly the same statement from
``backend.services.graph_writes``.

Design constraints (see project context):

* ``event_type`` is a closed ``Enum`` used as the discriminator of a tagged
  union. Anything else -- including delete / retract / correction events and
  any IP-link event -- **fails validation** (HTTP 422 at the API boundary).
  ``LINKED_TO_IP`` is not a written relationship in this project and no event
  manufactures it.
* Events are upserts with MERGE semantics identical to bulk ingestion. There
  is no delete/retract/correction support; do not add event types here
  without a corresponding shared write statement in ``graph_writes``.
* The events API never creates a ``Case`` node from an event: the target case
  must already exist (bulk ingestion owns case metadata). This keeps case
  isolation explicit and avoids half-populated cases.
"""
from enum import Enum
from typing import Annotated, Dict, List, Literal, Optional, Set, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.models.common import get_current_iso_time
from backend.models.entity import (
    FIR, BankAccount, CellTower, IPAddress, Location, Person, Phone,
    SocialHandle, SourceRecord, Vehicle,
)
from backend.models.insights import InsightItem
from backend.models.relationship import (
    CommunicationRecord, CriminalHistoryRecord, IntelligenceReportRecord,
    SurveillanceLogRecord, TransactionRecord,
)


# ---------------------------------------------------------------------------
# Event types (closed set == what the bulk path persists today)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    # Entity upserts
    PERSON_UPSERT = "PERSON_UPSERT"
    PHONE_UPSERT = "PHONE_UPSERT"
    BANK_ACCOUNT_UPSERT = "BANK_ACCOUNT_UPSERT"
    VEHICLE_UPSERT = "VEHICLE_UPSERT"
    SOCIAL_HANDLE_UPSERT = "SOCIAL_HANDLE_UPSERT"
    IP_ADDRESS_UPSERT = "IP_ADDRESS_UPSERT"          # node + Case INVOLVES only; no LINKED_TO_IP
    LOCATION_UPSERT = "LOCATION_UPSERT"
    CELL_TOWER_UPSERT = "CELL_TOWER_UPSERT"
    SOURCE_RECORD_UPSERT = "SOURCE_RECORD_UPSERT"
    FIR_UPSERT = "FIR_UPSERT"
    # Relationship-bearing records
    COMMUNICATION = "COMMUNICATION"                  # (Phone)-[:CALLED]->(Phone)
    TRANSACTION = "TRANSACTION"                      # (BankAccount)-[:TRANSFERRED_TO]->(BankAccount) + Transaction node
    SURVEILLANCE_LOG = "SURVEILLANCE_LOG"            # (Person|Vehicle)-[:LOCATED_AT]->(Location)
    CRIMINAL_HISTORY = "CRIMINAL_HISTORY"            # (Person)-[:HAS_PRIOR_CASE]->(PriorCase)
    INTELLIGENCE_REPORT = "INTELLIGENCE_REPORT"      # SourceRecord(INTELLIGENCE_REPORT) + Case INVOLVES


# ---------------------------------------------------------------------------
# Event envelope + one concrete class per type (tagged union on event_type)
# ---------------------------------------------------------------------------

class _EventBase(BaseModel):
    """Common envelope. ``extra='forbid'`` so typos / unsupported keys fail loudly."""
    model_config = ConfigDict(extra="forbid")

    event_id: Optional[str] = Field(default=None, description="Optional client-supplied event identifier (echoed back)")
    occurred_at: Optional[str] = Field(default=None, description="Optional ISO 8601 time the fact was observed (informational)")


class PersonUpsertEvent(_EventBase):
    event_type: Literal[EventType.PERSON_UPSERT]
    payload: Person


class PhoneUpsertEvent(_EventBase):
    event_type: Literal[EventType.PHONE_UPSERT]
    payload: Phone


class BankAccountUpsertEvent(_EventBase):
    event_type: Literal[EventType.BANK_ACCOUNT_UPSERT]
    payload: BankAccount


class VehicleUpsertEvent(_EventBase):
    event_type: Literal[EventType.VEHICLE_UPSERT]
    payload: Vehicle


class SocialHandleUpsertEvent(_EventBase):
    event_type: Literal[EventType.SOCIAL_HANDLE_UPSERT]
    payload: SocialHandle


class IPAddressUpsertEvent(_EventBase):
    event_type: Literal[EventType.IP_ADDRESS_UPSERT]
    payload: IPAddress


class LocationUpsertEvent(_EventBase):
    event_type: Literal[EventType.LOCATION_UPSERT]
    payload: Location


class CellTowerUpsertEvent(_EventBase):
    event_type: Literal[EventType.CELL_TOWER_UPSERT]
    payload: CellTower


class SourceRecordUpsertEvent(_EventBase):
    event_type: Literal[EventType.SOURCE_RECORD_UPSERT]
    payload: SourceRecord


class FIRUpsertEvent(_EventBase):
    event_type: Literal[EventType.FIR_UPSERT]
    payload: FIR


class CommunicationEvent(_EventBase):
    event_type: Literal[EventType.COMMUNICATION]
    payload: CommunicationRecord


class TransactionEvent(_EventBase):
    event_type: Literal[EventType.TRANSACTION]
    payload: TransactionRecord


class SurveillanceLogEvent(_EventBase):
    event_type: Literal[EventType.SURVEILLANCE_LOG]
    payload: SurveillanceLogRecord


class CriminalHistoryEvent(_EventBase):
    event_type: Literal[EventType.CRIMINAL_HISTORY]
    payload: CriminalHistoryRecord


class IntelligenceReportEvent(_EventBase):
    event_type: Literal[EventType.INTELLIGENCE_REPORT]
    payload: IntelligenceReportRecord


GraphEvent = Annotated[
    Union[
        PersonUpsertEvent, PhoneUpsertEvent, BankAccountUpsertEvent, VehicleUpsertEvent,
        SocialHandleUpsertEvent, IPAddressUpsertEvent, LocationUpsertEvent, CellTowerUpsertEvent,
        SourceRecordUpsertEvent, FIRUpsertEvent,
        CommunicationEvent, TransactionEvent, SurveillanceLogEvent, CriminalHistoryEvent,
        IntelligenceReportEvent,
    ],
    Field(discriminator="event_type"),
]

#: Payload model per event type (single source for validation + documentation).
EVENT_PAYLOAD_MODELS: Dict[EventType, type] = {
    EventType.PERSON_UPSERT: Person,
    EventType.PHONE_UPSERT: Phone,
    EventType.BANK_ACCOUNT_UPSERT: BankAccount,
    EventType.VEHICLE_UPSERT: Vehicle,
    EventType.SOCIAL_HANDLE_UPSERT: SocialHandle,
    EventType.IP_ADDRESS_UPSERT: IPAddress,
    EventType.LOCATION_UPSERT: Location,
    EventType.CELL_TOWER_UPSERT: CellTower,
    EventType.SOURCE_RECORD_UPSERT: SourceRecord,
    EventType.FIR_UPSERT: FIR,
    EventType.COMMUNICATION: CommunicationRecord,
    EventType.TRANSACTION: TransactionRecord,
    EventType.SURVEILLANCE_LOG: SurveillanceLogRecord,
    EventType.CRIMINAL_HISTORY: CriminalHistoryRecord,
    EventType.INTELLIGENCE_REPORT: IntelligenceReportRecord,
}


class EventBatch(BaseModel):
    """A batch of events for ONE existing case, applied in one write transaction."""
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., description="Target case. Must already exist (events never create Case nodes).")
    events: List[GraphEvent] = Field(..., min_length=1, max_length=5000, description="Events to apply, in order")
    batch_id: Optional[str] = Field(default=None, description="Optional client batch identifier (echoed back)")


# ---------------------------------------------------------------------------
# Touched entities (input to the scoped detector dispatcher)
# ---------------------------------------------------------------------------

class EntityKind(str, Enum):
    """Kinds tracked for detector dispatch. Values name the graph label/relationship they represent."""
    PERSON = "Person"
    PHONE = "Phone"
    BANK_ACCOUNT = "BankAccount"
    VEHICLE = "Vehicle"
    SOCIAL_HANDLE = "SocialHandle"
    IP_ADDRESS = "IPAddress"
    LOCATION = "Location"
    CELL_TOWER = "CellTower"
    PRIOR_CASE = "PriorCase"
    # relationship kinds (identity = the endpoints recorded in the node sets above)
    CALLED = "CALLED"
    TRANSFERRED_TO = "TRANSFERRED_TO"
    LOCATED_AT = "LOCATED_AT"
    HAS_PRIOR_CASE = "HAS_PRIOR_CASE"
    OWNS = "OWNS"
    HAS_HANDLE = "HAS_HANDLE"


class TouchedEntities(BaseModel):
    """
    Identities affected by a batch, keyed by the MERGE key each detector anchors on.

    Populated from (a) entity payload keys, (b) ``owner_person_id`` side effects,
    (c) BOTH endpoints of every relationship record, and (d) entities whose
    ``case_ids`` list changed (reported by the write statements). Relationship
    kinds are recorded in ``relationship_kinds`` so the dispatcher can select
    detectors; their identities live in the node sets.
    """
    persons: Set[str] = Field(default_factory=set, description="Person.person_id")
    phones: Set[str] = Field(default_factory=set, description="Phone.phone_number")
    bank_accounts: Set[str] = Field(default_factory=set, description="BankAccount.account_number")
    vehicles: Set[str] = Field(default_factory=set, description="Vehicle.vin")
    social_handles: Set[str] = Field(default_factory=set, description="SocialHandle.handle_id")
    ip_addresses: Set[str] = Field(default_factory=set, description="IPAddress.ip_address")
    locations: Set[str] = Field(default_factory=set, description="Location.location_id")
    cell_towers: Set[str] = Field(default_factory=set, description="CellTower.cell_tower_id")
    prior_cases: Set[str] = Field(default_factory=set, description="PriorCase.prior_case_id")
    relationship_kinds: Set[EntityKind] = Field(default_factory=set, description="Relationship kinds written by the batch")
    case_ids_changed: Set[str] = Field(
        default_factory=set,
        description="'<Label>:<key>' of nodes whose case_ids list gained the batch's case (cross-case triggers)",
    )

    def is_empty(self) -> bool:
        return not any([
            self.persons, self.phones, self.bank_accounts, self.vehicles, self.social_handles,
            self.ip_addresses, self.locations, self.cell_towers, self.prior_cases, self.relationship_kinds,
        ])

    def node_kinds(self) -> Set[EntityKind]:
        kinds: Set[EntityKind] = set()
        for kind, ids in (
            (EntityKind.PERSON, self.persons), (EntityKind.PHONE, self.phones),
            (EntityKind.BANK_ACCOUNT, self.bank_accounts), (EntityKind.VEHICLE, self.vehicles),
            (EntityKind.SOCIAL_HANDLE, self.social_handles), (EntityKind.IP_ADDRESS, self.ip_addresses),
            (EntityKind.LOCATION, self.locations), (EntityKind.CELL_TOWER, self.cell_towers),
            (EntityKind.PRIOR_CASE, self.prior_cases),
        ):
            if ids:
                kinds.add(kind)
        return kinds

    def kinds(self) -> Set[EntityKind]:
        return self.node_kinds() | set(self.relationship_kinds)


# ---------------------------------------------------------------------------
# Processing result
# ---------------------------------------------------------------------------

class WriteCounts(BaseModel):
    nodes_created: int = Field(default=0, description="Primary nodes reported as created by the write statements")
    nodes_matched: int = Field(default=0, description="Primary nodes matched (pre-existing) or not written (missing MATCH target)")
    relationships_written: int = Field(default=0, description="Relationship writes attempted, counted per input record (bulk-ingest convention)")
    statements_executed: int = Field(default=0, description="Cypher statements executed for the batch (excluding detectors)")


class DetectorRun(BaseModel):
    detector: str = Field(..., description="Detector function name")
    insight_type: str = Field(..., description="Insight type the detector produces")
    status: Literal["ok", "failed"] = "ok"
    insights: int = Field(default=0, description="Insights returned by this detector")
    error: Optional[str] = Field(default=None, description="Error message when status == 'failed'")


class EventProcessingResult(BaseModel):
    case_id: str
    batch_id: Optional[str] = None
    event_ids: List[Optional[str]] = Field(default_factory=list, description="event_id of each processed event, in order")
    events_by_type: Dict[str, int] = Field(default_factory=dict)
    writes: WriteCounts = Field(default_factory=WriteCounts)
    touched: TouchedEntities = Field(default_factory=TouchedEntities)
    detectors_run: List[DetectorRun] = Field(default_factory=list, description="Every detector selected by the dispatcher, with status")
    detectors_failed: List[str] = Field(default_factory=list, description="Names of detectors that raised (never silently dropped)")
    detectors_skipped: List[str] = Field(default_factory=list, description="Detectors not selected because no dependency was touched")
    insights: List[InsightItem] = Field(default_factory=list, description="Scoped insights involving touched entities")
    warnings: List[str] = Field(default_factory=list)
    processed_at: str = Field(default_factory=get_current_iso_time)
