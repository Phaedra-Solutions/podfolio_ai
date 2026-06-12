from fastapi import APIRouter

from app.api.v1.endpoints import batch, episodes, health

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(episodes.router, prefix="/episodes", tags=["Episodes"])
api_router.include_router(batch.router, prefix="/episodes", tags=["Batch Processing"])
