from fastapi import APIRouter, HTTPException, status
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

