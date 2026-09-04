import pytest
from unittest.mock import patch, MagicMock
from backend.models.path import ShortestPathResponse, AmbiguityCandidate, AmbiguousPathResponse
from backend.services.path_service import PathService


def test_shortest_path_exact_match(client, mock_session):
    mock_response = ShortestPathResponse(
        ambiguous=False,
        nodes=[
            {"id": "P-001", "name": "Devendra Sharma", "labels": ["Person"], "properties": {}},
            {"id": "+919876543210", "name": "+919876543210", "labels": ["Phone"], "properties": {}},
            {"id": "+919876543211", "name": "+919876543211", "labels": ["Phone"], "properties": {}},
            {"id": "P-003", "name": "Rajiv Sen", "labels": ["Person"], "properties": {}}
        ],
        relationships=[
            {"id": "rel1", "type": "OWNS", "start_node": "P-001", "end_node": "+919876543210", "properties": {}},
            {"id": "rel2", "type": "CALLED", "start_node": "+919876543210", "end_node": "+919876543211", "properties": {}},
            {"id": "rel3", "type": "OWNS", "start_node": "P-003", "end_node": "+919876543211", "properties": {}}
        ],
        path_length=3,
        summary="Person(Devendra Sharma) -> Phone(+919876543210) -> Phone(+919876543211) -> Person(Rajiv Sen)"
    )

    with patch.object(PathService, "find_shortest_path", return_value=mock_response):
        response = client.get("/api/cases/shortest-path?suspect_name=Devendra+Sharma&victim_name=Rajiv+Sen")
        assert response.status_code == 200
        data = response.json()
        assert data["ambiguous"] is False
        assert data["path_length"] == 3
        assert len(data["nodes"]) == 4
        assert len(data["relationships"]) == 3


def test_shortest_path_ambiguous_suspect(client):
    mock_ambiguous = AmbiguousPathResponse(
        ambiguous=True,
        message="Multiple Person entities match suspect 'John Smith'. Please specify 'suspect_id' to disambiguate.",
        candidates=[
            AmbiguityCandidate(person_id="P-001", name="John Smith", aliases=["Johnny"], dob="1985-04-12", case_ids=["CASE-01"]),
            AmbiguityCandidate(person_id="P-042", name="John Smith", aliases=["Smitty"], dob="1992-11-03", case_ids=["CASE-02"])
        ]
    )

    with patch.object(PathService, "find_shortest_path", return_value=mock_ambiguous):
        response = client.get("/api/cases/shortest-path?suspect_name=John+Smith&victim_name=Jane+Doe")
        assert response.status_code == 200
        data = response.json()
        assert data["ambiguous"] is True
        assert len(data["candidates"]) == 2
        assert data["candidates"][0]["person_id"] == "P-001"
        assert data["candidates"][1]["person_id"] == "P-042"


def test_shortest_path_ambiguous_victim(client):
    mock_ambiguous = AmbiguousPathResponse(
        ambiguous=True,
        message="Multiple Person entities match victim 'Jane Doe'. Please specify 'victim_id' to disambiguate.",
        candidates=[
            AmbiguityCandidate(person_id="P-101", name="Jane Doe", aliases=[], dob="1990-01-01", case_ids=["CASE-01"]),
            AmbiguityCandidate(person_id="P-102", name="Jane Doe", aliases=[], dob="1995-05-05", case_ids=["CASE-03"])
        ]
    )

    with patch.object(PathService, "find_shortest_path", return_value=mock_ambiguous):
        response = client.get("/api/cases/shortest-path?suspect_name=Devendra+Sharma&victim_name=Jane+Doe")
        assert response.status_code == 200
        data = response.json()
        assert data["ambiguous"] is True
        assert len(data["candidates"]) == 2


def test_shortest_path_no_path_found(client):
    mock_empty = ShortestPathResponse(
        ambiguous=False,
        nodes=[],
        relationships=[],
        path_length=0,
        summary="No path found between suspect 'Devendra Sharma' and victim 'Rajiv Sen' within depth 5."
    )

    with patch.object(PathService, "find_shortest_path", return_value=mock_empty):
        response = client.get("/api/cases/shortest-path?suspect_name=Devendra+Sharma&victim_name=Rajiv+Sen")
        assert response.status_code == 200
        data = response.json()
        assert data["path_length"] == 0
        assert data["nodes"] == []


def test_shortest_path_missing_params(client):
    response = client.get("/api/cases/shortest-path?suspect_name=Devendra+Sharma")
    assert response.status_code == 400
    assert "victim_name" in response.json()["message"]


def test_resolve_person_case_insensitive_alias(mock_session):
    mock_session.run = MagicMock(return_value=MagicMock(data=lambda: [
        {"person_id": "P-001", "name": "Devendra Sharma", "aliases": ["Deva", "The Broker"], "dob": "1980-01-01", "case_ids": ["CASE-01"]}
    ]))
    candidates = PathService._resolve_person(mock_session, name="The Broker")
    assert len(candidates) == 1
    assert candidates[0].person_id == "P-001"
    query_issued = mock_session.run.call_args[0][0]
    assert "toLower($name) IN [a IN p.aliases | toLower(a)]" in query_issued


