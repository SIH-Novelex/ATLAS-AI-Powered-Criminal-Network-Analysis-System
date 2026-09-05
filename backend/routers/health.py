from fastapi import APIRouter, HTTPException, status, Response
from backend.database import db
from backend.logging_config import logger

router = APIRouter(prefix="/api", tags=["Health & System"])


@router.get("/health", summary="Health Check & Neo4j Connectivity")
def health_check():
    health = db.check_health()
    if health["status"] != "healthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=health
        )
    return health


@router.head("/health", include_in_schema=False)
def health_check_head():
    health = db.check_health()
    if health["status"] != "healthy":
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_200_OK)

