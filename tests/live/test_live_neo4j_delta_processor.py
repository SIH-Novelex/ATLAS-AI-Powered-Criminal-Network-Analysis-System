"""
Live-Neo4j verification for the DeltaProcessor write path (Phase 2 / B).

Write parity: replaying every record of a case through DeltaProcessor must produce a graph
identical (labels, properties, relationships) to bulk ingestion of the same case. This is the
graph-level half of the Phase 2 equivalence harness; the insight-level assertions
(ids(F) == ids(F'), ids(F) ⊆ ids(S)) are added with the scoped detectors.

Skipped automatically unless Neo4j is reachable (NEO4J_TEST_* env vars, see test_live_neo4j_step0.py).
WARNING: wipes the target database.
"""
import json
import os
import sys

import pytest

pytestmark = pytest.mark.live_neo4j

from tests.live.test_live_neo4j_step0 import _driver_or_skip  # noqa: E402
from tests.unit.test_delta_processor import events_from_case_doc  # noqa: E402

FILES = [
    os.path.join("dataset", "case_001_homicide.json"),
    os.path.join("dataset", "case_002_fraud.json"),
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "case_edge_batching.json"),
]
VOLATILE = {"created_at", "updated_at", "ingested_at"}


def _docs():
    out = []
    for f in FILES:
        with open(f, "r", encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def _key(props):
    from backend.services import graph_writes as gw
    for k in gw.MERGE_KEYS.values():
        if k in props:
            return f"{k}={props[k]}"
    return json.dumps(props, sort_keys=True, default=str)


def graph_snapshot(session):
    nodes = sorted(
        ({"labels": sorted(r["l"]), "props": {k: v for k, v in r["p"].items() if k not in VOLATILE}}
         for r in session.run("MATCH (n) RETURN labels(n) AS l, properties(n) AS p")),
        key=lambda x: json.dumps(x, sort_keys=True, default=str))
    rels = sorted(
        ({"start": f"{sorted(r['la'])}:{_key(r['pa'])}", "type": r["t"],
          "props": {k: v for k, v in r["pr"].items() if k not in VOLATILE},
          "end": f"{sorted(r['lb'])}:{_key(r['pb'])}"}
         for r in session.run("MATCH (a)-[x]->(b) RETURN labels(a) AS la, properties(a) AS pa, type(x) AS t, "
                              "properties(x) AS pr, labels(b) AS lb, properties(b) AS pb")),
        key=lambda x: json.dumps(x, sort_keys=True, default=str))
    return nodes, rels


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


def _merge_case_node(session, case_meta_dict):
    """Events never create Case nodes; the harness creates them with the shared CASE_MERGE statement."""
    from backend.models.case_input import CaseMetadata
    from backend.models.common import get_current_iso_time
    from backend.services import graph_writes as gw
    session.run(gw.CASE_MERGE, {**gw.case_params(CaseMetadata(**case_meta_dict)), "now": get_current_iso_time()})


def test_event_replay_produces_identical_graph_to_bulk_ingest(live_session):
    from backend.models.case_input import CaseData
    from backend.models.event import EventBatch
    from backend.services.delta_processor import DeltaProcessor
    from backend.services.ingestion_service import IngestionService
    from backend.services.schema_manager import init_schema

    docs = _docs()

    # Reference path: bulk ingest all three payloads.
    bulk_responses = [IngestionService.ingest_case(live_session, CaseData(**d)) for d in docs]
    ref_nodes, ref_rels = graph_snapshot(live_session)
    assert len(ref_nodes) == 47 and len(ref_rels) == 90  # values recorded in Step 1B verification

    # Incremental path: fresh DB, Case nodes via the shared CASE_MERGE, every record as an event.
    live_session.run("MATCH (n) DETACH DELETE n")
    init_schema(live_session)
    processor = DeltaProcessor()
    results = []
    for d in docs:
        _merge_case_node(live_session, d["case_metadata"])
        results.append(processor.process_events(live_session, EventBatch(case_id=d["case_metadata"]["case_id"], events=events_from_case_doc(d))))
    ev_nodes, ev_rels = graph_snapshot(live_session)

    assert ev_nodes == ref_nodes, "node multiset differs between bulk ingestion and event replay"
    assert ev_rels == ref_rels, "relationship multiset differs between bulk ingestion and event replay"

    # Write counts: identical relationship / matched counts; node counts differ by exactly the Case node
    # (created by bulk, never by events).
    for bulk, ev in zip(bulk_responses, results):
        assert ev.writes.relationships_written == bulk.created.relationships
        assert ev.writes.nodes_matched == bulk.matched_existing_entities
        assert ev.writes.nodes_created == bulk.created.nodes - 1

    # Cross-case join detection: case_002 re-declares P-001, its phone, its account and the IP.
    assert results[1].touched.case_ids_changed == {
        "Person:P-001", "Phone:+919876543210", "BankAccount:ACC-9001-DEV", "IPAddress:198.51.100.45",
    }
    assert results[0].touched.case_ids_changed == set()  # first case: everything brand new


def test_event_replay_is_idempotent_and_reports_no_joins(live_session):
    from backend.models.event import EventBatch
    from backend.services.delta_processor import DeltaProcessor

    d = _docs()[0]
    _merge_case_node(live_session, d["case_metadata"])
    processor = DeltaProcessor()
    first = processor.process_events(live_session, EventBatch(case_id="CASE-2024-001", events=events_from_case_doc(d)))
    nodes0, rels0 = graph_snapshot(live_session)
    second = processor.process_events(live_session, EventBatch(case_id="CASE-2024-001", events=events_from_case_doc(d)))
    nodes1, rels1 = graph_snapshot(live_session)

    assert first.writes.nodes_created == 20 and first.writes.nodes_matched == 0
    assert second.writes.nodes_created == 0 and second.writes.nodes_matched == 20
    assert second.touched.case_ids_changed == set()
    assert (nodes0, rels0) == (nodes1, rels1)
    # FIR->accused links exist after the FIRST batch (Step 0 ordering preserved in the event path)
    assert live_session.run("MATCH (:FIR)-[:INVOLVES]->(p:Person) RETURN count(p) AS c").single()["c"] == 2
    # aliases not duplicated by the replay (shared PEOPLE_MERGE)
    assert live_session.run("MATCH (p:Person {person_id:'P-001'}) RETURN p.aliases AS a").single()["a"] == ["Deva", "The Broker"]


def test_batch_is_atomic_on_failure(live_session):
    """A statement failure inside the batch rolls back every write of that batch."""
    from backend.models.event import EventBatch
    from backend.services import graph_writes as gw
    from backend.services.delta_processor import DeltaProcessor

    d = _docs()[0]
    _merge_case_node(live_session, d["case_metadata"])
    # Violate the Person uniqueness constraint mid-batch: person_id is the MERGE key so MERGE never violates it;
    # instead break the batch by making a later statement invalid.
    processor = DeltaProcessor()
    original = gw.ALL_WRITE_STATEMENTS["CALLED_MERGE"]
    gw.ALL_WRITE_STATEMENTS["CALLED_MERGE"] = "THIS IS NOT CYPHER"
    try:
        with pytest.raises(Exception):
            processor.process_events(live_session, EventBatch(case_id="CASE-2024-001", events=events_from_case_doc(d)))
    finally:
        gw.ALL_WRITE_STATEMENTS["CALLED_MERGE"] = original
    # Nothing from the batch persisted: only the Case node exists.
    assert live_session.run("MATCH (n) RETURN count(n) AS c").single()["c"] == 1
    assert live_session.run("MATCH (p:Person) RETURN count(p) AS c").single()["c"] == 0
