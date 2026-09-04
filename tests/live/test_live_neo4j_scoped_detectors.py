"""
Live-Neo4j checks for the scoped detectors (Phase 2 / stage 3).

What is verified here, against a real Neo4j 5.x:
* every scoped Cypher statement compiles (EXPLAIN) with and without case_id / LIMIT;
* the POSSIBLE_CO_LOCATION case predicate (`$case_id IN l1.case_id` on a scalar STRING edge
  property) evaluates as equality at runtime and never raises -- so keeping it verbatim is safe;
* anchor strategies: a touched entity is discovered at EVERY pattern position (transfer chain
  b1/b2/b3, cross-domain p1/p2/ph1/ph2/b1/b2, co-location e1/e2/loc, cross-case either endpoint,
  bridge via any owned asset, IMEI reuse via one phone, prior-case via person or record);
* global re-aggregation (fan-in degree counted over all edges, not only touched ones);
* case_ids join trigger: an OLD call becomes a cross-case link when one endpoint gains a case;
* touched-everything scoped run reproduces the full run on the sample data (a sanity check only --
  the formal equivalence harness is the next stage and is NOT claimed here).

Skipped automatically unless Neo4j is reachable (NEO4J_TEST_* env vars). WARNING: wipes the DB.
"""
import json
import os
import sys

import pytest

pytestmark = pytest.mark.live_neo4j

from tests.live.test_live_neo4j_step0 import _driver_or_skip, _load_case  # noqa: E402


def _canon(insight):
    """Co-location ids depend on elementId() string ordering (pre-existing, run-to-run nondeterministic);
    compare those by unordered entity pair instead of raw id."""
    if insight.insight_type.value == "POSSIBLE_CO_LOCATION":
        return ("POSSIBLE_CO_LOCATION", insight.metadata.get("location_id"), tuple(sorted(insight.entities_involved)))
    return insight.insight_id


def _ids(insights):
    return {_canon(i) for i in insights}


@pytest.fixture
def live_session():
    driver = _driver_or_skip()
    from backend.services.schema_manager import init_schema
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
        init_schema(session)
        yield session
        session.run("MATCH (n) DETACH DELETE n")
    driver.close()


def _ingest(session, doc):
    from backend.models.case_input import CaseData
    from backend.services.ingestion_service import IngestionService
    return IngestionService.ingest_case(session, CaseData(**doc))


def _T(**kw):
    from backend.models.event import TouchedEntities
    return TouchedEntities(**{k: set(v) for k, v in kw.items()})


def test_all_scoped_cypher_compiles(live_session):
    from backend.services.scoped_detectors import FULL_LIMITS, SCOPED_DETECTORS

    class Explain:
        def __init__(self, live): self.live = live; self.n = 0
        def run(self, q, p=None):
            self.live.run("EXPLAIN " + q, p or {}).consume(); self.n += 1
            class R:
                def data(self_inner): return []
            return R()

    touched = _T(persons=["P"], phones=["+1"], bank_accounts=["A"], vehicles=["V"], social_handles=["S"],
                 ip_addresses=["1.1.1.1"], locations=["L"], cell_towers=["T"], prior_cases=["PC"])
    for name, (scoped, _full) in SCOPED_DETECTORS.items():
        for case_id in (None, "CASE-X"):
            ex = Explain(live_session)
            assert scoped(ex, touched, case_id) == []
            assert ex.n == (2 if name == "INFRASTRUCTURE_REUSE" else 1)
        if name in FULL_LIMITS:
            assert scoped(Explain(live_session), touched, None, limit=None) == []


