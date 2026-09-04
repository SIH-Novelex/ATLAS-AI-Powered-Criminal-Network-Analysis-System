from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, status
from neo4j.exceptions import ServiceUnavailable

from backend.database import db
from backend.models.insights import InsightItem
from backend.services.insights_engine import InsightsEngine
from backend.logging_config import logger

router = APIRouter(tags=["Insights & Pattern Detection"])


@router.get("/api/cases/{case_id}/insights", response_model=List[InsightItem], summary="Get Case Insights")
def get_case_insights(
    case_id: str,
    severity: Optional[str] = Query(None, description="Filter by severity: CRITICAL, HIGH, MEDIUM, LOW"),
    insight_type: Optional[str] = Query(None, alias="type", description="Filter by insight type")
):
    try:
        with db.get_session() as session:
            # First check if case exists
            case_check = session.run("MATCH (c:Case {case_id: $case_id}) RETURN c", {"case_id": case_id}).single()
            if not case_check:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Case '{case_id}' not found."
                )

            insights = InsightsEngine.run_all_detectors(session, case_id=case_id)
            
            # Apply filters
            if severity:
                insights = [i for i in insights if i.severity.upper() == severity.upper()]
            if insight_type:
                insights = [i for i in insights if i.insight_type.upper() == insight_type.upper()]

            return insights
    except ServiceUnavailable as se:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(se))


@router.get("/api/insights/delta", response_model=List[InsightItem], summary="Get Cross-Case Delta Insights")
def get_delta_insights(
    case_id: Optional[str] = Query(None, description="Optional case ID filter"),
    since: Optional[str] = Query(None, description="ISO timestamp to filter recently discovered insights")
):
    try:
        with db.get_session() as session:
            insights = InsightsEngine.run_all_detectors(session, case_id=case_id)
            # Filter for cross-case insight types
            cross_types = ["SHARED_ENTITY", "CROSS_CASE_LINK", "BRIDGE_NODE", "INFRASTRUCTURE_REUSE"]
            delta = [i for i in insights if i.insight_type in cross_types]
            if since:
                delta = [i for i in delta if i.created_at >= since]
            return delta
    except ServiceUnavailable as se:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(se))


@router.get("/api/cases/{case_id}/ai-insights", summary="Generate AI Case Insights with Gemini")
@router.post("/api/cases/{case_id}/ai-insights", summary="Generate AI Case Insights with Gemini")
def get_case_ai_insights(case_id: str):
    from backend.services.gemini_service import GeminiService
    try:
        with db.get_session() as session:
            return GeminiService.generate_case_brief(session, case_id=case_id)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except ServiceUnavailable as se:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(se))
    except Exception as e:
        logger.error(f"Error generating AI insights for case {case_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


