"""ORM models. Import every model here so Alembic autogenerate sees the full metadata."""

from app.db.models.auth import ApiToken, AuditEvent, AuthSettings, User, UserSession
from app.db.models.dataset import Dataset, DatasetCase, DatasetVersion
from app.db.models.endpoint import ModelEndpoint
from app.db.models.evaluation import EvaluationProfile, EvaluationResult
from app.db.models.project import Project
from app.db.models.report import Comparison, Report
from app.db.models.run import (
    BenchmarkRun,
    ResourceSample,
    RunAggregateMetric,
    RunCaseResult,
    RunDataset,
    RunModel,
)

__all__ = [
    "ApiToken",
    "AuditEvent",
    "AuthSettings",
    "BenchmarkRun",
    "Comparison",
    "Dataset",
    "DatasetCase",
    "DatasetVersion",
    "EvaluationProfile",
    "EvaluationResult",
    "ModelEndpoint",
    "Project",
    "Report",
    "ResourceSample",
    "RunAggregateMetric",
    "RunCaseResult",
    "RunDataset",
    "RunModel",
    "User",
    "UserSession",
]
