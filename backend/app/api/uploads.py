"""Read dataset content from either a JSON body or a multipart upload, with size caps.

JSON (used by the web UI and CLI)::

    {"name": "...", "dataset_type": "qa", "content": "<jsonl or csv text>", "filename": "x.jsonl"}

Multipart (convenient with curl)::

    curl -F name=my-set -F dataset_type=qa -F tags=a,b -F file=@cases.jsonl .../datasets
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile

from app.core.config import Settings
from app.core.exceptions import AppError

# Room for the JSON envelope / multipart framing around the dataset content itself.
_OVERHEAD = 1024 * 1024
_CHUNK = 64 * 1024


@dataclass
class Upload[T: BaseModel]:
    data: T
    raw: bytes | None


def _too_large(settings: Settings) -> AppError:
    return AppError(
        f"The request is larger than the {settings.dataset_max_upload_bytes}-byte dataset "
        "limit (DATASET_MAX_UPLOAD_BYTES).",
        code="payload_too_large",
        status_code=413,
    )


def _invalid(exc: ValidationError) -> AppError:
    errors = [
        {k: v for k, v in e.items() if k not in ("input", "ctx", "url")}
        for e in exc.errors(include_url=False)
    ]
    for e in errors:
        e["loc"] = ["body", *e["loc"]]
    return AppError(
        "Request validation failed", code="validation_error", status_code=422, details=errors
    )


async def _read_body(request: Request, cap: int, settings: Settings) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise _too_large(settings)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > cap:
            raise _too_large(settings)
    return bytes(body)


async def read_upload[T: BaseModel](
    request: Request, model: type[T], settings: Settings
) -> Upload[T]:
    cap = settings.dataset_max_upload_bytes + _OVERHEAD
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type == "multipart/form-data":
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            raise _too_large(settings)
        form = await request.form(max_files=1, max_fields=20)
        fields: dict[str, Any] = {}
        raw: bytes | None = None
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                if key != "file":
                    raise AppError(
                        "The upload part must be named 'file'.",
                        status_code=422,
                        code="validation_error",
                    )
                raw = await _read_upload_file(value, settings)
                fields.setdefault("filename", value.filename)
            elif key == "tags":
                fields["tags"] = [t.strip() for t in value.split(",") if t.strip()]
            elif value != "":
                fields[key] = value
        if "content" in fields:
            raw = str(fields.pop("content")).encode("utf-8")
        try:
            return Upload(model.model_validate(fields), raw)
        except ValidationError as exc:
            raise _invalid(exc) from None

    if content_type not in ("application/json", ""):
        raise AppError(
            "Send application/json or multipart/form-data.",
            code="unsupported_media_type",
            status_code=415,
        )
    body = await _read_body(request, cap, settings)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise AppError("The request body is not valid JSON.", code="bad_request") from None
    if not isinstance(payload, dict):
        raise AppError("The request body must be a JSON object.", code="bad_request")
    try:
        data = model.model_validate(payload)
    except ValidationError as exc:
        raise _invalid(exc) from None
    content = getattr(data, "content", None)
    return Upload(data, content.encode("utf-8") if content is not None else None)


async def _read_upload_file(upload: UploadFile, settings: Settings) -> bytes:
    data = bytearray()
    while chunk := await upload.read(_CHUNK):
        data.extend(chunk)
        if len(data) > settings.dataset_max_upload_bytes:
            raise _too_large(settings)
    return bytes(data)
