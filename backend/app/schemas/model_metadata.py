"""Declared model and deployment metadata (spec §5.10, §5.12), and the telemetry source.

Metadata stays a free-form JSON object, but the keys below have a fixed meaning and type, so
quantization variants can be compared field by field. Nothing here is ever inferred: a key the
user did not declare is *unknown*, not guessed from the model name (spec §5.10).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# key: (label, type, min, max). Types: "str", "int", "float".
MODEL_FIELDS: dict[str, tuple[str, str, float | None, float | None]] = {
    "family": ("Model family", "str", None, None),
    "revision": ("Revision", "str", None, None),
    "parameter_count": ("Parameters", "str", None, None),
    "quantization_method": ("Quantization method", "str", None, None),
    "weight_precision": ("Weight precision", "str", None, None),
    "weight_bits": ("Weight bits", "float", 1, 64),
    "activation_precision": ("Activation precision", "str", None, None),
    "kv_cache_dtype": ("KV-cache dtype", "str", None, None),
}
DEPLOYMENT_FIELDS: dict[str, tuple[str, str, float | None, float | None]] = {
    "serving_backend": ("Serving backend", "str", None, None),
    "backend_version": ("Backend version", "str", None, None),
    "gpu_type": ("GPU type", "str", None, None),
    "gpu_count": ("GPU count", "int", 1, 1024),
    "gpu_memory_gb": ("GPU memory (GB, each)", "float", 0.001, 100_000),
    "tensor_parallel_size": ("Tensor parallel size", "int", 1, 1024),
    "pipeline_parallel_size": ("Pipeline parallel size", "int", 1, 1024),
    "max_model_len": ("Max model length (tokens)", "int", 1, 100_000_000),
    "gpu_memory_utilization": ("GPU memory utilization setting", "float", 0.001, 1),
    "max_num_seqs": ("Max concurrent sequences", "int", 1, 1_000_000),
    "max_num_batched_tokens": ("Max batched tokens", "int", 1, 100_000_000),
    "server_args": ("Other server arguments", "str", None, None),
    "driver_version": ("Driver version", "str", None, None),
    "cuda_version": ("CUDA version", "str", None, None),
    "cpu": ("CPU", "str", None, None),
    "ram_gb": ("System RAM (GB)", "float", 0.001, 1_000_000),
}
MAX_TEXT = 2000


def normalize_metadata(
    value: dict[str, Any], fields: dict[str, tuple[str, str, float | None, float | None]]
) -> dict[str, Any]:
    """Type-check the well-known keys; drop empty values (unknown is absent, never ``""``).
    Other keys are kept as given."""
    out: dict[str, Any] = {}
    for key, raw in value.items():
        if key not in fields:
            out[key] = raw
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        label, kind, lo, hi = fields[key]
        if kind == "str":
            if not isinstance(raw, str | int | float) or isinstance(raw, bool):
                raise ValueError(f"{key} ({label}) must be text.")
            text = str(raw).strip()
            if len(text) > MAX_TEXT:
                raise ValueError(f"{key} must be at most {MAX_TEXT} characters.")
            out[key] = text
            continue
        if isinstance(raw, bool):
            raise ValueError(f"{key} ({label}) must be a number.")
        try:
            number = float(raw) if kind == "float" else int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(
                f"{key} ({label}) must be a{'n integer' if kind == 'int' else ' number'}."
            ) from None
        if (lo is not None and number < lo) or (hi is not None and number > hi):
            raise ValueError(f"{key} ({label}) must be between {lo:g} and {hi:g}.")
        # Whole numbers stay integers (4 bits, not 4.0); fractions are kept (e.g. 4.5 bpw).
        out[key] = int(number) if float(number).is_integer() else number
    return out


def variant_fields(
    model: dict[str, Any] | None, deployment: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Every well-known field in a fixed order, with ``None`` (unknown) when not declared."""
    model, deployment = model or {}, deployment or {}
    rows = [
        {"key": k, "label": label, "group": "model", "value": model.get(k)}
        for k, (label, *_rest) in MODEL_FIELDS.items()
    ]
    rows += [
        {"key": k, "label": label, "group": "deployment", "value": deployment.get(k)}
        for k, (label, *_rest) in DEPLOYMENT_FIELDS.items()
    ]
    return rows


class TelemetryConfiguration(BaseModel):
    """A Prometheus text-format endpoint scraped while the model is measured.

    Works with NVIDIA DCGM exporter (``DCGM_FI_DEV_*``), nvidia_gpu_exporter
    (``nvidia_smi_*``), and vLLM's own ``/metrics`` (KV-cache usage). The URL goes through the
    same outbound network policy as model endpoints. Telemetry is best effort: when it fails the
    run continues and the metrics say why they are unavailable.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    source: Literal["prometheus"] = "prometheus"
    url: str = Field(min_length=1, max_length=2048)
    interval_seconds: float = Field(default=2.0, ge=0.5, le=60)
    # Only these GPU indices (the exporter's "gpu" label). None = all GPUs the exporter reports.
    gpu_indices: list[int] | None = Field(default=None, max_length=64)
    timeout_seconds: float = Field(default=2.0, gt=0, le=30)
