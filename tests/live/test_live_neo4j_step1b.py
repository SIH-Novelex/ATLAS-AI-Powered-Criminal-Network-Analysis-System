"""
Live-Neo4j verification for the Step 1B batched relationship writers.

Skipped automatically unless Neo4j is reachable (see tests/test_live_neo4j_step0.py for the
NEO4J_TEST_* environment variables). WARNING: wipes the target database.
"""
import json
import os

import pytest

pytestmark = pytest.mark.live_neo4j

from tests.live.test_live_neo4j_step0 import _driver_or_skip, _load_case  # noqa: E402

EDGE_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "case_edge_batching.json")


def _load_edge():
    from backend.models.case_input import CaseData
    with open(EDGE_FIXTURE, "r", encoding="utf-8") as f:
        return CaseData(**json.load(f))


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


def _count(session, cypher, **params):
    return session.run(cypher, params).single()["c"]


def test_sample_cases_graph_shape_after_batching(live_session):
    """Reference counts recorded from the pre-batching (Step 0) code on the same two payloads."""
    from backend.services.ingestion_service import IngestionService

    r1 = IngestionService.ingest_case(live_session, _load_case("case_001_homicide.json"))
    r2 = IngestionService.ingest_case(live_session, _load_case("case_002_fraud.json"))
    assert (r1.created.nodes, r1.created.relationships, r1.matched_existing_entities) == (21, 40, 0)
    assert (r2.created.nodes, r2.created.relationships, r2.matched_existing_entities) == (6, 18, 4)

    assert _count(live_session, "MATCH ()-[r:CALLED]->() RETURN count(r) AS c") == 3
    assert _count(live_session, "MATCH ()-[r:TRANSFERRED_TO]->() RETURN count(r) AS c") == 3
    assert _count(live_session, "MATCH (t:Transaction) RETURN count(t) AS c") == 3
    assert _count(live_session, "MATCH (:Case)-[:INVOLVES]->(:Transaction) RETURN count(*) AS c") == 3
    assert _count(live_session, "MATCH ()-[r:LOCATED_AT]->() RETURN count(r) AS c") == 4   # 2 persons + 1 vehicle + 1 tower
    assert _count(live_session, "MATCH ()-[r:HAS_PRIOR_CASE]->() RETURN count(r) AS c") == 1
    assert _count(live_session, "MATCH (sr:SourceRecord {source_type:'INTELLIGENCE_REPORT'}) RETURN count(sr) AS c") == 1

    call = live_session.run("MATCH ()-[r:CALLED {call_id:'CALL-2024-001'}]->() RETURN properties(r) AS p").single()["p"]
    assert call["duration_seconds"] == 180 and call["cell_tower"] == "TOWER-BLR-041"
    assert call["case_id"] == "CASE-2024-001" and call["source_record_id"] == "CDR-2024-10" and "created_at" in call

    # The phone that only appears as a call target in case 001 keeps case_ids=[001] (unchanged semantics)
    assert live_session.run("MATCH (p:Phone {phone_number:'+919876543211'}) RETURN p.case_ids AS c").single()["c"] == ["CASE-2024-001"]


