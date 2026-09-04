import pytest
from unittest.mock import patch
from backend.database import db


def test_health_check_healthy(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "database" in data
    assert "version" in data


def test_health_check_unhealthy(client):
    with patch.object(db, "check_health", return_value={"status": "unhealthy", "error": "Connection refused", "database": "neo4j"}):
        response = client.get("/api/health")
        assert response.status_code == 503
        data = response.json()
        assert data["detail"]["status"] == "unhealthy"


def test_cases_reset_unconfirmed_fails(client):
    response = client.delete("/api/cases/reset")
    assert response.status_code == 400
    assert "confirm=true" in response.json()["message"]


def test_cases_reset_confirmed_succeeds(client):
    response = client.delete("/api/cases/reset?confirm=true")
    assert response.status_code == 200
    assert response.json()["status"] == "success"

