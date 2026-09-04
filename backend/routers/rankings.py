from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status
from neo4j.exceptions import ServiceUnavailable

from backend.database import db
from backend.models.rankings import RankingsResponse
from backend.services.ranking_service import RankingService
from backend.logging_config import logger

router = APIRouter(tags=["Centrality & Rankings"])


def _handle_rankings(
    metric: str,
    label: Optional[str],
    limit: int,
    case_id: Optional[str]
) -> RankingsResponse:
    try:
        with db.get_session() as session:
            return RankingService.get_rankings(
                session=session,
                metric=metric,
                label=label,
                limit=limit,
                case_id=case_id
            )
    except ServiceUnavailable as se:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(se))


@router.get("/api/cases/rankings", response_model=RankingsResponse, summary="Entity Centrality & Suspect Rankings")
def get_cases_rankings(
    metric: str = Query("degree", description="Ranking algorithm: degree, weighted_degree, betweenness, pagerank, cross_case_relevance"),
    label: Optional[str] = Query("Person", description="Target node label: Person, Phone, BankAccount, Vehicle, etc."),
    limit: int = Query(10, ge=1, le=100, description="Max entities to return"),
    case_id: Optional[str] = Query(None, description="Optional case filter")
):
    return _handle_rankings(metric, label, limit, case_id)


@router.get("/api/analytics/rankings", response_model=RankingsResponse, summary="Alias: Graph Analytics Rankings")
def get_analytics_rankings(
    metric: str = Query("degree", description="Ranking algorithm: degree, weighted_degree, betweenness, pagerank, cross_case_relevance"),
    label: Optional[str] = Query("Person", description="Target node label: Person, Phone, BankAccount, Vehicle, etc."),
    limit: int = Query(10, ge=1, le=100, description="Max entities to return"),
    case_id: Optional[str] = Query(None, description="Optional case filter")
):
    return _handle_rankings(metric, label, limit, case_id)