def test_edge_fixture_merge_semantics(live_session):
    """Duplicate keys inside one batch, ON MATCH behaviour, missing targets and fallback identifiers."""
    from backend.services.ingestion_service import IngestionService

    edge = _load_edge()
    first = IngestionService.ingest_case(live_session, edge)
    # 20 MERGE-able records, but CRIM-E-2's Person does not exist -> that row returns nothing and is
    # counted as "matched" (identical to the pre-batching `res_ch is None` branch): 19 created / 1 matched.
    assert (first.created.nodes, first.created.relationships, first.created.source_records) == (19, 44, 4)
    assert first.matched_existing_entities == 1

    # CALLED: duplicate call_id in one batch -> ON MATCH applied to the 2nd row:
    # duration overwritten (90), cell_tower kept (coalesce(null, 'T-E1')), ON CREATE-only props untouched.
    c = live_session.run("MATCH ()-[r:CALLED {call_id:'CALL-E-1'}]->() RETURN properties(r) AS p").single()["p"]
    assert c["duration_seconds"] == 90 and c["cell_tower"] == "T-E1" and c["source_record_id"] == "SR-E-1"
    assert _count(live_session, "MATCH ()-[r:CALLED {call_id:'CALL-E-1'}]->() RETURN count(r) AS c") == 1
    assert _count(live_session, "MATCH ()-[r:CALLED]->() RETURN count(r) AS c") == 3
    # implicit Phone creation for a number never declared as an entity
    assert live_session.run("MATCH (p:Phone {phone_number:'+91E3'}) RETURN p.case_ids AS c").single()["c"] == ["CASE-EDGE-001"]

    # TRANSFERRED_TO / Transaction: duplicate tx_id in one batch -> first row wins (no ON MATCH), single edge + node.
    t = live_session.run("MATCH ()-[r:TRANSFERRED_TO {transaction_id:'TX-E-1'}]->() RETURN properties(r) AS p").single()["p"]
    assert t["amount"] == 100.0 and t["reference_no"] == "REF-1"
    assert _count(live_session, "MATCH (t:Transaction {transaction_id:'TX-E-1'}) RETURN count(t) AS c") == 1
    assert _count(live_session, "MATCH ()-[r:TRANSFERRED_TO]->() RETURN count(r) AS c") == 2
    assert _count(live_session, "MATCH (:Case {case_id:'CASE-EDGE-001'})-[:INVOLVES]->(:Transaction) RETURN count(*) AS c") == 2
    assert live_session.run("MATCH (b:BankAccount {account_number:'ACC-E3'}) RETURN b.case_ids AS c").single()["c"] == ["CASE-EDGE-001"]

    # Surveillance: fallback Location id, missing Person/Vehicle silently produce no edge (MATCH fails), others written.
    loc = live_session.run("MATCH (l:Location {location_id:'LOC_SURV-E-2'}) RETURN properties(l) AS p").single()["p"]
    assert loc["name"] == "LOC_SURV-E-2" and loc["case_ids"] == ["CASE-EDGE-001"] and "address" not in loc
    assert _count(live_session, "MATCH (:Person)-[r:LOCATED_AT]->() RETURN count(r) AS c") == 4   # P-E1,P-E2 @SURV-1; P-E1 @SURV-2; P-E2 @SURV-3
    assert _count(live_session, "MATCH (:Vehicle)-[r:LOCATED_AT]->() RETURN count(r) AS c") == 1  # VIN-E1 only
    assert _count(live_session, "MATCH ()-[r:LOCATED_AT {log_id:'SURV-E-2'}]->() RETURN count(r) AS c") == 1
    p_edge = live_session.run("MATCH (:Person {person_id:'P-E1'})-[r:LOCATED_AT {log_id:'SURV-E-1'}]->() RETURN properties(r) AS p").single()["p"]
    assert p_edge == {"log_id": "SURV-E-1", "timestamp": "2026-01-03T09:00:00Z", "activity_description": "Meeting",
                      "evidence_ref": "CAM-1.mp4", "case_id": "CASE-EDGE-001", "source_record_id": "SR-E-1"}
    v_edge = live_session.run("MATCH (:Vehicle)-[r:LOCATED_AT {log_id:'SURV-E-1'}]->() RETURN properties(r) AS p").single()["p"]
    assert v_edge == {"log_id": "SURV-E-1", "timestamp": "2026-01-03T09:00:00Z", "activity_description": "Meeting", "case_id": "CASE-EDGE-001"}

    # PriorCase: duplicate prior_id -> node created once with first row's props, HAS_PRIOR_CASE from both persons;
    # orphan record (missing person) creates the PriorCase node but no HAS_PRIOR_CASE / INVOLVES (as before).
    pc = live_session.run("MATCH (pc:PriorCase {prior_case_id:'CRIM-E-1'}) RETURN properties(pc) AS p").single()["p"]
    assert pc["status"] == "CONVICTED" and pc["case_number"] == "CC-E-1"
    assert sorted(r["pid"] for r in live_session.run("MATCH (p:Person)-[:HAS_PRIOR_CASE]->(:PriorCase {prior_case_id:'CRIM-E-1'}) RETURN p.person_id AS pid")) == ["P-E1", "P-E2"]
    assert _count(live_session, "MATCH (pc:PriorCase {prior_case_id:'CRIM-E-2'}) RETURN count(pc) AS c") == 1
    assert _count(live_session, "MATCH ()-[:HAS_PRIOR_CASE]->(:PriorCase {prior_case_id:'CRIM-E-2'}) RETURN count(*) AS c") == 0
    assert _count(live_session, "MATCH (:Case)-[:INVOLVES]->(:PriorCase {prior_case_id:'CRIM-E-2'}) RETURN count(*) AS c") == 0

    # Intel: duplicate report_id -> first row wins, never overwritten.
    ir = live_session.run("MATCH (sr:SourceRecord {source_record_id:'INTEL-E-1'}) RETURN properties(sr) AS p").single()["p"]
    assert ir["source_agency"] == "Unit A" and ir["reliability_score"] == 0.9 and ir["source_type"] == "INTELLIGENCE_REPORT"
    assert _count(live_session, "MATCH (sr:SourceRecord {source_type:'INTELLIGENCE_REPORT'}) RETURN count(sr) AS c") == 2

    # Re-ingest: fully idempotent, everything matched, no new nodes/edges, counts as before.
    before_nodes = _count(live_session, "MATCH (n) RETURN count(n) AS c")
    before_rels = _count(live_session, "MATCH ()-[r]->() RETURN count(r) AS c")
    second = IngestionService.ingest_case(live_session, edge)
    assert second.created.nodes == 0 and second.matched_existing_entities == 20
    assert second.created.relationships == 44 and second.created.source_records == 4
    assert _count(live_session, "MATCH (n) RETURN count(n) AS c") == before_nodes
    assert _count(live_session, "MATCH ()-[r]->() RETURN count(r) AS c") == before_rels
