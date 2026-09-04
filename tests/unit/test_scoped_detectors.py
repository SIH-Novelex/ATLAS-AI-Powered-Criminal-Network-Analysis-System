"""
Phase 2 / stage 3 -- app/services/scoped_detectors.py (mock-session level).

Per scoped detector: the Cypher it issues (a) keeps the full detector's semantic predicates
and case filter verbatim, (b) anchors on touched entity identity at EVERY relevant pattern
position, (c) passes sorted touched identities as parameters, and (d) reuses the full
detector's Python formatting (same insight ids / fields for the same rows).

Dispatcher: dependency matrix, skip of irrelevant detectors, and every trigger route
(new node, case_ids join, relationship, owner relationship, either endpoint), plus
failure reporting and global scope.

No Neo4j needed; see tests/test_live_neo4j_scoped_detectors.py for live anchor checks.
"""
import os
import re
import sys

import pytest

from tests.conftest import MockRecord, MockResult  # noqa: E402

from backend.models.event import DetectorRun, EntityKind, TouchedEntities  # noqa: E402
from backend.services import scoped_detectors as sd  # noqa: E402
from backend.services.insights_engine import InsightsEngine  # noqa: E402
from backend.services.scoped_detectors import (  # noqa: E402
    CASE_JOIN_TRIGGERS, DETECTOR_ORDER, DETECTOR_TRIGGERS, FULL_LIMITS, SCOPED_DETECTORS,
    ScopedInsightsEngine, make_detector_runner, run_scoped_detectors, select_detectors,
)

ALL_TYPES = set(DETECTOR_ORDER)


def _norm(q: str) -> str:
    return " ".join(q.split())


class RecordingSession:
    """Returns canned rows (by query index) and records (cypher, params)."""

    def __init__(self, rows_per_query=None):
        self.rows_per_query = list(rows_per_query or [])
        self.calls = []

    def run(self, query, parameters=None):
        self.calls.append((query, parameters or {}))
        rows = self.rows_per_query.pop(0) if self.rows_per_query else []
        return MockResult([MockRecord(r) for r in rows])


class FailingSession(RecordingSession):
    def __init__(self, fail_when):
        super().__init__()
        self.fail_when = fail_when

    def run(self, query, parameters=None):
        super().run(query, parameters)
        if self.fail_when(query):
            raise RuntimeError("simulated Neo4j failure")
        return MockResult([])


def _full_cypher(full_detector):
    """The Cypher string(s) the FULL detector issues (captured through a recording session)."""
    s = RecordingSession()
    full_detector(s, None)
    return [_norm(q) for q, _ in s.calls]


def _tail_after_anchor(scoped_q: str) -> str:
    """Everything after the anchor block (`WITH DISTINCT ...` onwards) of a scoped query."""
    n = _norm(scoped_q)
    i = n.find("WITH DISTINCT")
    return n[i:] if i >= 0 else n


T_ALL = TouchedEntities(persons={"P2", "P1"}, phones={"+2", "+1"}, bank_accounts={"B2", "B1"}, vehicles={"V"},
                        social_handles={"S"}, ip_addresses={"9.9.9.9"}, locations={"L"}, cell_towers={"T"}, prior_cases={"PC"})


# ===========================================================================
# 1. Registry / structural guarantees for ALL scoped detectors
# ===========================================================================

def test_registry_covers_all_ten_types_in_full_engine_order():
    assert DETECTOR_ORDER == ["SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "TRANSFER_CHAIN", "HIGH_FAN_IN",
                              "HIGH_FAN_OUT", "INFRASTRUCTURE_REUSE", "POSSIBLE_CO_LOCATION", "CROSS_DOMAIN_PATH", "PRIOR_CASE_LINK"]
    for name, (scoped, full) in SCOPED_DETECTORS.items():
        assert scoped.__name__ == full.__name__ + "_scoped"
        assert getattr(InsightsEngine, full.__name__) is full  # full detectors untouched & still the reference


def test_full_detectors_are_unmodified_reference_implementation():
    """The full detectors' Cypher must still be the original full-graph queries (no anchors leaked in)."""
    for name, (_scoped, full) in SCOPED_DETECTORS.items():
        for q in _full_cypher(full):
            assert "CALL {" not in q and "IN $persons" not in q and "IN $phones" not in q and "IN $bank_accounts" not in q, name
    assert "LIMIT 25" in _full_cypher(InsightsEngine.detect_transfer_chains)[0]
    assert "LIMIT 20" in _full_cypher(InsightsEngine.detect_possible_co_location)[0]
    assert "LIMIT 10" in _full_cypher(InsightsEngine.detect_cross_domain_paths)[0]


