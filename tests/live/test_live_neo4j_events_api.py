"""
Live-Neo4j verification of POST /api/events/batch (Phase 2 / stage 4, brief section 9).

Through the real FastAPI app (TestClient) against a real Neo4j:
* the endpoint creates the expected graph state (counts + spot checks);
* first replay creates the required relationships immediately (FIR->accused, OWNS, CALLED ...);
* replay is idempotent (no graph change, nodes_matched == N);
* case_id additions trigger the scoped detectors and cross-case calls are detected;
* detector failures are visible in the response body;
* no LINKED_TO_IP relationship is ever created; missing case -> 404; rollback on failure.

Skipped automatically unless Neo4j is reachable (NEO4J_TEST_* env vars). WARNING: wipes the DB.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.live_neo4j

from tests.live.test_live_neo4j_step0 import _driver_or_skip  # noqa: E402
from tests.unit.test_delta_processor import events_from_case_doc  # noqa: E402

URL = "/api/events/batch"


def _doc(name):
    with open(os.path.join("dataset", name), "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def live():
    """(client, driver): the app's db.get_session() is bound to the live driver; DB wiped before/after."""
    driver = _driver_or_skip()
    from fastapi.testclient import TestClient
    from backend.database import db
    from backend.main import app
    from backend.services.schema_manager import init_schema
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        init_schema(s)
    with patch.object(db, "get_session", side_effect=lambda *a, **k: driver.session()):
        with TestClient(app) as client:
            yield client, driver
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    driver.close()


def _count(driver, cypher):
    with driver.session() as s:
        return s.run(cypher).single()["c"]


def _create_case(client, meta):
    r = client.post("/api/cases/ingest", json={"case_metadata": meta})
    assert r.status_code in (200, 201)


def test_endpoint_creates_expected_graph_state_and_is_idempotent(live):
    client, driver = live
    doc = _doc("case_001_homicide.json")
    evs = events_from_case_doc(doc)

    # missing case -> 404, nothing written
    r = client.post(URL, json={"case_id": "CASE-2024-001", "events": evs})
    assert r.status_code == 404 and _count(driver, "MATCH (n) RETURN count(n) AS c") == 0

    _create_case(client, doc["case_metadata"])
    r = client.post(URL, json={"case_id": "CASE-2024-001", "events": evs, "batch_id": "live-1"})
    assert r.status_code == 201
    j = r.json()
    assert j["writes"] == {"nodes_created": 20, "nodes_matched": 0, "relationships_written": 40, "statements_executed": 23}
    assert j["detectors_failed"] == [] and len(j["detectors_run"]) == 10 and j["detectors_skipped"] == []
    assert {i["insight_type"] for i in j["insights"]} == {"TRANSFER_CHAIN", "INFRASTRUCTURE_REUSE", "POSSIBLE_CO_LOCATION", "PRIOR_CASE_LINK"}

    # graph state == bulk ingestion of the same case (21 nodes incl. Case, 40 relationships)
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 21
    assert _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c") == 40
    # first replay creates the required relationships immediately
    assert _count(driver, "MATCH (:FIR)-[:INVOLVES]->(:Person) RETURN count(*) AS c") == 2
    assert _count(driver, "MATCH (:Person)-[:OWNS]->() RETURN count(*) AS c") == 7
    assert _count(driver, "MATCH ()-[:CALLED]->() RETURN count(*) AS c") == 2
    assert _count(driver, "MATCH ()-[:TRANSFERRED_TO]->() RETURN count(*) AS c") == 2
    assert _count(driver, "MATCH ()-[:LOCATED_AT]->() RETURN count(*) AS c") == 4
    assert _count(driver, "MATCH ()-[:HAS_PRIOR_CASE]->() RETURN count(*) AS c") == 1
    assert _count(driver, "MATCH ()-[:LINKED_TO_IP]->() RETURN count(*) AS c") == 0

    # idempotent replay
    r2 = client.post(URL, json={"case_id": "CASE-2024-001", "events": evs, "batch_id": "live-2"})
    assert r2.status_code == 200
    assert r2.json()["writes"] == {"nodes_created": 0, "nodes_matched": 20, "relationships_written": 40, "statements_executed": 23}
    assert r2.json()["touched"]["case_ids_changed"] == []
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 21 and _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c") == 40
    with driver.session() as s:
        assert s.run("MATCH (p:Person {person_id:'P-001'}) RETURN p.aliases AS a").single()["a"] == ["Deva", "The Broker"]


