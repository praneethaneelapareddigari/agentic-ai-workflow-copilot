"""
Lightweight observability layer: structured JSON logging + in-memory metrics
+ per-request/workflow trace IDs. Designed to be swapped for OpenTelemetry /
Prometheus exporters in production without touching call sites (everything
funnels through `get_logger()` and `METRICS`).
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default="-"
)


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:16]
    trace_id_var.set(tid)
    return tid


def current_trace_id() -> str:
    return trace_id_var.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": current_trace_id(),
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)  # type: ignore[attr-defined]
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: str, message: str, **fields: Any) -> None:
    record_level = getattr(logging, level.upper(), logging.INFO)
    logger.log(record_level, message, extra={"extra_fields": fields})


@dataclass
class _Metrics:
    """Minimal counters/timers/histograms, thread-safe, process-local.

    In production this would be replaced by a Prometheus client or an
    OpenTelemetry MeterProvider; the interface (`incr`, `observe`, `snapshot`)
    is kept intentionally small so that swap is a one-file change.
    """

    _counters: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _histograms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _lock: Lock = field(default_factory=Lock)

    def incr(self, name: str, value: float = 1.0, **tags: Any) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, value: float, **tags: Any) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._histograms[key].append(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            hist_summary = {}
            for k, values in self._histograms.items():
                if not values:
                    continue
                sorted_vals = sorted(values)
                hist_summary[k] = {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values), 2),
                    "p95_ms": round(
                        sorted_vals[int(len(sorted_vals) * 0.95) - 1], 2
                    ),
                    "max_ms": round(max(values), 2),
                }
            return {
                "counters": dict(self._counters),
                "histograms": hist_summary,
            }

    @staticmethod
    def _key(name: str, tags: dict[str, Any]) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


METRICS = _Metrics()


class timed:
    """Context manager / decorator that records elapsed ms into METRICS."""

    def __init__(self, metric_name: str, **tags: Any):
        self.metric_name = metric_name
        self.tags = tags
        self._start: Optional[float] = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        METRICS.observe(self.metric_name, elapsed_ms, **self.tags)
        status = "error" if exc_type else "ok"
        METRICS.incr(f"{self.metric_name}.count", status=status, **self.tags)
        return False