def test_co_location_case_predicate_on_scalar_edge_property_is_safe(live_session):
    """Verified schema fact + runtime behaviour behind keeping the full predicate verbatim."""
    from backend.services.scoped_detectors import ScopedInsightsEngine
    _ingest(live_session, json.loads(json.dumps(_load_case("case_001_homicide.json").model_dump())))
    types = {r["t"] for r in live_session.run("MATCH ()-[l:LOCATED_AT]->() WHERE l.case_id IS NOT NULL RETURN DISTINCT valueType(l.case_id) AS t")}
    assert types == {"STRING NOT NULL"}  # scalar, not a list
    # `$case_id IN <scalar string>` evaluates as equality at runtime (no error)
    row = live_session.run("MATCH ()-[l:LOCATED_AT]->() WHERE l.case_id IS NOT NULL WITH l LIMIT 1 "
                           "RETURN 'CASE-2024-001' IN l.case_id AS eq, 'X' IN l.case_id AS neq").single()
    assert row["eq"] is True and row["neq"] is False
    # scoped detector with the case filter: matching case returns pairs, other case returns none, nothing raises
    assert len(ScopedInsightsEngine.detect_possible_co_location_scoped(live_session, _T(locations=["LOC-HOTEL-01"]), "CASE-2024-001")) == 6
    assert ScopedInsightsEngine.detect_possible_co_location_scoped(live_session, _T(locations=["LOC-HOTEL-01"]), "CASE-OTHER") == []
    # edge-carried case (Location MERGE is ON CREATE only, so loc.case_ids never gains the 2nd case): edges still match
    _ingest(live_session, {"case_metadata": {"case_id": "CASE-B", "case_name": "B"},
                           "entities": {"people": [{"person_id": "P-B1", "name": "B1"}, {"person_id": "P-B2", "name": "B2"}]},
                           "surveillance_logs": [{"log_id": "SURV-B-1", "location_id": "LOC-HOTEL-01", "timestamp": "2026-02-01T00:00:00Z",
                                                  "observed_person_ids": ["P-B1", "P-B2"]}]})
    assert live_session.run("MATCH (l:Location {location_id:'LOC-HOTEL-01'}) RETURN l.case_ids AS c").single()["c"] == ["CASE-2024-001"]
    scoped_b = ScopedInsightsEngine.detect_possible_co_location_scoped(live_session, _T(persons=["P-B1", "P-B2"]), "CASE-B", limit=None)
    assert scoped_b and all("P-B1" in i.entities_involved or "P-B2" in i.entities_involved for i in scoped_b)


def test_touched_everything_reproduces_full_run_on_samples(live_session):
    """Sanity only (not the equivalence harness): with every identity touched, scoped == full."""
    from backend.services.insights_engine import InsightsEngine
    from backend.services.scoped_detectors import run_scoped_detectors

    for f in ("case_001_homicide.json", "case_002_fraud.json"):
        _ingest(live_session, json.loads(json.dumps(_load_case(f).model_dump())))
    full = InsightsEngine.run_all_detectors(live_session, None)
    q = lambda c: {r["k"] for r in live_session.run(c)}
    everything = _T(persons=q("MATCH (n:Person) RETURN n.person_id AS k"), phones=q("MATCH (n:Phone) RETURN n.phone_number AS k"),
                    bank_accounts=q("MATCH (n:BankAccount) RETURN n.account_number AS k"), vehicles=q("MATCH (n:Vehicle) RETURN n.vin AS k"),
                    social_handles=q("MATCH (n:SocialHandle) RETURN n.handle_id AS k"), ip_addresses=q("MATCH (n:IPAddress) RETURN n.ip_address AS k"),
                    locations=q("MATCH (n:Location) RETURN n.location_id AS k"), cell_towers=q("MATCH (n:CellTower) RETURN n.cell_tower_id AS k"),
                    prior_cases=q("MATCH (n:PriorCase) RETURN n.prior_case_id AS k"))
    scoped, runs, skipped = run_scoped_detectors(live_session, everything)
    assert skipped == [] and all(r.status == "ok" for r in runs)
    assert len(full) == 16 and _ids(scoped) == _ids(full)
    F = {_canon(i): i for i in full}
    for i in scoped:
        assert i.model_dump(exclude={"created_at"}) == F[_canon(i)].model_dump(exclude={"created_at"})