def test_case_id_additions_trigger_scoped_detectors_and_cross_case_calls(live):
    client, driver = live
    d1, d2 = _doc("case_001_homicide.json"), _doc("case_002_fraud.json")
    _create_case(client, d1["case_metadata"])
    r1 = client.post(URL, json={"case_id": "CASE-2024-001", "events": events_from_case_doc(d1)}).json()
    assert not any(i["insight_type"] in ("SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE") for i in r1["insights"])

    _create_case(client, d2["case_metadata"])
    r2 = client.post(URL, json={"case_id": "CASE-2024-002", "events": events_from_case_doc(d2)}).json()
    assert set(r2["touched"]["case_ids_changed"]) == {"Person:P-001", "Phone:+919876543210", "BankAccount:ACC-9001-DEV", "IPAddress:198.51.100.45"}
    types = {i["insight_type"] for i in r2["insights"]}
    assert {"SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE"} <= types
    links = sorted(tuple(i["entities_involved"]) for i in r2["insights"] if i["insight_type"] == "CROSS_CASE_LINK")
    # +919876543210 [001, 002] is the source of the OLD case_001 call to +919876543211 [001] (flips to cross-case
    # because the source now has 002) and of the NEW case_002 call to +919876543999 [002] (source has 001)
    assert links == [("+919876543210", "+919876543211"), ("+919876543210", "+919876543999")]
    shared = {i["entities_involved"][0] for i in r2["insights"] if i["insight_type"] == "SHARED_ENTITY"}
    assert shared == {"P-001", "+919876543210", "ACC-9001-DEV", "198.51.100.45"}
    assert _count(driver, "MATCH ()-[:LINKED_TO_IP]->() RETURN count(*) AS c") == 0
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 27 and _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c") == 56


def test_detector_failure_is_visible_live(live):
    """Force one scoped detector to fail on the real DB by breaking its query text; writes still succeed."""
    client, driver = live
    from backend.services import scoped_detectors as sd
    _create_case(client, {"case_id": "CASE-F", "case_name": "fail"})
    original = sd.SCOPED_DETECTORS["PRIOR_CASE_LINK"]

    def broken(session, touched, case_id=None, **kw):
        session.run("THIS IS NOT CYPHER").data()

    broken.__name__ = "detect_prior_case_links_scoped"
    sd.SCOPED_DETECTORS["PRIOR_CASE_LINK"] = (broken, original[1])
    try:
        r = client.post(URL, json={"case_id": "CASE-F", "events": [
            {"event_type": "CRIMINAL_HISTORY", "payload": {"record_id": "R-F", "person_id": "P-F", "case_number": "CC-F"}},
            {"event_type": "PERSON_UPSERT", "payload": {"person_id": "P-F", "name": "Fail Person"}}]})
    finally:
        sd.SCOPED_DETECTORS["PRIOR_CASE_LINK"] = original
    assert r.status_code == 201
    j = r.json()
    assert j["detectors_failed"] == ["detect_prior_case_links_scoped"]
    failed = next(x for x in j["detectors_run"] if x["insight_type"] == "PRIOR_CASE_LINK")
    assert failed["status"] == "failed" and "CypherSyntaxError" in failed["error"]
    assert all(x["status"] == "ok" for x in j["detectors_run"] if x["insight_type"] != "PRIOR_CASE_LINK")
    # writes were committed despite the detector failure
    assert _count(driver, "MATCH (:Person {person_id:'P-F'})-[:HAS_PRIOR_CASE]->(:PriorCase {prior_case_id:'R-F'}) RETURN count(*) AS c") == 1


def test_mid_batch_failure_rolls_back_live(live):
    client, driver = live
    from backend.services import graph_writes as gw
    _create_case(client, {"case_id": "CASE-RB", "case_name": "rollback"})
    original = gw.ALL_WRITE_STATEMENTS["CALLED_MERGE"]
    gw.ALL_WRITE_STATEMENTS["CALLED_MERGE"] = "THIS IS NOT CYPHER"
    try:
        r = client.post(URL, json={"case_id": "CASE-RB", "events": [
            {"event_type": "PERSON_UPSERT", "payload": {"person_id": "P-RB", "name": "RB"}},
            {"event_type": "COMMUNICATION", "payload": {"source_phone": "+RB1", "target_phone": "+RB2", "timestamp": "T"}}]})
    finally:
        gw.ALL_WRITE_STATEMENTS["CALLED_MERGE"] = original
    assert r.status_code == 500 and "rolled back" in r.json()["message"]
    assert _count(driver, "MATCH (p:Person) RETURN count(p) AS c") == 0
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 1  # only the Case node
