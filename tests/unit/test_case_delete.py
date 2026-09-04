import pytest
from unittest.mock import patch, MagicMock
from backend.database import db
from backend.services.graph_service import GraphService


def test_delete_case_unconfirmed_fails(client):
    """Calling DELETE /api/cases/{case_id} without confirm=true returns 400 Bad Request."""
    response = client.delete("/api/cases/CASE-001")
    assert response.status_code == 400
    data = response.json()
    assert "confirm=true" in (data.get("message") or data.get("detail", ""))


def test_delete_case_not_found(client):
    """Calling DELETE /api/cases/{case_id}?confirm=true for non-existent case returns 404."""
    with patch.object(GraphService, "delete_case", return_value=None):
        response = client.delete("/api/cases/NON_EXISTENT_CASE?confirm=true")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in (data.get("message") or data.get("detail", "")).lower()


def test_delete_case_confirmed_query_param(client):
    """Calling DELETE /api/cases/{case_id}?confirm=true returns 200 with deletion summary."""
    mock_summary = {
        "case_id": "CASE-2024-001",
        "nodes_removed": 14,
        "nodes_detached": 3,
        "relationships_removed": 22,
        "status": "deleted"
    }
    with patch.object(GraphService, "delete_case", return_value=mock_summary):
        response = client.delete("/api/cases/CASE-2024-001?confirm=true")
        assert response.status_code == 200
        data = response.json()
        assert data["case_id"] == "CASE-2024-001"
        assert data["nodes_removed"] == 14
        assert data["nodes_detached"] == 3
        assert data["relationships_removed"] == 22
        assert data["status"] == "deleted"


def test_delete_case_confirmed_body(client):
    """Calling DELETE /api/cases/{case_id} with JSON body {"confirm": true} returns 200."""
    mock_summary = {
        "case_id": "CASE-2024-002",
        "nodes_removed": 8,
        "nodes_detached": 1,
        "relationships_removed": 10,
        "status": "deleted"
    }
    with patch.object(GraphService, "delete_case", return_value=mock_summary):
        response = client.request(
            "DELETE",
            "/api/cases/CASE-2024-002",
            json={"confirm": True}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["case_id"] == "CASE-2024-002"
        assert data["nodes_removed"] == 8
        assert data["status"] == "deleted"


def test_graph_service_delete_case_logic():
    """Test GraphService.delete_case directly with mock session."""
    session = MagicMock()
    
    # Simulate: 1. Case exists
    # 2. Exclusive nodes stats: 10 nodes, 15 rels
    # 3. Shared rels: 3 rels
    # 4. Shared nodes: 2 nodes
    def mock_run(query, params=None):
        q_lower = query.lower()
        mock_res = MagicMock()
        if "return count(c) as count" in q_lower:
            mock_res.data.return_value = [{"count": 1}]
        elif "exclusive_rels" in q_lower:
            mock_res.data.return_value = [{"nodes_removed": 10, "exclusive_rels": 15}]
        elif "shared_rels" in q_lower:
            mock_res.data.return_value = [{"shared_rels": 3}]
        elif "nodes_detached" in q_lower:
            mock_res.data.return_value = [{"nodes_detached": 2}]
        else:
            mock_res.data.return_value = []
        return mock_res

    session.run.side_effect = mock_run

    result = GraphService.delete_case(session, "CASE-001")
    assert result is not None
    assert result["case_id"] == "CASE-001"
    assert result["nodes_removed"] == 10
    assert result["relationships_removed"] == 18  # 15 + 3
    assert result["nodes_detached"] == 2
    assert result["status"] == "deleted"


def test_graph_service_delete_case_not_found():
    """Test GraphService.delete_case returns None when case does not exist."""
    session = MagicMock()
    mock_res = MagicMock()
    mock_res.data.return_value = [{"count": 0}]
    session.run.return_value = mock_res

    result = GraphService.delete_case(session, "NON_EXISTENT")
    assert result is None