def test_anchor_positions_on_sample_graph(live_session):
    from backend.services.scoped_detectors import SCOPED_DETECTORS

    for f in ("case_001_homicide.json", "case_002_fraud.json"):
        _ingest(live_session, json.loads(json.dumps(_load_case(f).model_dump())))

    def scoped(name, **kw):
        return _ids(SCOPED_DETECTORS[name][0](live_session, _T(**kw), None))

    def full(name):
        return _ids(SCOPED_DETECTORS[name][1](live_session, None))

    # TRANSFER_CHAIN ACC-9001-DEV -> ACC-9002-MULE -> ACC-9003-SHOOTER: found from any position, not from an unrelated one
    chain = full("TRANSFER_CHAIN"); assert len(chain) == 1
    assert scoped("TRANSFER_CHAIN", bank_accounts=["ACC-9001-DEV"]) == chain
    assert scoped("TRANSFER_CHAIN", bank_accounts=["ACC-9002-MULE"]) == chain      # middle position
    assert scoped("TRANSFER_CHAIN", bank_accounts=["ACC-9003-SHOOTER"]) == chain   # last position
    assert scoped("TRANSFER_CHAIN", bank_accounts=["NOPE"]) == set()

    # CROSS_CASE_LINK: both links share source +919876543210; anchoring on a TARGET phone finds its link
    links = full("CROSS_CASE_LINK"); assert len(links) == 2
    assert scoped("CROSS_CASE_LINK", phones=["+919876543210"]) == links
    assert len(scoped("CROSS_CASE_LINK", phones=["+919876543211"])) == 1
    assert scoped("CROSS_CASE_LINK", phones=["+919876543211"]) <= links

    # SHARED_ENTITY: P-001 is in both cases, P-002 is not
    assert len(scoped("SHARED_ENTITY", persons=["P-001"])) == 1 and scoped("SHARED_ENTITY", persons=["P-002"]) == set()
    assert scoped("SHARED_ENTITY", persons=["P-001", "P-002"], phones=["+919876543210"], bank_accounts=["ACC-9001-DEV"], ip_addresses=["198.51.100.45"]) == full("SHARED_ENTITY")

    # BRIDGE_NODE: P-001 reachable via person id, via an owned phone, via an owned account
    bridge = full("BRIDGE_NODE"); assert len(bridge) == 1
    assert scoped("BRIDGE_NODE", persons=["P-001"]) == bridge
    assert scoped("BRIDGE_NODE", phones=["+919876543210"]) == bridge
    assert scoped("BRIDGE_NODE", bank_accounts=["ACC-9001-DEV"]) == bridge
    assert scoped("BRIDGE_NODE", persons=["P-003"]) == set()

    # INFRASTRUCTURE_REUSE: IMEI shared by +...210 and +...211 -> found from ONE of them; not from the other IMEI's phone
    infra = full("INFRASTRUCTURE_REUSE"); assert len(infra) == 1
    assert scoped("INFRASTRUCTURE_REUSE", phones=["+919876543211"]) == infra
    assert scoped("INFRASTRUCTURE_REUSE", phones=["+919876543212"]) == set()

    # PRIOR_CASE_LINK via person or via record id
    prior = full("PRIOR_CASE_LINK"); assert len(prior) == 1
    assert scoped("PRIOR_CASE_LINK", persons=["P-001"]) == prior == scoped("PRIOR_CASE_LINK", prior_cases=["CRIM-DEV-001"])

    # POSSIBLE_CO_LOCATION: from the location (all 6 pairs), from a vehicle / a tower (their 3 pairs each), subset of full
    coloc = full("POSSIBLE_CO_LOCATION"); assert len(coloc) == 6
    assert scoped("POSSIBLE_CO_LOCATION", locations=["LOC-HOTEL-01"]) == coloc
    by_vehicle = scoped("POSSIBLE_CO_LOCATION", vehicles=["VIN-MAH-XUV-90118"])
    assert len(by_vehicle) == 3 and by_vehicle <= coloc and all("VIN-MAH-XUV-90118" in k[2] for k in by_vehicle)
    by_tower = scoped("POSSIBLE_CO_LOCATION", cell_towers=["TOWER-BLR-041"])
    assert len(by_tower) == 3 and by_tower <= coloc


