import json
import os
import pytest


def load_sample(filename: str):
    path = os.path.join("dataset", filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_ingest_envelope_success(client):
    payload = load_sample("envelope_sample.json")
    response = client.post("/api/cases/ingest", json=payload)
    assert response.status_code in [200, 201]
    data = response.json()
    assert data["case_id"] == "CASE-2024-003"
    assert data["dataset_id"] == "DS-2026-08"
    assert "created" in data
    assert "nodes" in data["created"]
    assert "relationships" in data["created"]
    assert "source_records" in data["created"]
    assert "matched_existing_entities" in data
    assert "new_cross_case_links" in data
    assert "new_insights" in data
    assert "insights" in data
    assert isinstance(data["insights"], list)


def test_ingest_direct_case_data(client):
    payload = load_sample("case_001_homicide.json")
    response = client.post("/api/cases/ingest", json=payload)
    assert response.status_code in [200, 201]
    data = response.json()
    assert data["case_id"] == "CASE-2024-001"
    assert data["created"]["nodes"] >= 4
    assert data["created"]["relationships"] >= 4


def test_ingest_idempotent_merge(client):
    payload = load_sample("case_001_homicide.json")
    # First ingestion
    resp1 = client.post("/api/cases/ingest?mode=merge", json=payload)
    assert resp1.status_code in [200, 201]

    # Second ingestion with same case (idempotent MERGE)
    resp2 = client.post("/api/cases/ingest?mode=merge", json=payload)
    assert resp2.status_code in [200, 201]
    assert resp2.json()["case_id"] == "CASE-2024-001"


def test_ingest_validation_error(client):
    # Missing required case_metadata and case_id
    invalid_payload = {"some_other_field": "invalid"}
    response = client.post("/api/cases/ingest", json=invalid_payload)
    assert response.status_code == 422
    assert "error" in response.json()

