"""
Live-Neo4j verification that ``POST /api/v1/ingest`` (legacy multipart endpoint) ingests
``dataset/case_001_homicide.json`` through the CaseData / IngestionService pipeline.

Through the real FastAPI app (TestClient) against a real Neo4j:
  A. the upload succeeds (201) - this used to be a 500 ``KeyError: 'caller'``;
  B. the resulting graph holds the expected entities / relationships and is identical to the
     graph produced by ``POST /api/cases/ingest`` for the same document;
  C. the FIR -> accused Person INVOLVES relationships exist immediately after the upload;
  D. insights are generated;
  E. re-uploading the same file is idempotent (no new nodes, all entities matched, graph unchanged);
  plus: a legacy free-text upload still ingests through the legacy engine against the live DB.

Skipped automatically unless Neo4j is reachable (NEO4J_TEST_* env vars, see
test_live_neo4j_step0.py). WARNING: wipes the target database.
"""
import io
import json
import os
import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.live_neo4j

from tests.live.test_live_neo4j_step0 import _driver_or_skip  # noqa: E402
from tests.integration.test_unified_ingest import SAMPLE_TEXT_INPUT  # noqa: E402

LEGACY_URL = "/api/v1/ingest"
BULK_URL = "/api/cases/ingest"
CASE_FILE = "case_001_homicide.json"


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


def _sample_bytes(name):
    with open(os.path.join("dataset", name), "rb") as f:
        return f.read()


def _upload(client, *files):
    return client.post(
        LEGACY_URL,
        files=[("files", (name, io.BytesIO(content), mime)) for name, content, mime in files],
    )


def _count(driver, cypher):
    with driver.session() as s:
        return s.run(cypher).single()["c"]


# Business keys used by the MERGE statements in app/services/graph_writes.py, one per label.
_NODE_KEY = (
    "coalesce({n}.case_id, {n}.fir_id, {n}.person_id, {n}.phone_number, {n}.account_number, {n}.vin, "
    "{n}.handle_id, {n}.ip_address, {n}.cell_tower_id, {n}.location_id, {n}.prior_case_id, {n}.source_record_id)"
)


def _graph_signature(driver):
    """Deterministic, timestamp-free snapshot of the graph: (label, key) nodes + (label, type, label, key, key) rels."""
    with driver.session() as s:
        nodes = s.run(f"MATCH (n) RETURN labels(n) AS labels, {_NODE_KEY.format(n='n')} AS key").data()
        rels = s.run(
            "MATCH (a)-[r]->(b) RETURN labels(a) AS la, type(r) AS t, labels(b) AS lb, "
            f"{_NODE_KEY.format(n='a')} AS ka, {_NODE_KEY.format(n='b')} AS kb"
        ).data()
    assert all(n["key"] is not None for n in nodes), "node without a known business key - extend _NODE_KEY"
    return (
        sorted((tuple(sorted(n["labels"])), str(n["key"])) for n in nodes),
        sorted((tuple(sorted(r["la"])), r["t"], tuple(sorted(r["lb"])), str(r["ka"]), str(r["kb"])) for r in rels),
    )


def _wipe(driver):
    from backend.services.schema_manager import init_schema
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        init_schema(s)


def _insight_identities(insights):
    """Orientation-free identity per insight.

    POSSIBLE_CO_LOCATION ids depend on Neo4j's internal ``elementId()`` string ordering, which differs
    between freshly wiped databases even for the same code path (pre-existing full-detector behaviour,
    documented in test_equivalence_harness.py); those are identified by (location_id, entity set) instead.
    """
    return {
        ("POSSIBLE_CO_LOCATION", i["metadata"].get("location_id"), frozenset(i["entities_involved"]))
        if i["insight_type"] == "POSSIBLE_CO_LOCATION" else ("ID", i["insight_id"])
        for i in insights
    }


