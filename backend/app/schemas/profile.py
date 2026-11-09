"""Evaluation profile schemas (spec §8.1 EvaluationProfile, §9.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.evaluation import EvaluationConfig
from app.schemas.run import GenerationConfig, LoadConfig, validate_suites


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_id: uuid.UUID | None = None
    suites: list[str] = Field(default_factory=lambda: ["performance"], min_length=1)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    load: LoadConfig = Field(default_factory=LoadConfig)

    @field_validator("suites")
    @classmethod
    def _suites(cls, v: list[str]) -> list[str]:
        return validate_suites(v)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_id: uuid.UUID | None = None
    suites: list[str] | None = Field(default=None, min_length=1)
    evaluation: EvaluationConfig | None = None
    generation: GenerationConfig | None = None
    load: LoadConfig | None = None

    @field_validator("suites")
    @classmethod
    def _suites(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else validate_suites(v)


class ProfileRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    description: str | None
    suites: list[str]
    evaluation: dict[str, Any]
    generation: dict[str, Any]
    load: dict[str, Any]
    created_at: datetime
    updated_at: datetime
