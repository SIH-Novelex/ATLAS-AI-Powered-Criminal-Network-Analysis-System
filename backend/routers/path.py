from typing import Optional, Union
from fastapi import APIRouter, HTTPException, Query, status
from neo4j.exceptions import ServiceUnavailable

from backend.database import db
from backend.models.path import ShortestPathResponse, AmbiguousPathResponse
from backend.services.path_service import PathService
from backend.logging_config import logger

router = APIRouter(tags=["Shortest Path Analysis"])


def _handle_shortest_path(
    suspect_name: Optional[str],
    victim_name: Optional[str],
    suspect_id: Optional[str],
    victim_id: Optional[str],
    max_depth: int
) -> Union[ShortestPathResponse, AmbiguousPathResponse]:
    if not (suspect_name or suspect_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either 'suspect_name' or 'suspect_id'."
        )
    if not (victim_name or victim_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either 'victim_name' or 'victim_id'."
        )

    try:
        with db.get_session() as session:
            result = PathService.find_shortest_path(
                session=session,
                suspect_name=suspect_name,
                victim_name=victim_name,
                suspect_id=suspect_id,
                victim_id=victim_id,
                max_depth=max_depth
            )

            # If ambiguous, return the AmbiguousPathResponse
            if getattr(result, "ambiguous", False):
                return result

            # If person wasn't found at all
            if result.path_length == 0 and "not found" in (result.summary or ""):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=result.summary
                )

            return result
    except ServiceUnavailable as se:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(se))


@router.get("/api/cases/shortest-path", response_model=Union[ShortestPathResponse, AmbiguousPathResponse], summary="Find Shortest Path between Suspect and Victim")
def get_cases_shortest_path(
    suspect_name: Optional[str] = Query(None, description="Suspect full name"),
    victim_name: Optional[str] = Query(None, description="Victim full name"),
    suspect_id: Optional[str] = Query(None, description="Suspect person_id"),
    victim_id: Optional[str] = Query(None, description="Victim person_id"),
    max_depth: int = Query(5, ge=1, le=15, description="Maximum hops to traverse")
):
    return _handle_shortest_path(suspect_name, victim_name, suspect_id, victim_id, max_depth)


@router.get("/api/graph/shortest-path", response_model=Union[ShortestPathResponse, AmbiguousPathResponse], summary="Alias: Shortest Path Traversal")
def get_graph_shortest_path(
    suspect_name: Optional[str] = Query(None, description="Suspect full name"),
    victim_name: Optional[str] = Query(None, description="Victim full name"),
    suspect_id: Optional[str] = Query(None, description="Suspect person_id"),
    victim_id: Optional[str] = Query(None, description="Victim person_id"),
    max_depth: int = Query(5, ge=1, le=15, description="Maximum hops to traverse")
):
    return _handle_shortest_path(suspect_name, victim_name, suspect_id, victim_id, max_depth)

