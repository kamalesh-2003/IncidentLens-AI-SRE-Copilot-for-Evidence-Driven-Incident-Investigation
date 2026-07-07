"""Tests for the impact estimator and the mock metrics store.

Run against MockMetricsStore, which is deterministic and needs no network.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents.impact_estimator import AVERAGE_REQUESTS_PER_USER, estimate_impact
from metrics import MockMetricsStore

START = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)


@pytest.fixture
def store():
    return MockMetricsStore()


# --- Metrics store -----------------------------------------------------------


def test_series_spans_window_at_step(store):
    samples = store.request_rate("checkout", START, END, step_seconds=60)
    assert len(samples) == 31  # inclusive of both endpoints
    assert samples[0].timestamp == START
    assert samples[-1].timestamp == END
    assert all(s.value > 0 for s in samples)


def test_store_is_deterministic(store):
    a = store.error_rate("checkout", START, END)
    b = store.error_rate("checkout", START, END)
    assert [s.value for s in a] == [s.value for s in b]


def test_error_rate_ramps_up(store):
    samples = store.error_rate("checkout", START, END)
    # The incident ramps, so the end is meaningfully worse than the start.
    assert samples[-1].value > samples[0].value
    assert all(0.0 <= s.value <= 1.0 for s in samples)


# --- Estimator ---------------------------------------------------------------


@pytest.fixture
def estimate(store):
    return estimate_impact(
        service="checkout", started_at=START, resolved_at=END, store=store
    )


def test_estimate_basic_shape(estimate):
    assert estimate.service == "checkout"
    assert estimate.source == "mock"
    assert estimate.duration_seconds == 1800
    assert estimate.total_requests > 0
    assert 0 <= estimate.failed_requests <= estimate.total_requests
    assert 0.0 <= estimate.error_rate_pct <= 100.0
    assert estimate.peak_latency_p99_ms > 0


def test_affected_users_derivation(estimate):
    assert estimate.estimated_affected_users == (
        estimate.failed_requests // AVERAGE_REQUESTS_PER_USER
    )


def test_baseline_below_incident_error_rate(estimate):
    # Baseline (window start) should be lower than the window-wide error rate.
    assert estimate.baseline_error_rate_pct <= estimate.error_rate_pct


def test_estimate_is_deterministic(store):
    a = estimate_impact(service="checkout", started_at=START, resolved_at=END, store=store)
    b = estimate_impact(service="checkout", started_at=START, resolved_at=END, store=store)
    assert a == b


def test_firing_alert_uses_now_as_window_end(store):
    # No resolved_at → window ends "now", so duration is positive.
    estimate = estimate_impact(
        service="checkout",
        started_at=datetime.now(UTC) - timedelta(minutes=10),
        store=store,
    )
    assert estimate.duration_seconds > 0
    assert estimate.total_requests > 0
