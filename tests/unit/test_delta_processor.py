"""
Phase 2 / B -- app/services/delta_processor.py (mock-session level).

Verifies, without Neo4j:
* the processor issues ONLY statements defined in graph_writes (no duplicated write logic);
* plan order mirrors bulk ingestion (People before OWNS / FIR-accused / HAS_PRIOR_CASE; Locations before LOCATED_AT);
* parameter rows are built by the shared row builders (identical to bulk for the same records);
* everything runs in one write transaction; the case must exist; write counts follow the bulk convention;
* touched-entity derivation: payload keys, owner side effects, both relationship endpoints, implicit nodes,
  fallback identifiers, case_ids joins;
* detector runner integration: results and failures are surfaced, never swallowed; default runner warns.
"""
import json
import os
import re
import sys

import pytest

from tests.conftest import MockSession, MockRecord, MockResult  # noqa: E402

from backend.models.case_input import CaseData  # noqa: E402
from backend.models.event import DetectorRun, EntityKind, EventBatch, EventType, TouchedEntities  # noqa: E402
from backend.models.insights import InsightItem  # noqa: E402
from backend.services import graph_writes as gw  # noqa: E402
from backend.services.delta_processor import (  # noqa: E402
    NO_DETECTOR_RUNNER_WARNING, CaseNotFoundError, DeltaProcessor, _no_detectors,
)
from backend.services.ingestion_service import IngestionService  # noqa: E402
from backend.services.insights_engine import InsightsEngine  # noqa: E402

