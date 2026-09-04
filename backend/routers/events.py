"""
Incremental event API (Phase 2, stage 4).

    POST /api/events/batch   EventBatch -> EventProcessingResult

Pipeline: EventBatch (validated by FastAPI/Pydantic) -> DeltaProcessor
(one write transaction, graph_writes statements only) -> touched entities ->
scoped detector dispatcher (global scope) -> EventProcessingResult.

Contract highlights (kept in sync with app/models/event.py and DeltaProcessor):
* Unsupported event types (delete / retract / correction / IP-link / typos) and unknown
  fields fail validation -> 422 (the application-wide RequestValidationError handler).
* The target case must already exist -> 404 otherwise. The event API never creates
  Case nodes; bulk ingestion (/api/cases/ingest) owns case metadata.
* Batches are atomic: a failure inside the write transaction rolls back every write of
  the batch and surfaces as 500 (Neo4jError) / 503 (ServiceUnavailable).
* Detector failures do NOT fail the request: they are reported per detector in
  ``detectors_run`` (status="failed", error=...) and listed in ``detectors_failed``.
  A failed detector is never presented as a successful empty result.
* Idempotent: replaying the same batch re-applies the same MERGE semantics as bulk
  ingestion (nodes_created=0, nodes_matched=N, no graph change).
"""
from fastapi import APIRouter, Body, HTTPException, Response, status
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from backend.database import db
from backend.logging_config import logger
from backend.models.event import EventBatch, EventProcessingResult
from backend.services.delta_processor import CaseNotFoundError, DeltaProcessor

router = APIRouter(prefix="/api/events", tags=["Incremental Events"])

# One processor instance for the router: stateless, default runner = scoped dispatcher (global scope).
_processor = DeltaProcessor()


@router.post(
    "/batch",
    response_model=EventProcessingResult,
    summary="Apply a batch of incremental events to an existing case",
    response_description="Write counts, touched entities, detectors run/failed/skipped and scoped insights",
    status_code=status.HTTP_200_OK,
)
def process_event_batch(
    response: Response,
    batch: EventBatch = Body(..., description="Events for ONE existing case (1..5000), applied in one write transaction"),
):
    try:
        with db.get_session() as session:
            result = _processor.process_events(session, batch)
            # 201 when the batch created at least one primary node, 200 when everything matched (same rule as /api/cases/ingest)
            if result.writes.nodes_created > 0 and result.writes.nodes_matched == 0:
                response.status_code = status.HTTP_201_CREATED
            else:
                response.status_code = status.HTTP_200_OK
            return result

    except CaseNotFoundError as nf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(nf))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ve))
    except ServiceUnavailable as se:
        logger.error(f"Neo4j database unavailable during event processing: {se}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database service unavailable: {str(se)}",
        )
    except Neo4jError as ne:
        logger.error(f"Neo4j Cypher error during event processing (batch rolled back): {ne}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Graph database transaction error (batch rolled back): {str(ne)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected event processing error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal event processing failure: {str(e)}",
        )