def test_cross_domain_path_found_from_every_position(live_session):
    from backend.services.scoped_detectors import SCOPED_DETECTORS
    _ingest(live_session, {"case_metadata": {"case_id": "CASE-XD", "case_name": "xd"},
                           "entities": {"people": [{"person_id": "P1", "name": "One"}, {"person_id": "P2", "name": "Two"}],
                                        "phones": [{"phone_number": "+PH1", "owner_person_id": "P1"}, {"phone_number": "+PH2", "owner_person_id": "P2"}],
                                        "bank_accounts": [{"account_number": "B1", "owner_person_id": "P2"}, {"account_number": "B2"}]},
                           "relationships": {"communications": [{"call_id": "C1", "source_phone": "+PH1", "target_phone": "+PH2", "timestamp": "T"}],
                                             "transactions": [{"transaction_id": "T1", "source_account": "B1", "target_account": "B2", "amount": 5.0, "timestamp": "T"}]}})
    scoped, full = SCOPED_DETECTORS["CROSS_DOMAIN_PATH"]
    F = _ids(full(live_session, None)); assert len(F) == 1
    for kw in ({"persons": ["P1"]}, {"persons": ["P2"]}, {"phones": ["+PH1"]}, {"phones": ["+PH2"]}, {"bank_accounts": ["B1"]}, {"bank_accounts": ["B2"]}):
        assert _ids(scoped(live_session, _T(**kw), None)) == F, kw
    assert _ids(scoped(live_session, _T(persons=["P9"]), None)) == set()
    assert _ids(scoped(live_session, _T(persons=["P1"]), "CASE-XD")) == F and scoped(live_session, _T(persons=["P1"]), "OTHER") == []
    assert _ids(scoped(live_session, _T(bank_accounts=["B2"]), None, limit=None)) == F


def test_fan_in_reaggregates_over_all_edges_when_anchored_on_target(live_session):
    from backend.services.scoped_detectors import SCOPED_DETECTORS
    _ingest(live_session, json.loads(json.dumps(_load_case("case_001_homicide.json").model_dump())))
    # ACC-9002-MULE has 1 inbound so far; two more from another case reach the threshold (3)
    _ingest(live_session, {"case_metadata": {"case_id": "CASE-FAN", "case_name": "fan"},
                           "relationships": {"transactions": [
                               {"transaction_id": "TXF-1", "source_account": "ACC-F1", "target_account": "ACC-9002-MULE", "amount": 1.0, "timestamp": "T"},
                               {"transaction_id": "TXF-2", "source_account": "ACC-F2", "target_account": "ACC-9002-MULE", "amount": 2.0, "timestamp": "T"}]}})
    scoped, full = SCOPED_DETECTORS["HIGH_FAN_IN"]
    F = _ids(full(live_session, None)); assert len(F) == 1
    # both endpoints touched (what the processor does) -> found; degree counted over ALL three inbound edges
    got = scoped(live_session, _T(bank_accounts=["ACC-F1", "ACC-F2", "ACC-9002-MULE"]), None)
    assert _ids(got) == F and got[0].metadata["in_degree"] == 3
    # anchoring only on the new sources would miss it -- which is why relationship events touch both endpoints
    assert scoped(live_session, _T(bank_accounts=["ACC-F1", "ACC-F2"]), None) == []


def test_case_join_turns_old_call_into_cross_case_link(live_session):
    from backend.services.scoped_detectors import SCOPED_DETECTORS, select_detectors
    _ingest(live_session, {"case_metadata": {"case_id": "CASE-1", "case_name": "one"},
                           "entities": {"phones": [{"phone_number": "+PH1"}, {"phone_number": "+PH2"}]},
                           "relationships": {"communications": [{"call_id": "C1", "source_phone": "+PH1", "target_phone": "+PH2", "timestamp": "T"}]}})
    scoped, full = SCOPED_DETECTORS["CROSS_CASE_LINK"]
    assert full(live_session, None) == []
    # +PH1 gains CASE-2 through a plain phone upsert: NO new relationship
    _ingest(live_session, {"case_metadata": {"case_id": "CASE-2", "case_name": "two"}, "entities": {"phones": [{"phone_number": "+PH1"}]}})
    F = _ids(full(live_session, None)); assert len(F) == 1
    # dispatcher: the join alone selects CROSS_CASE_LINK ...
    t = _T(phones=["+PH1"]); t.case_ids_changed.add("Phone:+PH1")
    assert "CROSS_CASE_LINK" in select_detectors(t)[0]
    # ... and the scoped detector, anchored on the node that joined, finds the OLD call
    assert _ids(scoped(live_session, t, None)) == F
    assert _ids(scoped(live_session, _T(phones=["+PH2"]), None)) == F  # other endpoint works too
    # asymmetry preserved: only PH1 -> PH2 (p1 owns a case p2 lacks)
    assert [i.entities_involved for i in scoped(live_session, t, None)] == [["+PH1", "+PH2"]]
