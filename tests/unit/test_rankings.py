import pytest
from unittest.mock import patch
from backend.models.rankings import RankingsResponse, RankingItem
from backend.services.ranking_service import RankingService


def test_rankings_degree_cypher(client):
    mock_resp = RankingsResponse(
        metric="degree",
        label="Person",
        supported=True,
        total_ranked=2,
        rankings=[
            RankingItem(id="P-001", name="Devendra Sharma", labels=["Person"], score=8.0, rank=1, case_ids=["CASE-2024-001"], details={"degree": 8}),
            RankingItem(id="P-002", name="Karan Singhania", labels=["Person"], score=5.0, rank=2, case_ids=["CASE-2024-001"], details={"degree": 5})
        ]
    )

    with patch.object(RankingService, "get_rankings", return_value=mock_resp):
        response = client.get("/api/cases/rankings?metric=degree&label=Person&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["metric"] == "degree"
        assert data["supported"] is True
        assert len(data["rankings"]) == 2
        assert data["rankings"][0]["score"] == 8.0


def test_rankings_weighted_degree(client):
    mock_resp = RankingsResponse(
        metric="weighted_degree",
        label="Person",
        supported=True,
        total_ranked=1,
        rankings=[
            RankingItem(id="P-001", name="Devendra Sharma", labels=["Person"], score=500010.5, rank=1, case_ids=["CASE-2024-001"], details={"raw_degree": 8})
        ]
    )

    with patch.object(RankingService, "get_rankings", return_value=mock_resp):
        response = client.get("/api/cases/rankings?metric=weighted_degree")
        assert response.status_code == 200
        data = response.json()
        assert data["metric"] == "weighted_degree"
        assert data["supported"] is True


def test_rankings_cross_case_relevance(client):
    mock_resp = RankingsResponse(
        metric="cross_case_relevance",
        label="Person",
        supported=True,
        total_ranked=1,
        rankings=[
            RankingItem(id="P-001", name="Devendra Sharma", labels=["Person"], score=28.0, rank=1, case_ids=["CASE-01", "CASE-02"], details={"case_count": 2, "degree": 8})
        ]
    )

    with patch.object(RankingService, "get_rankings", return_value=mock_resp):
        response = client.get("/api/cases/rankings?metric=cross_case_relevance")
        assert response.status_code == 200
        data = response.json()
        assert data["metric"] == "cross_case_relevance"
        assert data["supported"] is True


def test_rankings_gds_unavailable_handled(client):
    mock_gds_unsupported = RankingsResponse(
        metric="pagerank",
        label="Person",
        supported=False,
        reason="Neo4j Graph Data Science (GDS) library is not installed or available on this Neo4j instance.",
        total_ranked=0,
        rankings=[]
    )

    with patch.object(RankingService, "get_rankings", return_value=mock_gds_unsupported):
        response = client.get("/api/cases/rankings?metric=pagerank")
        assert response.status_code == 200
        data = response.json()
        assert data["metric"] == "pagerank"
        assert data["supported"] is False
        assert "GDS" in data["reason"] or "Graph Data Science" in data["reason"]
        assert len(data["rankings"]) == 0