@pytest.mark.parametrize("name", DETECTOR_ORDER)
def test_scoped_params_are_sorted_lists_and_case_id_is_passed_through(name):
    scoped, _ = SCOPED_DETECTORS[name]
    s = RecordingSession()
    scoped(s, T_ALL, "CASE-Z")
    assert s.calls, name
    for _q, p in s.calls:
        for k, v in p.items():
            if isinstance(v, list):
                assert v == sorted(v), f"{name}: param {k} not sorted"
        if "case_id" in p:
            assert p["case_id"] == "CASE-Z"
    # INFRASTRUCTURE_REUSE keeps case filtering in Python: its Cypher carries NO $case_id (like the full detector)
    if name == "INFRASTRUCTURE_REUSE":
        assert all("case_id" not in p for _q, p in s.calls)
        assert all("$case_id" not in q for q, _p in s.calls)
    else:
        assert all("$case_id" in q and "case_id" in p for q, p in s.calls)


@pytest.mark.parametrize("name", DETECTOR_ORDER)
def test_scoped_query_does_not_fall_back_to_full_query(name):
    scoped, full = SCOPED_DETECTORS[name]
    s = RecordingSession()
    scoped(s, T_ALL, None)
    full_qs = set(_full_cypher(full))
    for q, _ in s.calls:
        assert _norm(q) not in full_qs, f"{name} issued the unscoped full-graph query"


