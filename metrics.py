"""Metrics store backends for impact estimation.

Exposes a :class:`MetricsStore` abstraction over the RED signals (request **R**ate,
**E**rrors, **D**uration) needed to size an incident's user impact, selected by
:func:`get_metrics_store`:

- :class:`PrometheusMetricsStore` when ``PROMETHEUS_URL`` is set (queries the HTTP API).
- :class:`MockMetricsStore` otherwise — deterministic, realistic incident-window
  data so the estimator runs with no external dependencies.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random

import httpx

from config import get_settings

log = logging.getLogger("incidentlens.metrics")

DEFAULT_STEP_SECONDS = 60

# Conventional PromQL for the RED signals, assuming an `http_requests_total`
# counter and an `http_request_duration_seconds` histogram labeled by `service`.
_PROMQL_REQUEST_RATE = 'sum(rate(http_requests_total{{service="{service}"}}[1m]))'
_PROMQL_ERROR_RATE = (
    'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[1m]))'
    ' / sum(rate(http_requests_total{{service="{service}"}}[1m]))'
)
_PROMQL_LATENCY_P99 = (
    "histogram_quantile(0.99, sum(rate("
    'http_request_duration_seconds_bucket{{service="{service}"}}[1m])) by (le)) * 1000'
)


@dataclass(frozen=True)
class MetricSample:
    """A single timestamped metric value."""

    timestamp: datetime
    value: float


def _timestamps(start: datetime, end: datetime, step_seconds: int) -> list[datetime]:
    """Evenly spaced timestamps across ``[start, end]`` (at least one)."""
    step = timedelta(seconds=step_seconds)
    out: list[datetime] = []
    ts = start
    while ts <= end:
        out.append(ts)
        ts += step
    return out or [start]


class MetricsStore(ABC):
    """Returns RED-signal time series for a service over a window."""

    name: str

    @abstractmethod
    def request_rate(
        self, service: str, start: datetime, end: datetime, step_seconds: int = DEFAULT_STEP_SECONDS
    ) -> list[MetricSample]:
        """Requests per second."""

    @abstractmethod
    def error_rate(
        self, service: str, start: datetime, end: datetime, step_seconds: int = DEFAULT_STEP_SECONDS
    ) -> list[MetricSample]:
        """Fraction of requests failing (0.0–1.0)."""

    @abstractmethod
    def latency_p99_ms(
        self, service: str, start: datetime, end: datetime, step_seconds: int = DEFAULT_STEP_SECONDS
    ) -> list[MetricSample]:
        """99th-percentile latency in milliseconds."""


class MockMetricsStore(MetricsStore):
    """Deterministic synthetic metrics with a realistic incident ramp.

    Values are seeded per (service, metric) so a given service always produces the
    same series — reproducible across processes and unit-testable.
    """

    name = "mock"

    @staticmethod
    def _rng(service: str, metric: str) -> Random:
        seed = int.from_bytes(
            hashlib.blake2b(f"{service}:{metric}".encode(), digest_size=4).digest(), "big"
        )
        return Random(seed)

    @staticmethod
    def _ramp(index: int, count: int) -> float:
        """0.0 at the window start rising to 1.0 at the end."""
        return index / (count - 1) if count > 1 else 1.0

    def request_rate(self, service, start, end, step_seconds=DEFAULT_STEP_SECONDS):
        rng = self._rng(service, "request_rate")
        base = 40 + rng.random() * 160  # 40–200 req/s baseline for this service
        return [
            MetricSample(ts, round(base * (1 + (rng.random() - 0.5) * 0.1), 2))
            for ts in _timestamps(start, end, step_seconds)
        ]

    def error_rate(self, service, start, end, step_seconds=DEFAULT_STEP_SECONDS):
        rng = self._rng(service, "error_rate")
        baseline = 0.002 + rng.random() * 0.004  # 0.2–0.6% healthy baseline
        peak = 0.10 + rng.random() * 0.30  # 10–40% at the height of the incident
        timestamps = _timestamps(start, end, step_seconds)
        samples = []
        for i, ts in enumerate(timestamps):
            value = baseline + (peak - baseline) * self._ramp(i, len(timestamps))
            value *= 1 + (rng.random() - 0.5) * 0.1
            samples.append(MetricSample(ts, round(min(max(value, 0.0), 1.0), 4)))
        return samples

    def latency_p99_ms(self, service, start, end, step_seconds=DEFAULT_STEP_SECONDS):
        rng = self._rng(service, "latency")
        baseline = 80 + rng.random() * 70  # 80–150ms healthy p99
        peak = baseline * (5 + rng.random() * 15)  # 5–20x during the incident
        timestamps = _timestamps(start, end, step_seconds)
        samples = []
        for i, ts in enumerate(timestamps):
            value = baseline + (peak - baseline) * self._ramp(i, len(timestamps))
            value *= 1 + (rng.random() - 0.5) * 0.1
            samples.append(MetricSample(ts, round(value, 1)))
        return samples


class PrometheusMetricsStore(MetricsStore):
    """Reads RED signals from a Prometheus HTTP API via ``query_range``."""

    name = "prometheus"

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _query_range(
        self, promql: str, start: datetime, end: datetime, step_seconds: int
    ) -> list[MetricSample]:
        resp = httpx.get(
            f"{self._base_url}/api/v1/query_range",
            params={
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step_seconds,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
        if not result:
            return []
        # Queries aggregate to a single series; read the first.
        return [
            MetricSample(datetime.fromtimestamp(float(ts), tz=UTC), float(value))
            for ts, value in result[0]["values"]
        ]

    def request_rate(self, service, start, end, step_seconds=DEFAULT_STEP_SECONDS):
        return self._query_range(
            _PROMQL_REQUEST_RATE.format(service=service), start, end, step_seconds
        )

    def error_rate(self, service, start, end, step_seconds=DEFAULT_STEP_SECONDS):
        return self._query_range(
            _PROMQL_ERROR_RATE.format(service=service), start, end, step_seconds
        )

    def latency_p99_ms(self, service, start, end, step_seconds=DEFAULT_STEP_SECONDS):
        return self._query_range(
            _PROMQL_LATENCY_P99.format(service=service), start, end, step_seconds
        )


def get_metrics_store() -> MetricsStore:
    """Return the Prometheus store if configured, else the mock store."""
    prometheus_url = get_settings().prometheus_url
    if prometheus_url:
        log.info("using Prometheus metrics at %s", prometheus_url)
        return PrometheusMetricsStore(prometheus_url)
    return MockMetricsStore()
