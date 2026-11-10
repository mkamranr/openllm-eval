"""Parse Prometheus text exposition and map known GPU / serving metrics to canonical keys.

Supported exporters (whatever subset the scraped page exposes):

* NVIDIA DCGM exporter: ``DCGM_FI_DEV_FB_USED`` / ``_FB_FREE`` / ``_FB_RESERVED`` (MiB),
  ``DCGM_FI_DEV_GPU_UTIL`` (%), ``DCGM_FI_DEV_POWER_USAGE`` (W), ``DCGM_FI_DEV_GPU_TEMP`` (°C).
* nvidia_gpu_exporter (nvidia-smi): ``nvidia_smi_memory_used_bytes``,
  ``nvidia_smi_memory_total_bytes``, ``nvidia_smi_utilization_gpu_ratio``,
  ``nvidia_smi_power_draw_watts``, ``nvidia_smi_temperature_gpu``.
* vLLM ``/metrics``: ``vllm:gpu_cache_usage_perc`` / ``vllm:kv_cache_usage_perc`` (0-1),
  ``vllm:num_requests_running``, ``vllm:num_requests_waiting``.

Nothing is inferred: a key the page does not expose is simply absent from the sample.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)(?:\s+\S+)?$"
)
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')

Sample = tuple[str, dict[str, str], float]

# metric name -> (canonical per-GPU key, multiplier)
_GPU_METRICS: dict[str, tuple[str, float]] = {
    "DCGM_FI_DEV_FB_USED": ("memory_used_mb", 1.0),
    "DCGM_FI_DEV_FB_FREE": ("_memory_free_mb", 1.0),
    "DCGM_FI_DEV_FB_RESERVED": ("_memory_reserved_mb", 1.0),
    "DCGM_FI_DEV_GPU_UTIL": ("utilization_pct", 1.0),
    "DCGM_FI_DEV_POWER_USAGE": ("power_w", 1.0),
    "DCGM_FI_DEV_GPU_TEMP": ("temperature_c", 1.0),
    "nvidia_smi_memory_used_bytes": ("memory_used_mb", 1 / 1048576),
    "nvidia_smi_memory_total_bytes": ("memory_total_mb", 1 / 1048576),
    "nvidia_smi_utilization_gpu_ratio": ("utilization_pct", 100.0),
    "nvidia_smi_power_draw_watts": ("power_w", 1.0),
    "nvidia_smi_temperature_gpu": ("temperature_c", 1.0),
}
_SERVER_METRICS: dict[str, tuple[str, float]] = {
    "vllm:gpu_cache_usage_perc": ("kv_cache_usage_pct", 100.0),
    "vllm:kv_cache_usage_perc": ("kv_cache_usage_pct", 100.0),
    "vllm:num_requests_running": ("requests_running", 1.0),
    "vllm:num_requests_waiting": ("requests_waiting", 1.0),
}
CANONICAL_KEYS = (
    "gpu_memory_used_mb",
    "gpu_memory_total_mb",
    "gpu_utilization_pct",
    "gpu_power_w",
    "gpu_temperature_c",
    "kv_cache_usage_pct",
    "requests_running",
    "requests_waiting",
)


def parse(text: str) -> list[Sample]:
    out: list[Sample] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        labels = {k: v.replace('\\"', '"') for k, v in _LABEL.findall(m.group("labels") or "")}
        out.append((m.group("name"), labels, value))
    return out


def _gpu_id(labels: dict[str, str], fallback: int) -> str:
    for key in ("gpu", "index", "minor_number", "device", "uuid"):
        if labels.get(key):
            return labels[key]
    return str(fallback)


def extract(samples: list[Sample], gpu_indices: list[int] | None = None) -> dict[str, Any]:
    """Canonical values: memory and power summed over the selected GPUs, utilization averaged,
    temperature the maximum. ``gpus`` keeps per-device values."""
    gpus: dict[str, dict[str, float]] = defaultdict(dict)
    order: dict[str, int] = {}
    server: dict[str, float] = {}
    for name, labels, value in samples:
        if name in _GPU_METRICS:
            key, mult = _GPU_METRICS[name]
            gid = _gpu_id(labels, len(order))
            order.setdefault(gid, len(order))
            gpus[gid][key] = value * mult
        elif name in _SERVER_METRICS:
            key, mult = _SERVER_METRICS[name]
            # Several model labels may exist: keep the highest (the busiest engine).
            server[key] = max(server.get(key, -math.inf), value * mult)

    devices = []
    for gid, vals in sorted(gpus.items(), key=lambda kv: order[kv[0]]):
        if "memory_total_mb" not in vals and "memory_used_mb" in vals and "_memory_free_mb" in vals:
            vals["memory_total_mb"] = (
                vals["memory_used_mb"]
                + vals["_memory_free_mb"]
                + vals.get("_memory_reserved_mb", 0)
            )
        device = {"gpu": gid, **{k: round(v, 3) for k, v in vals.items() if not k.startswith("_")}}
        devices.append(device)
    if gpu_indices is not None:
        wanted = {str(i) for i in gpu_indices}
        devices = [d for pos, d in enumerate(devices) if d["gpu"] in wanted or str(pos) in wanted]

    values: dict[str, Any] = {}

    def collect(key: str) -> list[float]:
        return [d[key] for d in devices if key in d]

    if mem := collect("memory_used_mb"):
        values["gpu_memory_used_mb"] = round(sum(mem), 3)
    if total := collect("memory_total_mb"):
        values["gpu_memory_total_mb"] = round(sum(total), 3)
    if util := collect("utilization_pct"):
        values["gpu_utilization_pct"] = round(sum(util) / len(util), 3)
    if power := collect("power_w"):
        values["gpu_power_w"] = round(sum(power), 3)
    if temp := collect("temperature_c"):
        values["gpu_temperature_c"] = max(temp)
    values.update({k: round(v, 3) for k, v in server.items()})
    if devices:
        values["gpus"] = devices
    return values
