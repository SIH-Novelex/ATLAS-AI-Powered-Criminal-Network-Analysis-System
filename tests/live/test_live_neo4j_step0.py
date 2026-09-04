"""
Live-Neo4j verification for the Step 0 fixes.

These tests are SKIPPED automatically unless a Neo4j instance is reachable.
Configure via environment variables (defaults match docker-compose.yml):

    NEO4J_TEST_URI=bolt://localhost:7687
    NEO4J_TEST_USERNAME=neo4j
    NEO4J_TEST_PASSWORD=password

WARNING: the tests wipe the target database (MATCH (n) DETACH DELETE n).
Never point them at a production or shared instance.
"""
import json
import os

import pytest

pytestmark = pytest.mark.live_neo4j

URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_TEST_USERNAME", "neo4j")
PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "password")


def _driver_or_skip():
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD), connection_timeout=2.0)
        driver.verify_connectivity()
        return driver
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Live Neo4j not reachable at {URI}: {exc}")


def _load_case(filename: str):
    from backend.models.case_input import CaseData
    with open(os.path.join("dataset", filename), "r", encoding="utf-8") as f:
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


def test_fir_accused_involves_exists_after_first_ingestion(live_session):
    from backend.services.ingestion_service import IngestionService

    case = _load_case("case_001_homicide.json")
    expected = len(case.fir_records[0].accused_person_ids)
    assert expected == 2

    IngestionService.ingest_case(live_session, case)

    count = live_session.run(
        "MATCH (f:FIR {fir_id: $fir_id})-[:INVOLVES]->(p:Person) RETURN count(p) AS c",
        {"fir_id": case.fir_records[0].fir_id},
    ).single()["c"]
    assert count == expected, "FIR->accused INVOLVES must exist after the FIRST ingestion"

    ids = [r["pid"] for r in live_session.run(
        "MATCH (:FIR)-[:INVOLVES]->(p:Person) RETURN p.person_id AS pid ORDER BY pid"
    )]
    assert ids == sorted(case.fir_records[0].accused_person_ids)


def test_reingest_does_not_duplicate_aliases_or_roles(live_session):
    from backend.services.ingestion_service import IngestionService

    case = _load_case("case_001_homicide.json")
    p001 = next(p for p in case.entities.people if p.person_id == "P-001")

    for _ in range(3):
        IngestionService.ingest_case(live_session, case)

    row = live_session.run(
        "MATCH (p:Person {person_id: 'P-001'}) RETURN p.aliases AS aliases, p.roles AS roles"
    ).single()
    assert row["aliases"] == p001.aliases, f"aliases duplicated: {row['aliases']}"
    assert row["roles"] == p001.roles, f"roles duplicated: {row['roles']}"


def test_cross_case_reingest_merges_new_values_without_duplicates(live_session):
    """case_002 re-declares P-001 with a subset of aliases and one new role."""
    from backend.services.ingestion_service import IngestionService

    c1 = _load_case("case_001_homicide.json")
    c2 = _load_case("case_002_fraud.json")
    for case in (c1, c2, c2, c1):
        IngestionService.ingest_case(live_session, case)

    row = live_session.run(
        "MATCH (p:Person {person_id: 'P-001'}) RETURN p.aliases AS aliases, p.roles AS roles, p.case_ids AS case_ids"
    ).single()
    assert row["aliases"] == ["Deva", "The Broker"]
    assert row["roles"] == ["Suspect", "Organizer", "Director"]
    assert row["case_ids"] == ["CASE-2024-001", "CASE-2024-002"]

    total_links = live_session.run("MATCH (:FIR)-[:INVOLVES]->(:Person) RETURN count(*) AS c").single()["c"]
    assert total_links == 4  # 001: P-001,P-002 ; 002: P-001,P-005