def test_no_scoped_detector_writes_anything():
    """Every non-docstring string literal in scoped_detectors.py is read-only Cypher (or a plain label)."""
    import ast
    src = open(os.path.join("backend", "services", "scoped_detectors.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                doc_ids.add(id(node.body[0].value))
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]
    assert literals
    for lit in literals:
        for token in ("MERGE ", "CREATE ", "DELETE ", " SET ", "REMOVE "):
            assert token not in lit, f"write-like token {token!r} in literal: {lit[:80]!r}"
    # LINKED_TO_IP appears only as a read pattern inherited from the full detectors
    assert any("-[:LINKED_TO_IP]->" in lit or "OWNS|HAS_HANDLE|LINKED_TO_IP" in lit for lit in literals)


# ===========================================================================
# 2. Per-detector anchor + preserved-semantics assertions
# ===========================================================================

def test_shared_entity_scoped_anchor_and_semantics():
    s = RecordingSession()
    ScopedInsightsEngine.detect_shared_entities_scoped(s, T_ALL, None)
    (q, p), = s.calls
    n = _norm(q)
    for label, key, param in [("Person", "person_id", "persons"), ("Phone", "phone_number", "phones"),
                              ("BankAccount", "account_number", "bank_accounts"), ("Vehicle", "vin", "vehicles"),
                              ("SocialHandle", "handle_id", "social_handles"), ("IPAddress", "ip_address", "ip_addresses")]:
        assert f"MATCH (n:{label}) WHERE n.{key} IN ${param} RETURN n" in n
    assert "size(n.case_ids) > 1" in n and "($case_id IS NULL OR $case_id IN n.case_ids)" in n
    # RETURN clause identical to the full detector's
    full = _full_cypher(InsightsEngine.detect_shared_entities)[0]
    assert n[n.index("RETURN labels(n)"):] == full[full.index("RETURN labels(n)"):]
    assert p["persons"] == ["P1", "P2"] and p["ip_addresses"] == ["9.9.9.9"]


def test_cross_case_link_scoped_anchors_on_either_endpoint_not_call_id():
    s = RecordingSession()
    ScopedInsightsEngine.detect_cross_case_links_scoped(s, TouchedEntities(phones={"+B", "+A"}), None)
    (q, p), = s.calls
    n = _norm(q)
    assert "WHERE p1.phone_number IN $phones RETURN p1, r, p2" in n
    assert "WHERE p2.phone_number IN $phones RETURN p1, r, p2" in n
    assert "call_id" not in n  # never anchored on the new relationship
    # asymmetric cross-case predicate + case filter verbatim
    assert "any(c1 IN p1.case_ids WHERE NOT c1 IN p2.case_ids)" in n
    assert "($case_id IS NULL OR $case_id IN p1.case_ids OR $case_id IN p2.case_ids)" in n
    assert p == {"phones": ["+A", "+B"], "case_id": None}


def test_bridge_node_scoped_selects_person_via_any_asset_and_reaggregates_globally():
    s = RecordingSession()
    ScopedInsightsEngine.detect_bridge_nodes_scoped(s, T_ALL, "C1")
    (q, _p), = s.calls
    n = _norm(q)
    assert "MATCH (p:Person) WHERE p.person_id IN $persons RETURN p" in n
    assert "MATCH (p:Person)-[:OWNS]->(a:Phone) WHERE a.phone_number IN $phones RETURN p" in n
    assert "MATCH (p:Person)-[:OWNS]->(a:BankAccount) WHERE a.account_number IN $bank_accounts RETURN p" in n
    assert "MATCH (p:Person)-[:OWNS]->(a:Vehicle) WHERE a.vin IN $vehicles RETURN p" in n
    assert "MATCH (p:Person)-[:HAS_HANDLE]->(a:SocialHandle) WHERE a.handle_id IN $social_handles RETURN p" in n
    assert "MATCH (p:Person)-[:LINKED_TO_IP]->(a:IPAddress) WHERE a.ip_address IN $ip_addresses RETURN p" in n
    # global re-aggregation over ALL assets of the selected person, same union incl. read-only LINKED_TO_IP
    assert "WITH DISTINCT p MATCH (p)-[:OWNS|HAS_HANDLE|LINKED_TO_IP]->(asset) WITH p, count(DISTINCT asset) as asset_count, p.case_ids as cases" in n
    assert "WHERE size(cases) >= 2 AND asset_count >= 2 AND ($case_id IS NULL OR $case_id IN cases)" in n


def test_transfer_chain_scoped_anchors_all_three_positions_and_keeps_limit_25():
    s = RecordingSession()
    ScopedInsightsEngine.detect_transfer_chains_scoped(s, TouchedEntities(bank_accounts={"B"}), None)
    (q, p), = s.calls
    n = _norm(q)
    pat = "MATCH (b1:BankAccount)-[r1:TRANSFERRED_TO]->(b2:BankAccount)-[r2:TRANSFERRED_TO]->(b3:BankAccount)"
    for pos in ("b1", "b2", "b3"):
        assert f"{pat} WHERE {pos}.account_number IN $bank_accounts RETURN b1, r1, b2, r2, b3" in n
    assert "WITH DISTINCT b1, r1, b2, r2, b3 WHERE b1 <> b3" in n
    assert "any(c IN b1.case_ids WHERE c = $case_id) OR any(c IN b2.case_ids WHERE c = $case_id) OR any(c IN b3.case_ids WHERE c = $case_id)" in n
    assert n.endswith("LIMIT $limit") and p["limit"] == 25 == FULL_LIMITS["TRANSFER_CHAIN"]
    # LIMIT can be lifted for LIMIT-free comparison runs
    s2 = RecordingSession()
    ScopedInsightsEngine.detect_transfer_chains_scoped(s2, TouchedEntities(bank_accounts={"B"}), None, limit=None)
    assert "LIMIT" not in _norm(s2.calls[0][0]) and s2.calls[0][1]["limit"] is None


@pytest.mark.parametrize("fn, role, degree", [
    (ScopedInsightsEngine.detect_high_fan_in_scoped, "target", "in_degree"),
    (ScopedInsightsEngine.detect_high_fan_out_scoped, "src", "out_degree"),
])
def test_fan_in_out_scoped_anchor_on_aggregated_account_and_count_all_edges(fn, role, degree):
    s = RecordingSession()
    fn(s, TouchedEntities(bank_accounts={"B"}), None, threshold=3)
    (q, p), = s.calls
    n = _norm(q)
    assert n.startswith(f"MATCH ({role}:BankAccount) WHERE {role}.account_number IN $bank_accounts")
    # the aggregation edge pattern is unrestricted -> degree counted over every TRANSFERRED_TO of that account
    if role == "target":
        assert "MATCH (src:BankAccount)-[r:TRANSFERRED_TO]->(target) WITH target, count(DISTINCT src) as in_degree" in n
    else:
        assert "MATCH (src)-[r:TRANSFERRED_TO]->(target:BankAccount) WITH src, count(DISTINCT target) as out_degree" in n
    assert f"WHERE {degree} >= $threshold AND ($case_id IS NULL OR $case_id IN cases)" in n
    assert p == {"bank_accounts": ["B"], "case_id": None, "threshold": 3}


def test_infrastructure_reuse_scoped_reaggregates_by_imei_and_keeps_python_case_filter():
    s = RecordingSession()
    ScopedInsightsEngine.detect_infrastructure_reuse_scoped(s, TouchedEntities(phones={"+1"}, ip_addresses={"1.1.1.1"}), None)
    (q_imei, p_imei), (q_ip, p_ip) = s.calls
    n = _norm(q_imei)
    assert "MATCH (anchor:Phone) WHERE anchor.phone_number IN $phones AND anchor.imei IS NOT NULL AND anchor.imei <> ''" in n
    assert "WITH DISTINCT anchor.imei as touched_imei MATCH (ph:Phone {imei: touched_imei})" in n
    # same aggregation + predicate as the full IMEI query
    assert "WITH ph.imei as imei, collect(ph.phone_number) as phones, collect(DISTINCT ph.case_ids) as nested_cases WHERE size(phones) > 1 RETURN imei, phones, nested_cases" in n
    assert "$case_id" not in n and p_imei == {"phones": ["+1"]}
    n2 = _norm(q_ip)
    assert "MATCH (ip:IPAddress) WHERE ip.ip_address IN $ip_addresses MATCH (entity)-[:LINKED_TO_IP]->(ip)" in n2
    assert p_ip == {"ip_addresses": ["1.1.1.1"]}

    # Python case filter preserved: rows whose case set lacks case_id are dropped, exactly like the full detector
    rows = [{"imei": "IMEI-1", "phones": ["+1", "+2"], "nested_cases": [["CASE-A"], ["CASE-A"]]}]
    kept = ScopedInsightsEngine.detect_infrastructure_reuse_scoped(RecordingSession([rows, []]), TouchedEntities(phones={"+1"}), "CASE-A")
    dropped = ScopedInsightsEngine.detect_infrastructure_reuse_scoped(RecordingSession([rows, []]), TouchedEntities(phones={"+1"}), "CASE-B")
    assert len(kept) == 1 and dropped == []
    assert kept[0].insight_id == InsightsEngine.detect_infrastructure_reuse(RecordingSession([rows, []]), "CASE-A")[0].insight_id


def test_co_location_scoped_anchors_location_or_either_end_and_keeps_predicate_verbatim():
    s = RecordingSession()
    ScopedInsightsEngine.detect_possible_co_location_scoped(s, TouchedEntities(locations={"L"}, persons={"P"}, vehicles={"V"}, cell_towers={"T"}), None)
    (q, p), = s.calls
    n = _norm(q)
    pat = "MATCH (e1)-[l1:LOCATED_AT]->(loc:Location)<-[l2:LOCATED_AT]-(e2)"
    assert f"{pat} WHERE loc.location_id IN $locations RETURN e1, l1, loc, l2, e2" in n
    assert f"{pat} WHERE (e1:Person AND e1.person_id IN $persons) OR (e1:Vehicle AND e1.vin IN $vehicles) OR (e1:CellTower AND e1.cell_tower_id IN $cell_towers)" in n
    assert f"{pat} WHERE (e2:Person AND e2.person_id IN $persons) OR (e2:Vehicle AND e2.vin IN $vehicles) OR (e2:CellTower AND e2.cell_tower_id IN $cell_towers)" in n
    # e1/e2 unlabeled in the pattern (as in the full detector); elementId ordering + case predicate VERBATIM
    assert "WHERE elementId(e1) < elementId(e2) AND ($case_id IS NULL OR $case_id IN loc.case_ids OR $case_id IN l1.case_id OR $case_id IN l2.case_id)" in n
    assert n.endswith("LIMIT $limit") and p["limit"] == 20
    full = _full_cypher(InsightsEngine.detect_possible_co_location)[0]
    assert n[n.index("RETURN loc.location_id"):n.index("LIMIT $limit")].strip() == full[full.index("RETURN loc.location_id"):full.index("LIMIT 20")].strip()


def test_cross_domain_path_scoped_anchors_all_six_positions():
    s = RecordingSession()
    ScopedInsightsEngine.detect_cross_domain_paths_scoped(s, TouchedEntities(persons={"P"}, phones={"+1"}, bank_accounts={"B"}), None)
    (q, p), = s.calls
    n = _norm(q)
    pat = "MATCH (p1:Person)-[:OWNS]->(ph1:Phone)-[c:CALLED]->(ph2:Phone)<-[:OWNS]-(p2:Person)-[:OWNS]->(b1:BankAccount)-[t:TRANSFERRED_TO]->(b2:BankAccount)"
    for cond in ("p1.person_id IN $persons", "p2.person_id IN $persons", "ph1.phone_number IN $phones",
                 "ph2.phone_number IN $phones", "b1.account_number IN $bank_accounts", "b2.account_number IN $bank_accounts"):
        assert f"{pat} WHERE {cond} RETURN p1, ph1, c, ph2, p2, b1, t, b2" in n, cond
    assert n.count(pat) == 6
    assert "WITH DISTINCT p1, ph1, c, ph2, p2, b1, t, b2 WHERE p1 <> p2 AND ($case_id IS NULL OR $case_id IN p1.case_ids OR $case_id IN p2.case_ids)" in n
    assert n.endswith("LIMIT $limit") and p["limit"] == 10


def test_prior_case_link_scoped_anchors_person_or_prior_case():
    s = RecordingSession()
    ScopedInsightsEngine.detect_prior_case_links_scoped(s, TouchedEntities(persons={"P"}, prior_cases={"PC"}), "C")
    (q, p), = s.calls
    n = _norm(q)
    assert "MATCH (p:Person)-[:HAS_PRIOR_CASE]->(pc:PriorCase) WHERE p.person_id IN $persons RETURN p, pc" in n
    assert "MATCH (p:Person)-[:HAS_PRIOR_CASE]->(pc:PriorCase) WHERE pc.prior_case_id IN $prior_cases RETURN p, pc" in n
    assert "WITH DISTINCT p, pc WHERE ($case_id IS NULL OR $case_id IN p.case_ids)" in n
    assert p == {"persons": ["P"], "prior_cases": ["PC"], "case_id": "C"}


# ===========================================================================
# 3. Formatting parity: identical rows -> identical InsightItems (ids and all fields but created_at)
# ===========================================================================

_ROWS = {
    "SHARED_ENTITY": [{"labels": ["Phone"], "identifier": "+1", "case_ids": ["A", "B"], "entity_id": "+1"}],
    "CROSS_CASE_LINK": [{"src_phone": "+1", "src_cases": ["A", "B"], "dst_phone": "+2", "dst_cases": ["A"], "timestamp": "T", "duration": 5, "call_case": "A"}],
    "BRIDGE_NODE": [{"person_id": "P", "name": "N", "cases": ["A", "B"], "asset_count": 3}],
    "TRANSFER_CHAIN": [{"acc1": "X", "acc2": "Y", "acc3": "Z", "amt1": 1.0, "amt2": 2.0, "t1": "T1", "t2": "T2", "c1": ["A"], "c2": ["A"], "c3": ["B"]}],
    "HIGH_FAN_IN": [{"account_number": "X", "holder": "H", "in_degree": 4, "total_in": 9.0, "cases": ["A"]}],
    "HIGH_FAN_OUT": [{"account_number": "X", "holder": "H", "out_degree": 4, "total_out": 9.0, "cases": ["A"]}],
    "POSSIBLE_CO_LOCATION": [{"loc_id": "L", "loc_name": "Loc", "entity1": "P1", "entity2": "V1", "t1": "T1", "t2": "T2", "cases": ["A"]}],
    "CROSS_DOMAIN_PATH": [{"p1_name": "One", "p1_id": "P1", "p2_name": "Two", "p2_id": "P2", "ph1": "+1", "ph2": "+2", "b1": "B1", "b2": "B2", "c1": ["A"], "c2": ["A"]}],
    "PRIOR_CASE_LINK": [{"person_id": "P", "name": "N", "prior_id": "PC", "case_num": "CC", "offense": "O", "jurisdiction": "J", "status": "S", "year": 2001, "case_ids": ["A"]}],
}


@pytest.mark.parametrize("name", sorted(_ROWS))
def test_scoped_and_full_produce_identical_insights_for_identical_rows(name):
    scoped, full = SCOPED_DETECTORS[name]
    rows = _ROWS[name]
    full_out = full(RecordingSession([rows]), None)
    scoped_out = scoped(RecordingSession([rows]), T_ALL, None)
    assert len(full_out) == len(scoped_out) == 1
    assert full_out[0].model_dump(exclude={"created_at"}) == scoped_out[0].model_dump(exclude={"created_at"})


def test_infrastructure_reuse_formatting_parity_both_branches():
    rows_imei = [{"imei": "I", "phones": ["+1", "+2"], "nested_cases": [["A"], ["B"]]}]
    rows_ip = [{"ip_address": "1.1.1.1", "entities": ["P1", "P2"], "nested_cases": [["A"]]}]
    full_out = InsightsEngine.detect_infrastructure_reuse(RecordingSession([rows_imei, rows_ip]), None)
    scoped_out = ScopedInsightsEngine.detect_infrastructure_reuse_scoped(RecordingSession([rows_imei, rows_ip]), T_ALL, None)
    assert [i.model_dump(exclude={"created_at"}) for i in full_out] == [i.model_dump(exclude={"created_at"}) for i in scoped_out]
    assert [i.insight_id for i in scoped_out] == ["INS-INFRASTRUCTURE_REUSE-" + full_out[0].insight_id.split("-")[-1], full_out[1].insight_id]


# ===========================================================================
# 4. Dispatcher: matrix, skipping, and every trigger route
# ===========================================================================

def test_dependency_matrix_matches_verification_report():
    K = EntityKind
    assert DETECTOR_TRIGGERS["SHARED_ENTITY"] == {K.PERSON, K.PHONE, K.BANK_ACCOUNT, K.VEHICLE, K.SOCIAL_HANDLE, K.IP_ADDRESS}
    assert DETECTOR_TRIGGERS["CROSS_CASE_LINK"] == {K.PHONE, K.CALLED}
    assert {K.PERSON, K.OWNS, K.HAS_HANDLE} <= DETECTOR_TRIGGERS["BRIDGE_NODE"]
    assert DETECTOR_TRIGGERS["TRANSFER_CHAIN"] == DETECTOR_TRIGGERS["HIGH_FAN_IN"] == DETECTOR_TRIGGERS["HIGH_FAN_OUT"] == {K.BANK_ACCOUNT, K.TRANSFERRED_TO}
    assert DETECTOR_TRIGGERS["INFRASTRUCTURE_REUSE"] == {K.PHONE, K.IP_ADDRESS}
    assert {K.LOCATION, K.CELL_TOWER, K.LOCATED_AT} <= DETECTOR_TRIGGERS["POSSIBLE_CO_LOCATION"]
    assert {K.PERSON, K.PHONE, K.BANK_ACCOUNT, K.OWNS, K.CALLED, K.TRANSFERRED_TO} <= DETECTOR_TRIGGERS["CROSS_DOMAIN_PATH"]
    assert DETECTOR_TRIGGERS["PRIOR_CASE_LINK"] == {K.PERSON, K.PRIOR_CASE, K.HAS_PRIOR_CASE}
    # every kind triggers at least one detector, every detector has at least one trigger
    assert set().union(*DETECTOR_TRIGGERS.values()) == set(EntityKind)
    assert all(DETECTOR_TRIGGERS[n] for n in DETECTOR_ORDER)
    # IPAddress never triggers a write-dependent relationship kind (no LINKED_TO_IP kind exists at all)
    assert "LINKED_TO_IP" not in {k.value for k in EntityKind}
    assert set(CASE_JOIN_TRIGGERS) == {"Person", "Phone", "BankAccount", "Vehicle", "SocialHandle", "IPAddress", "Location", "CellTower", "PriorCase"}


def test_empty_touched_set_runs_nothing():
    selected, skipped = select_detectors(TouchedEntities())
    assert selected == [] and skipped == DETECTOR_ORDER
    insights, runs, sk = run_scoped_detectors(RecordingSession(), TouchedEntities())
    assert insights == [] and runs == [] and sk == DETECTOR_ORDER


def test_irrelevant_detectors_are_skipped():
    # a Location-only touch must not run phone/bank/person detectors
    selected, skipped = select_detectors(TouchedEntities(locations={"L"}))
    assert selected == ["POSSIBLE_CO_LOCATION"]
    assert set(skipped) == ALL_TYPES - {"POSSIBLE_CO_LOCATION"}
    # PriorCase-only touch
    selected, skipped = select_detectors(TouchedEntities(prior_cases={"PC"}))
    assert selected == ["PRIOR_CASE_LINK"]
    # SocialHandle-only touch: shared + bridge only
    selected, _ = select_detectors(TouchedEntities(social_handles={"S"}))
    assert selected == ["SHARED_ENTITY", "BRIDGE_NODE"]
    # IPAddress-only touch: shared, bridge (read-only LINKED_TO_IP union), infra (dead IP branch) -- never a write
    selected, _ = select_detectors(TouchedEntities(ip_addresses={"1.1.1.1"}))
    assert selected == ["SHARED_ENTITY", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE"]


def test_trigger_new_node_created():
    # a brand-new Phone node (no relationships yet) -> phone-dependent detectors run
    selected, skipped = select_detectors(TouchedEntities(phones={"+NEW"}))
    assert selected == ["SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE", "CROSS_DOMAIN_PATH"]
    assert "TRANSFER_CHAIN" in skipped and "POSSIBLE_CO_LOCATION" in skipped and "PRIOR_CASE_LINK" in skipped
    # a brand-new BankAccount
    selected, _ = select_detectors(TouchedEntities(bank_accounts={"B"}))
    assert selected == ["SHARED_ENTITY", "BRIDGE_NODE", "TRANSFER_CHAIN", "HIGH_FAN_IN", "HIGH_FAN_OUT", "CROSS_DOMAIN_PATH"]


def test_trigger_existing_node_gains_case_id():
    """A case_ids join alone (no node kind recorded) must trigger the label's detectors."""
    t = TouchedEntities(case_ids_changed={"Phone:+SHARED"})
    assert t.kinds() == set()  # nothing in the kind sets: the trigger comes purely from the join
    selected, _ = select_detectors(t)
    assert selected == ["SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE", "CROSS_DOMAIN_PATH"]
    selected, _ = select_detectors(TouchedEntities(case_ids_changed={"BankAccount:ACC"}))
    assert selected == ["SHARED_ENTITY", "BRIDGE_NODE", "TRANSFER_CHAIN", "HIGH_FAN_IN", "HIGH_FAN_OUT", "CROSS_DOMAIN_PATH"]
    selected, _ = select_detectors(TouchedEntities(case_ids_changed={"Person:P"}))
    assert selected == ["SHARED_ENTITY", "BRIDGE_NODE", "POSSIBLE_CO_LOCATION", "CROSS_DOMAIN_PATH", "PRIOR_CASE_LINK"]
    selected, _ = select_detectors(TouchedEntities(case_ids_changed={"Location:L"}))
    assert selected == ["POSSIBLE_CO_LOCATION"]


def test_trigger_relationship_created():
    # CALLED (kind only) -> cross-case + cross-domain
    selected, _ = select_detectors(TouchedEntities(relationship_kinds={EntityKind.CALLED}))
    assert selected == ["CROSS_CASE_LINK", "CROSS_DOMAIN_PATH"]
    # TRANSFERRED_TO -> chain, fan-in, fan-out, cross-domain
    selected, _ = select_detectors(TouchedEntities(relationship_kinds={EntityKind.TRANSFERRED_TO}))
    assert selected == ["TRANSFER_CHAIN", "HIGH_FAN_IN", "HIGH_FAN_OUT", "CROSS_DOMAIN_PATH"]
    # LOCATED_AT -> co-location; HAS_PRIOR_CASE -> prior
    assert select_detectors(TouchedEntities(relationship_kinds={EntityKind.LOCATED_AT}))[0] == ["POSSIBLE_CO_LOCATION"]
    assert select_detectors(TouchedEntities(relationship_kinds={EntityKind.HAS_PRIOR_CASE}))[0] == ["PRIOR_CASE_LINK"]


def test_trigger_owner_relationship_created():
    # OWNS / HAS_HANDLE kinds -> bridge (+ cross-domain for OWNS, which sits inside that path)
    assert select_detectors(TouchedEntities(relationship_kinds={EntityKind.OWNS}))[0] == ["BRIDGE_NODE", "CROSS_DOMAIN_PATH"]
    assert select_detectors(TouchedEntities(relationship_kinds={EntityKind.HAS_HANDLE}))[0] == ["BRIDGE_NODE"]
    # realistic: phone upsert with owner -> processor marks owner Person + OWNS; the owner's detectors run too
    from backend.models.event import EventBatch
    from backend.services.delta_processor import DeltaProcessor
    t = DeltaProcessor.touched_from_batch(EventBatch(case_id="C", events=[
        {"event_type": "PHONE_UPSERT", "payload": {"phone_number": "+1", "owner_person_id": "P-OWNER"}}]))
    assert t.persons == {"P-OWNER"} and EntityKind.OWNS in t.relationship_kinds
    selected, _ = select_detectors(t)
    assert "PRIOR_CASE_LINK" in selected and "BRIDGE_NODE" in selected  # person-triggered detectors included


def test_trigger_either_endpoint_of_relationship():
    """Relationship events touch BOTH endpoints; the scoped Cypher anchors on each of them."""
    from backend.models.event import EventBatch
    from backend.services.delta_processor import DeltaProcessor
    t = DeltaProcessor.touched_from_batch(EventBatch(case_id="C", events=[
        {"event_type": "TRANSACTION", "payload": {"transaction_id": "T", "source_account": "SRC", "target_account": "DST", "amount": 1.0, "timestamp": "T"}}]))
    assert t.bank_accounts == {"SRC", "DST"}
    s = RecordingSession()
    run_scoped_detectors(s, t)
    ran = [q for q, _ in s.calls]
    assert any("TRANSFERRED_TO" in q for q in ran)
    for q, p in s.calls:
        if "bank_accounts" in p:
            assert p["bank_accounts"] == ["DST", "SRC"]  # both endpoints, every time
    # fan-in anchors on the target, fan-out on the source -> each endpoint is evaluated in its aggregated role
    fan_in = next(p for q, p in s.calls if "in_degree" in q)
    fan_out = next(p for q, p in s.calls if "out_degree" in q)
    assert "DST" in fan_in["bank_accounts"] and "SRC" in fan_out["bank_accounts"]


def test_run_order_and_detector_run_records():
    s = RecordingSession()
    insights, runs, skipped = run_scoped_detectors(s, TouchedEntities(phones={"+1"}, bank_accounts={"B"}))
    assert [r.insight_type for r in runs] == ["SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "TRANSFER_CHAIN",
                                              "HIGH_FAN_IN", "HIGH_FAN_OUT", "INFRASTRUCTURE_REUSE", "CROSS_DOMAIN_PATH"]
    assert skipped == ["POSSIBLE_CO_LOCATION", "PRIOR_CASE_LINK"]
    assert all(r.status == "ok" and r.insights == 0 and r.error is None for r in runs)
    assert all(r.detector.endswith("_scoped") for r in runs)


def test_global_scope_by_default_and_case_filter_passthrough():
    s = RecordingSession()
    run_scoped_detectors(s, TouchedEntities(persons={"P"}))
    assert all(p["case_id"] is None for _q, p in s.calls if "case_id" in p)
    s2 = RecordingSession()
    run_scoped_detectors(s2, TouchedEntities(persons={"P"}), case_id="CASE-K")
    assert all(p["case_id"] == "CASE-K" for _q, p in s2.calls if "case_id" in p)


def test_detector_failure_is_reported_and_others_still_run():
    s = FailingSession(fail_when=lambda q: "TRANSFERRED_TO]->(b2:BankAccount)-[r2" in q)  # only TRANSFER_CHAIN fails
    insights, runs, _ = run_scoped_detectors(s, TouchedEntities(bank_accounts={"B"}))
    by_type = {r.insight_type: r for r in runs}
    assert by_type["TRANSFER_CHAIN"].status == "failed"
    assert by_type["TRANSFER_CHAIN"].error == "RuntimeError: simulated Neo4j failure"
    assert by_type["HIGH_FAN_IN"].status == "ok" and by_type["HIGH_FAN_OUT"].status == "ok" and by_type["CROSS_DOMAIN_PATH"].status == "ok"
    assert insights == []


def test_dedup_last_write_wins_like_full_engine():
    # two detectors returning the same insight_id -> one insight kept
    rows = _ROWS["HIGH_FAN_IN"]
    s = RecordingSession([[], [], rows, [], [], []])  # shared, bridge, chain(rows->ignored), fan-in ...
    # simpler: run fan-in twice through the dispatcher by touching accounts; duplicates within one detector collapse
    s = RecordingSession([[], [], [], rows + rows, [], []])
    insights, runs, _ = run_scoped_detectors(s, TouchedEntities(bank_accounts={"X"}))
    assert len(insights) == 1 and insights[0].insight_type.value == "HIGH_FAN_IN"


def test_limit_overrides_only_apply_to_limited_detectors():
    s = RecordingSession()
    run_scoped_detectors(s, TouchedEntities(bank_accounts={"B"}, persons={"P"}, locations={"L"}),
                         limit_overrides={"TRANSFER_CHAIN": None, "POSSIBLE_CO_LOCATION": None, "CROSS_DOMAIN_PATH": None, "HIGH_FAN_IN": None})
    for q, p in s.calls:
        if "r2:TRANSFERRED_TO" in q or "LOCATED_AT]->(loc:Location)<-" in q or "ph1:Phone)-[c:CALLED]" in q:
            assert "LIMIT" not in q and p["limit"] is None
        if "in_degree" in q:
            assert "limit" not in p  # HIGH_FAN_IN has no LIMIT; override ignored


def test_make_detector_runner_matches_processor_contract():
    runner = make_detector_runner(case_id=None, limit_overrides={"TRANSFER_CHAIN": None})
    insights, runs, skipped = runner(RecordingSession(), TouchedEntities(prior_cases={"PC"}))
    assert insights == [] and [r.insight_type for r in runs] == ["PRIOR_CASE_LINK"] and len(skipped) == 9
    assert all(isinstance(r, DetectorRun) for r in runs)


def test_shim_refuses_query_count_mismatch():
    """A scoped detector must issue exactly its planned queries -- never fall through to the full one."""
    from backend.services.scoped_detectors import _run_full_formatting
    with pytest.raises(RuntimeError):
        _run_full_formatting(InsightsEngine.detect_infrastructure_reuse, RecordingSession(), [("RETURN 1", {})], None)  # needs 2
    with pytest.raises(RuntimeError):
        _run_full_formatting(InsightsEngine.detect_shared_entities, RecordingSession(), [("RETURN 1", {}), ("RETURN 2", {})], None)  # needs 1