SAMPLE_001 = os.path.join("dataset", "case_001_homicide.json")
EDGE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "case_edge_batching.json")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _doc(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def events_from_case_doc(d):
    """Replay every persisted record of a CaseData document as individual events (same order as bulk steps)."""
    evs = []
    add = lambda t, p: evs.append({"event_type": t, "payload": p})
    for x in d.get("source_records", []): add("SOURCE_RECORD_UPSERT", x)
    for x in d.get("fir_records", []): add("FIR_UPSERT", x)
    ents = d.get("entities", {})
    for key, t in [("people", "PERSON_UPSERT"), ("phones", "PHONE_UPSERT"), ("bank_accounts", "BANK_ACCOUNT_UPSERT"),
                   ("vehicles", "VEHICLE_UPSERT"), ("social_handles", "SOCIAL_HANDLE_UPSERT"),
                   ("ip_addresses", "IP_ADDRESS_UPSERT"), ("locations", "LOCATION_UPSERT"), ("cell_towers", "CELL_TOWER_UPSERT")]:
        for x in ents.get(key, []): add(t, x)
    rels = d.get("relationships", {})
    for x in rels.get("communications", []): add("COMMUNICATION", x)
    for x in rels.get("transactions", []): add("TRANSACTION", x)
    for x in d.get("surveillance_logs", []): add("SURVEILLANCE_LOG", x)
    for x in d.get("criminal_history", []): add("CRIMINAL_HISTORY", x)
    for x in d.get("intelligence_reports", []): add("INTELLIGENCE_REPORT", x)
    return evs


class EventMockSession(MockSession):
    """MockSession that (a) knows whether the Case exists and (b) answers case-membership pre/post reads."""

    def __init__(self, case_exists=True, existing_in_case=(), existing_not_in_case=()):
        super().__init__()
        self.case_exists = case_exists
        self.in_case = set(existing_in_case)          # "<Label>:<key>" already carrying the case
        self.not_in_case = set(existing_not_in_case)  # "<Label>:<key>" existing WITHOUT the case (-> join)
        self.writes_done = False

    def run(self, query, parameters=None):
        q = " ".join(query.split())
        params = parameters or {}
        if q == " ".join(gw.CASE_EXISTS_QUERY.split()):
            self.queries.append((query, params))
            return MockResult([MockRecord({"exists": self.case_exists})])
        m = re.match(r"MATCH \(n:(\w+)\) WHERE n\.\w+ IN \$keys RETURN n\.\w+ AS key, \$case_id IN coalesce\(n\.case_ids, \[\]\) AS in_case", q)
        if m:
            self.queries.append((query, params))
            label = m.group(1)
            out = []
            for k in params["keys"]:
                item = f"{label}:{k}"
                if item in self.in_case:
                    out.append(MockRecord({"key": k, "in_case": True}))
                elif item in self.not_in_case:
                    # before the writes: not in case; after: joined (MERGE appended the case)
                    out.append(MockRecord({"key": k, "in_case": self.writes_done}))
            return MockResult(out)
        if q.startswith("UNWIND $rows AS row") or q.startswith("MERGE (f:FIR") or q.startswith("MATCH (f:FIR"):
            self.writes_done = True
        if _is_scoped_detector_query(q):
            # Scoped detector reads: the base MockSession's generic "match (p:person)" stub would return a
            # Person row lacking detector columns; answer with an empty result instead (dispatch tests only).
            self.queries.append((query, params))
            return MockResult([])
        return super().run(query, parameters)


def _norm(q):
    return " ".join(q.split())


def _is_scoped_detector_query(q: str) -> bool:
    return ("CALL {" in q) or ("$threshold" in q) or ("touched_imei" in q) or ("LINKED_TO_IP" in q)


def _write_queries(session):
    known = {_norm(s): name for name, s in gw.ALL_WRITE_STATEMENTS.items()}
    out = []
    for q, p in session.queries:
        n = _norm(q)
        if n in known:
            out.append((known[n], p))
    return out


# ---------------------------------------------------------------------------
# 1. single source of truth
# ---------------------------------------------------------------------------

def test_processor_issues_only_graph_writes_statements():
    """Write path only (detectors opted out): every statement must come from graph_writes."""
    batch = EventBatch(case_id="CASE-EDGE-001", events=events_from_case_doc(_doc(EDGE)))
    session = EventMockSession()
    DeltaProcessor(detector_runner=_no_detectors).process_events(session, batch)
    known = {_norm(s) for s in gw.ALL_WRITE_STATEMENTS.values()}
    read_only = {_norm(gw.CASE_EXISTS_QUERY)} | {_norm(gw.case_membership_query(l)) for l in gw.MERGE_KEYS}
    for q, _ in session.queries:
        assert _norm(q) in known or _norm(q) in read_only, f"statement not defined in graph_writes:\n{q}"
    assert session.write_transactions == 1


def test_delta_processor_module_contains_no_cypher_of_its_own():
    """Guard: the processor must reference graph_writes statements, never carry Cypher text itself."""
    import ast
    src = open(os.path.join("backend", "services", "delta_processor.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    # collect every string literal that is NOT a docstring
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                doc_nodes.add(id(node.body[0].value))
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes]
    for lit in literals:
        for token in ("MERGE ", "MATCH ", "CREATE ", "UNWIND ", "DETACH DELETE", "SET ", "LINKED_TO_IP"):
            assert token not in lit, f"delta_processor.py contains inline Cypher-like literal: {lit!r}"


# ---------------------------------------------------------------------------
# 2. plan: order + reuse of row builders
# ---------------------------------------------------------------------------

def test_plan_order_mirrors_bulk_ingestion():
    batch = EventBatch(case_id="CASE-EDGE-001", events=events_from_case_doc(_doc(EDGE)))
    names = [s["name"] for s in DeltaProcessor.build_plan(batch, "NOW")]
    expected = [
        "SOURCE_RECORDS_MERGE", "FIR_MERGE", "PEOPLE_MERGE", "FIR_ACCUSED_INVOLVES", "FIR_ACCUSED_INVOLVES",
        "PHONES_MERGE", "PHONE_OWNS", "BANK_ACCOUNTS_MERGE", "BANK_OWNS", "VEHICLES_MERGE", "VEHICLE_OWNS",
        "LOCATIONS_MERGE", "CALLED_MERGE", "TRANSACTIONS_MERGE", "SURVEILLANCE_LOCATIONS_MERGE",
        "SURVEILLANCE_PERSON_LOCATED_AT", "SURVEILLANCE_VEHICLE_LOCATED_AT", "PRIOR_CASES_MERGE", "INTEL_REPORTS_MERGE",
    ]
    assert names == expected
    # No Case step: events never create/update Case metadata
    assert "CASE_MERGE" not in names
    # order does not depend on event order in the batch
    shuffled = EventBatch(case_id="CASE-EDGE-001", events=list(reversed(events_from_case_doc(_doc(EDGE)))))
    assert [s["name"] for s in DeltaProcessor.build_plan(shuffled, "NOW")] == expected


def test_plan_rows_equal_bulk_rows_for_same_records():
    """Same record => same parameter row, because both paths call the same graph_writes builders."""
    d = _doc(SAMPLE_001)
    cd = CaseData(**d)
    plan = {s["name"]: s for s in DeltaProcessor.build_plan(EventBatch(case_id=cd.case_metadata.case_id, events=events_from_case_doc(d)), "NOW")}
    assert plan["PEOPLE_MERGE"]["rows"] == [gw.person_row(x) for x in cd.entities.people]
    assert plan["PHONES_MERGE"]["rows"] == [gw.phone_row(x) for x in cd.entities.phones]
    assert plan["PHONE_OWNS"]["rows"] == gw.phone_owns_rows([gw.phone_row(x) for x in cd.entities.phones])
    assert plan["CALLED_MERGE"]["rows"] == [gw.communication_row(x) for x in cd.relationships.communications]
    assert plan["TRANSACTIONS_MERGE"]["rows"] == [gw.transaction_row(x) for x in cd.relationships.transactions]
    assert plan["SURVEILLANCE_PERSON_LOCATED_AT"]["rows"] == [r for lg in cd.surveillance_logs for r in gw.surveillance_person_rows(lg)]
    assert plan["PRIOR_CASES_MERGE"]["rows"] == [gw.criminal_history_row(x) for x in cd.criminal_history]
    assert plan["INTEL_REPORTS_MERGE"]["rows"] == [gw.intelligence_report_row(x) for x in cd.intelligence_reports]
    assert plan["FIR_MERGE"]["params"] == {**gw.fir_params(cd.fir_records[0]), "case_id": "CASE-2024-001", "now": "NOW"}
    # batch params carry case_id/now exactly like bulk; LOCATED_AT batches carry no $now (as in bulk)
    assert plan["CALLED_MERGE"]["params"] == {"case_id": "CASE-2024-001", "now": "NOW"}
    assert plan["SURVEILLANCE_PERSON_LOCATED_AT"]["params"] == {"case_id": "CASE-2024-001"}
    assert plan["PHONE_OWNS"]["params"] == {}


def test_executed_write_statements_and_params_match_bulk_ingestion(monkeypatch):
    """Replaying case_001 as events executes the same (statement, params) sequence bulk ingestion does,
    minus the Case MERGE (events require an existing case)."""
    d = _doc(SAMPLE_001)
    bulk = MockSession()
    monkeypatch.setattr(InsightsEngine, "run_all_detectors", staticmethod(lambda *a, **k: []))
    IngestionService.ingest_case(bulk, CaseData(**d))
    bulk_writes = [(n, {k: v for k, v in p.items() if k != "now"}) for n, p in _write_queries(bulk)]

    ev = EventMockSession()
    DeltaProcessor(detector_runner=_no_detectors).process_events(ev, EventBatch(case_id="CASE-2024-001", events=events_from_case_doc(d)))
    ev_writes = [(n, {k: v for k, v in p.items() if k != "now"}) for n, p in _write_queries(ev)]

    assert bulk_writes[0][0] == "CASE_MERGE"
    assert ev_writes == bulk_writes[1:]


# ---------------------------------------------------------------------------
# 3. transaction, case guard, counts
# ---------------------------------------------------------------------------

def test_case_must_exist():
    session = EventMockSession(case_exists=False)
    with pytest.raises(CaseNotFoundError):
        DeltaProcessor().process_events(session, EventBatch(case_id="NOPE", events=[{"event_type": "PERSON_UPSERT", "payload": {"person_id": "P1", "name": "X"}}]))
    assert session.write_transactions == 0
    assert all(_norm(q) == _norm(gw.CASE_EXISTS_QUERY) for q, _ in session.queries)


def test_single_write_transaction_and_counts_follow_bulk_convention():
    d = _doc(EDGE)
    session = EventMockSession()
    res = DeltaProcessor().process_events(session, EventBatch(case_id="CASE-EDGE-001", events=events_from_case_doc(d), batch_id="B1"))
    assert session.write_transactions == 1
    # 19 write statements for this fixture (see plan test)
    assert res.writes.statements_executed == 19
    # relationships counted per input record like bulk: 44 for this fixture (bulk created.relationships == 44)
    assert res.writes.relationships_written == 44
    # primary-node rows: 1 FIR + 2 people + 2 phones + 2 banks + 1 vehicle + 1 location + 3 tx + 3 prior + 3 intel + 1 source = 19
    # (bulk reports 20 because it also counts the Case node, which events never write)
    assert res.writes.nodes_created == 19 and res.writes.nodes_matched == 0
    assert res.batch_id == "B1" and res.case_id == "CASE-EDGE-001"
    assert res.events_by_type["COMMUNICATION"] == 4 and res.events_by_type["SURVEILLANCE_LOG"] == 3
    assert len(res.event_ids) == len(events_from_case_doc(d)) == sum(res.events_by_type.values())


# ---------------------------------------------------------------------------
# 4. touched entities
# ---------------------------------------------------------------------------

def test_touched_entities_from_edge_fixture():
    t = DeltaProcessor.touched_from_batch(EventBatch(case_id="CASE-EDGE-001", events=events_from_case_doc(_doc(EDGE))))
    # explicit entities + FIR accused + surveillance observed + criminal-history persons (incl. missing ones: generous by design)
    assert t.persons == {"P-E1", "P-E2", "P-MISSING"}
    # declared phones + BOTH endpoints of every call, incl. the implicitly created +91E3
    assert t.phones == {"+91E1", "+91E2", "+91E3"}
    # declared accounts + both endpoints incl. implicit ACC-E3
    assert t.bank_accounts == {"ACC-E1", "ACC-E2", "ACC-E3"}
    # declared vehicle + observed vehicles (incl. missing one)
    assert t.vehicles == {"VIN-E1", "VIN-MISSING"}
    # declared location + surveillance fallback id for the log without location_id
    assert t.locations == {"LOC-E1", "LOC_SURV-E-2"}
    assert t.prior_cases == {"CRIM-E-1", "CRIM-E-2"}
    assert t.social_handles == set() and t.ip_addresses == set() and t.cell_towers == set()
    assert t.relationship_kinds == {EntityKind.OWNS, EntityKind.CALLED, EntityKind.TRANSFERRED_TO, EntityKind.LOCATED_AT, EntityKind.HAS_PRIOR_CASE}


def test_owner_side_effects_mark_owner_person_as_touched():
    b = EventBatch(case_id="C", events=[
        {"event_type": "PHONE_UPSERT", "payload": {"phone_number": "+1", "owner_person_id": "P-OWNER"}},
        {"event_type": "SOCIAL_HANDLE_UPSERT", "payload": {"handle_id": "tg_x", "platform": "Telegram", "handle": "x", "owner_person_id": "P-SOC"}},
        {"event_type": "VEHICLE_UPSERT", "payload": {"vin": "V1"}},  # no owner -> no person touched
    ])
    t = DeltaProcessor.touched_from_batch(b)
    assert t.persons == {"P-OWNER", "P-SOC"}
    assert t.relationship_kinds == {EntityKind.OWNS, EntityKind.HAS_HANDLE}
    assert t.phones == {"+1"} and t.social_handles == {"tg_x"} and t.vehicles == {"V1"}


def test_relationship_events_touch_both_endpoints_and_kind():
    b = EventBatch(case_id="C", events=[
        {"event_type": "COMMUNICATION", "payload": {"source_phone": "+A", "target_phone": "+B", "timestamp": "T"}},
        {"event_type": "TRANSACTION", "payload": {"transaction_id": "T1", "source_account": "X", "target_account": "Y", "amount": 1.0, "timestamp": "T"}},
        {"event_type": "CRIMINAL_HISTORY", "payload": {"record_id": "R1", "person_id": "P9", "case_number": "CC"}},
        {"event_type": "CELL_TOWER_UPSERT", "payload": {"cell_tower_id": "TW", "location_id": "L9"}},
        {"event_type": "SURVEILLANCE_LOG", "payload": {"log_id": "S1", "timestamp": "T", "observed_person_ids": ["P1"], "observed_vehicle_vins": ["V1"], "observed_phone_numbers": ["+Z"]}},
    ])
    t = DeltaProcessor.touched_from_batch(b)
    assert t.phones == {"+A", "+B"}                 # observed_phone_numbers (+Z) is NOT persisted -> not touched
    assert t.bank_accounts == {"X", "Y"}
    assert t.persons == {"P9", "P1"} and t.prior_cases == {"R1"}
    assert t.cell_towers == {"TW"} and t.locations == {"L9", "LOC_S1"} and t.vehicles == {"V1"}
    assert t.relationship_kinds == {EntityKind.CALLED, EntityKind.TRANSFERRED_TO, EntityKind.HAS_PRIOR_CASE, EntityKind.LOCATED_AT}


def test_non_detector_records_do_not_touch_anything():
    b = EventBatch(case_id="C", events=[
        {"event_type": "SOURCE_RECORD_UPSERT", "payload": {"source_record_id": "SR", "source_type": "CDR"}},
        {"event_type": "INTELLIGENCE_REPORT", "payload": {"report_id": "IR", "source_agency": "A", "entities_mentioned": ["P-1"]}},
        {"event_type": "FIR_UPSERT", "payload": {"fir_id": "F", "fir_number": "1", "police_station": "PS"}},
    ])
    assert DeltaProcessor.touched_from_batch(b).is_empty()


def test_case_ids_join_detection_uses_pre_and_post_reads():
    """A touched node that existed WITHOUT the batch's case before the writes and carries it afterwards
    is reported in touched.case_ids_changed; nodes already in the case, or never written, are not."""
    session = EventMockSession(
        existing_in_case={"Person:P-OLD"},                    # already in case -> not a join
        existing_not_in_case={"Phone:+SHARED", "Person:P-X"},  # existed in another case -> join
    )
    b = EventBatch(case_id="CASE-B", events=[
        {"event_type": "PERSON_UPSERT", "payload": {"person_id": "P-OLD", "name": "Old"}},
        {"event_type": "PERSON_UPSERT", "payload": {"person_id": "P-X", "name": "X"}},
        {"event_type": "PERSON_UPSERT", "payload": {"person_id": "P-NEW", "name": "New"}},   # brand new node -> not a join
        {"event_type": "COMMUNICATION", "payload": {"source_phone": "+SHARED", "target_phone": "+NEW", "timestamp": "T"}},
    ])
    res = DeltaProcessor().process_events(session, b)
    assert res.touched.case_ids_changed == {"Phone:+SHARED", "Person:P-X"}
    # membership reads happen for touched labels only, with sorted keys, before and after the writes
    reads = [(q, p) for q, p in session.queries if "AS in_case" in q]
    assert reads[0][1]["keys"] == ["P-NEW", "P-OLD", "P-X"] and reads[0][1]["case_id"] == "CASE-B"
    assert any(p["keys"] == ["+NEW", "+SHARED"] for _, p in reads)


# ---------------------------------------------------------------------------
# 5. detector runner integration
# ---------------------------------------------------------------------------

def _insight(i):
    return InsightItem(insight_id=f"INS-{i}", insight_type="SHARED_ENTITY", title="t", observed_facts=["f"], derived_interpretation="d")


def test_detector_results_and_failures_are_surfaced():
    captured = {}

    def runner(session, touched):
        captured["touched"] = touched
        return ([_insight(1), _insight(2)],
                [DetectorRun(detector="detect_shared_entities_scoped", insight_type="SHARED_ENTITY", status="ok", insights=2),
                 DetectorRun(detector="detect_transfer_chains_scoped", insight_type="TRANSFER_CHAIN", status="failed", error="boom")],
                ["detect_prior_case_links_scoped"])

    session = EventMockSession()
    res = DeltaProcessor(detector_runner=runner).process_events(
        session, EventBatch(case_id="C", events=[{"event_type": "PHONE_UPSERT", "payload": {"phone_number": "+1", "owner_person_id": "P1"}}]))
    assert captured["touched"].phones == {"+1"} and captured["touched"].persons == {"P1"}
    assert [i.insight_id for i in res.insights] == ["INS-1", "INS-2"]
    assert res.detectors_failed == ["detect_transfer_chains_scoped"]
    assert [d.status for d in res.detectors_run] == ["ok", "failed"]
    assert res.detectors_run[1].error == "boom"
    assert res.detectors_skipped == ["detect_prior_case_links_scoped"]
    assert NO_DETECTOR_RUNNER_WARNING not in res.warnings


def test_opt_out_runner_warns_that_no_insights_were_computed():
    session = EventMockSession()
    res = DeltaProcessor(detector_runner=_no_detectors).process_events(
        session, EventBatch(case_id="C", events=[{"event_type": "PERSON_UPSERT", "payload": {"person_id": "P1", "name": "X"}}]))
    assert res.insights == [] and res.detectors_run == [] and res.detectors_failed == []
    assert NO_DETECTOR_RUNNER_WARNING in res.warnings


def test_default_runner_is_the_scoped_dispatcher():
    """With no runner injected, the processor dispatches the scoped detectors in GLOBAL scope
    (case_id=None) and reports selected / skipped detectors in the result."""
    session = EventMockSession()
    res = DeltaProcessor().process_events(
        session, EventBatch(case_id="C", events=[{"event_type": "CRIMINAL_HISTORY", "payload": {"record_id": "R1", "person_id": "P1", "case_number": "CC"}}]))
    assert NO_DETECTOR_RUNNER_WARNING not in res.warnings
    ran = {d.insight_type for d in res.detectors_run}
    # Person + PriorCase + HAS_PRIOR_CASE touched -> PRIOR_CASE_LINK and the Person-triggered detectors run,
    # while bank/phone-only detectors are skipped.
    assert "PRIOR_CASE_LINK" in ran and "SHARED_ENTITY" in ran and "BRIDGE_NODE" in ran
    assert {"TRANSFER_CHAIN", "HIGH_FAN_IN", "HIGH_FAN_OUT", "CROSS_CASE_LINK", "INFRASTRUCTURE_REUSE"} <= set(res.detectors_skipped)
    # every scoped query that carries a case filter ran in GLOBAL scope
    detector_queries = [p for q, p in session.queries if _is_scoped_detector_query(q) and "case_id" in p]
    assert detector_queries and all(p["case_id"] is None for p in detector_queries)
    assert res.detectors_failed == []


def test_unpersisted_fields_produce_warnings():
    session = EventMockSession()
    res = DeltaProcessor().process_events(session, EventBatch(case_id="C", events=[
        {"event_type": "SURVEILLANCE_LOG", "payload": {"log_id": "S", "timestamp": "T", "observed_phone_numbers": ["+1", "+2"]}},
        {"event_type": "INTELLIGENCE_REPORT", "payload": {"report_id": "R", "source_agency": "A", "entities_mentioned": ["P"]}},
    ]))
    assert any("2 observed_phone_numbers" in w for w in res.warnings)
    assert any("1 entities_mentioned" in w for w in res.warnings)
