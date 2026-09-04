import json
import os
import pytest


def test_ingest_replace_without_confirm_fails(client):
    with open(os.path.join("dataset", "envelope_sample.json"), "r", encoding="utf-8") as f:
        payload = json.load(f)

    # mode=replace without confirm_replace=true must return 409 Conflict
    response = client.post("/api/cases/ingest?mode=replace", json=payload)
    assert response.status_code == 409
    data = response.json()
    assert "confirm_replace" in data["detail"] or "confirm_replace" in data["message"]


def test_ingest_replace_with_confirm_success(client):
    with open(os.path.join("dataset", "envelope_sample.json"), "r", encoding="utf-8") as f:
        payload = json.load(f)

    # mode=replace with confirm_replace=true must succeed
    response = client.post("/api/cases/ingest?mode=replace&confirm_replace=true", json=payload)
    assert response.status_code in [200, 201]
    data = response.json()
    assert data["case_id"] == "CASE-2024-003"

