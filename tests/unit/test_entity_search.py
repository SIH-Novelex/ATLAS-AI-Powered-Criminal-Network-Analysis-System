import pytest
from unittest.mock import patch
from backend.services.graph_service import GraphService


def test_entity_search(client):
    mock_results = [
        {
            "entity_id": "P-001",
            "display_name": "Devendra Sharma",
            "labels": ["Person"],
            "case_ids": ["CASE-2024-001"],
            "properties": {"name": "Devendra Sharma", "dob": "1982-06-14"}
        }
    ]

    with patch.object(GraphService, "search_entities", return_value=mock_results):
        response = client.get("/api/entities/search?q=Devendra")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["display_name"] == "Devendra Sharma"


def test_entity_360_profile(client):
    mock_profile = {
        "entity_id": "P-001",
        "labels": ["Person"],
        "properties": {"person_id": "P-001", "name": "Devendra Sharma"},
        "total_connections": 2,
        "connections": [
            {
                "relationship": "OWNS",
                "direction": "OUTGOING",
                "neighbor_id": "+919876543210",
                "neighbor_name": "+919876543210",
                "neighbor_labels": ["Phone"]
            }
        ]
    }

    with patch.object(GraphService, "get_entity_360", return_value=mock_profile):
        response = client.get("/api/entities/P-001")
        assert response.status_code == 200
        data = response.json()
        assert data["entity_id"] == "P-001"
        assert len(data["connections"]) == 1


def test_relationship_lookup(client):
    mock_rel = {
        "relationship_id": "CALL-2024-001",
        "type": "CALLED",
        "properties": {"timestamp": "2024-10-15T18:30:00Z", "duration_seconds": 180},
        "source": {"id": "+919876543210", "name": "+919876543210", "labels": ["Phone"]},
        "target": {"id": "+919876543211", "name": "+919876543211", "labels": ["Phone"]}
    }

    with patch.object(GraphService, "get_relationship_by_id", return_value=mock_rel):
        response = client.get("/api/relationships/CALL-2024-001")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "CALLED"
        assert data["source"]["id"] == "+919876543210"


def test_get_subgraph(client):
    mock_subgraph = {
        "case_id": "CASE-2024-001",
        "total_nodes": 2,
        "total_edges": 1,
        "nodes": [{"id": "P-001", "labels": ["Person"], "name": "Devendra Sharma"}],
        "edges": [{"id": "rel1", "type": "OWNS", "source": "P-001", "target": "+919876543210"}]
    }

    with patch.object(GraphService, "get_subgraph", return_value=mock_subgraph):
        response = client.get("/api/graph?case_id=CASE-2024-001")
        assert response.status_code == 200
        data = response.json()
        assert data["total_nodes"] == 2
        assert data["total_edges"] == 1


def test_list_cases_and_summary(client):
    mock_cases = [
        {
            "case_id": "CASE-2024-001",
            "case_name": "Operation Nightfall",
            "case_type": "HOMICIDE",
            "status": "OPEN",
            "total_entities": 10
        }
    ]
    mock_summary = {
        "case_id": "CASE-2024-001",
        "metadata": {"case_id": "CASE-2024-001", "case_name": "Operation Nightfall"},
        "entity_breakdown": {"Person": 4, "Phone": 3, "BankAccount": 3},
        "total_entities": 10
    }

    with patch.object(GraphService, "list_cases", return_value=mock_cases):
        resp_list = client.get("/api/cases")
        assert resp_list.status_code == 200
        assert len(resp_list.json()) == 1

    with patch.object(GraphService, "get_case_summary", return_value=mock_summary):
        resp_sum = client.get("/api/cases/CASE-2024-001")
        assert resp_sum.status_code == 200
        assert resp_sum.json()["case_id"] == "CASE-2024-001"
        assert resp_sum.json()["total_entities"] == 10

