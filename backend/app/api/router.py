from fastapi import APIRouter

from app.api.routes import datasets, endpoints, evaluation, health, projects, reports, runs

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(projects.router)
api_router.include_router(endpoints.router)
api_router.include_router(datasets.router)
api_router.include_router(runs.router)
api_router.include_router(evaluation.router)
api_router.include_router(reports.router)