def test_case_001_upload_creates_expected_graph_with_involves_and_insights_and_is_idempotent(live):
    client, driver = live
    doc = json.loads(_sample_bytes(CASE_FILE))
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 0

    # --- A: succeeds ---------------------------------------------------------------------
    r = _upload(client, (CASE_FILE, _sample_bytes(CASE_FILE), "application/json"))
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["case_id"] == "CASE-2024-001"
    assert first["created"] == {"nodes": 21, "relationships": 40, "source_records": 1}
    assert first["matched_existing_entities"] == 0
    assert first["warnings"] == []

    # --- B: graph contains the expected entities / relationships ---------------------------
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 21
    assert _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c") == 40
    assert _count(driver, "MATCH (c:Case {case_id: 'CASE-2024-001'}) RETURN count(c) AS c") == 1
    assert _count(driver, "MATCH (p:Person) RETURN count(p) AS c") == len(doc["entities"]["people"])
    assert _count(driver, "MATCH (ph:Phone) RETURN count(ph) AS c") == len(doc["entities"]["phones"])
    assert _count(driver, "MATCH (b:BankAccount) RETURN count(b) AS c") == len(doc["entities"]["bank_accounts"])
    assert _count(driver, "MATCH (f:FIR) RETURN count(f) AS c") == len(doc["fir_records"])
    assert _count(driver, "MATCH (:Phone)-[r:CALLED]->(:Phone) RETURN count(r) AS c") == len(doc["relationships"]["communications"])
    assert _count(driver, "MATCH (:BankAccount)-[r:TRANSFERRED_TO]->(:BankAccount) RETURN count(r) AS c") == len(doc["relationships"]["transactions"])
    # No read-only relationship kinds are ever written.
    assert _count(driver, "MATCH ()-[r:LINKED_TO_IP]->() RETURN count(r) AS c") == 0

    # --- C: FIR -> accused Person INVOLVES present immediately ----------------------------
    expected_accused = doc["fir_records"][0]["accused_person_ids"]
    assert len(expected_accused) == 2
    with driver.session() as s:
        accused = s.run(
            "MATCH (:FIR)-[:INVOLVES]->(p:Person) RETURN p.person_id AS pid ORDER BY pid"
        ).value()
    assert accused == sorted(expected_accused)

    # --- D: insights generated -------------------------------------------------------------
    assert len(first["insights"]) > 0
    assert first["new_insights"] == len(first["insights"])
    assert {i["insight_type"] for i in first["insights"]} == {
        "TRANSFER_CHAIN", "INFRASTRUCTURE_REUSE", "POSSIBLE_CO_LOCATION", "PRIOR_CASE_LINK"
    }

    # --- E: re-upload is idempotent --------------------------------------------------------
    before = _graph_signature(driver)
    r2 = _upload(client, (CASE_FILE, _sample_bytes(CASE_FILE), "application/json"))
    assert r2.status_code == 201, r2.text
    second = r2.json()
    assert second["created"]["nodes"] == 0
    assert second["matched_existing_entities"] == 21
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 21
    assert _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c") == 40
    assert _graph_signature(driver) == before
    assert _insight_identities(second["insights"]) == _insight_identities(first["insights"])


def test_legacy_endpoint_produces_same_graph_as_bulk_endpoint(live):
    """B (strict): the legacy multipart upload yields a graph identical to POST /api/cases/ingest."""
    client, driver = live

    r_bulk = client.post(BULK_URL, json=json.loads(_sample_bytes(CASE_FILE)))
    assert r_bulk.status_code == 201, r_bulk.text
    bulk_graph = _graph_signature(driver)
    bulk_body = r_bulk.json()

    _wipe(driver)
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 0

    r_legacy = _upload(client, (CASE_FILE, _sample_bytes(CASE_FILE), "application/json"))
    assert r_legacy.status_code == 201, r_legacy.text
    legacy_graph = _graph_signature(driver)
    legacy_body = r_legacy.json()

    assert legacy_graph == bulk_graph
    assert len(legacy_graph[0]) == 21 and len(legacy_graph[1]) == 40
    assert legacy_body["created"] == bulk_body["created"]
    assert legacy_body["matched_existing_entities"] == bulk_body["matched_existing_entities"]
    assert _insight_identities(legacy_body["insights"]) == _insight_identities(bulk_body["insights"])
    assert len(legacy_body["insights"]) == len(bulk_body["insights"]) == 9


def test_legacy_text_upload_still_ingests_through_legacy_engine(live):
    """F (live): a legacy FIR narrative + CSV upload still works end to end against Neo4j."""
    client, driver = live
    from backend.services.ingestion_engine import CaseIngestionEngine

    calls = []
    original = CaseIngestionEngine.parse_file

    def spy(self, filename, content, api_key_override=None):
        calls.append(filename)
        return original(self, filename, content, api_key_override)

    with patch.object(CaseIngestionEngine, "parse_file", spy):
        r = _upload(client, ("fir_and_records.txt", SAMPLE_TEXT_INPUT.encode("utf-8"), "text/plain"))
    assert r.status_code == 201, r.text
    data = r.json()
    assert calls == ["fir_and_records.txt"]
    assert data["case_id"] == "CASE_0045_2026"
    assert data["created"]["nodes"] == 18
    assert data["created"]["relationships"] == 31
    assert _count(driver, "MATCH (c:Case {case_id: 'CASE_0045_2026'}) RETURN count(c) AS c") == 1
    assert _count(driver, "MATCH (n) RETURN count(n) AS c") == 18
    assert _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c") == 31
