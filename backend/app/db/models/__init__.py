"""ORM models. Import every model here so Alembic autogenerate sees the full metadata."""

from app.db.models.dataset import Dataset, DatasetCase, DatasetVersion
from app.db.models.endpoint import ModelEndpoint
from app.db.models.evaluation import EvaluationProfile, EvaluationResult
from app.db.models.project import Project
from app.db.models.run import (
    BenchmarkRun,
    RunAggregateMetric,
    RunCaseResult,
    RunDataset,
    RunModel,
)

__all__ = [
    "BenchmarkRun",
    "Dataset",
    "DatasetCase",
    "DatasetVersion",
    "EvaluationProfile",
    "EvaluationResult",
    "ModelEndpoint",
    "Project",
    "RunAggregateMetric",
    "RunCaseResult",
    "RunDataset",
    "RunModel",
]
