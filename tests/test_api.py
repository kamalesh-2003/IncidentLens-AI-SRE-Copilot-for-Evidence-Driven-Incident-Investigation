"""Tests for the webhook receiver.

These exercise the API surface with FastAPI's TestClient. The background
incident workflow is patched out so the tests are hermetic and never call the
Anthropic API.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from config import Settings, get_settings
from main import app

FIRING_ALERT = {
    "alert_name": "HighErrorRate",
    "severity": "critical",
    "service": "checkout",
    "started_at": "2026-07-03T14:12:00Z",
}


@pytest.fixture
def client(monkeypatch):
    # Neutralize the background workflow so the API tests stay hermetic and fast.
    monkeypatch.setattr(main, "_run_incident", lambda *a, **k: None)
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "incidentlens"}


def test_firing_alert_accepted(client):
    resp = client.post(
        "/webhook/alert",
        json={
            "alert_name": "HighErrorRate",
            "severity": "critical",
            "service": "checkout",
            "started_at": "2026-07-03T14:12:00Z",
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"received": True, "alert_name": "HighErrorRate"}


def test_resolved_alert_requires_resolved_at(client):
    resp = client.post(
        "/webhook/alert",
        json={
            "alert_name": "HighErrorRate",
            "severity": "high",
            "service": "checkout",
            "status": "resolved",
            "started_at": "2026-07-03T14:12:00Z",
        },
    )
    assert resp.status_code == 422
    assert "resolved_at" in resp.text


def test_resolved_alert_with_timestamp_accepted(client):
    resp = client.post(
        "/webhook/alert",
        json={
            "alert_name": "HighErrorRate",
            "severity": "high",
            "service": "checkout",
            "status": "resolved",
            "started_at": "2026-07-03T14:12:00Z",
            "resolved_at": "2026-07-03T14:40:00Z",
        },
    )
    assert resp.status_code == 202


def test_invalid_severity_rejected(client):
    resp = client.post(
        "/webhook/alert",
        json={
            "alert_name": "HighErrorRate",
            "severity": "catastrophic",
            "service": "checkout",
            "started_at": "2026-07-03T14:12:00Z",
        },
    )
    assert resp.status_code == 422


@pytest.fixture
def authed_client(client):
    # Enable webhook auth by overriding settings with a token.
    app.dependency_overrides[get_settings] = lambda: Settings(webhook_token="s3cret")
    yield client
    app.dependency_overrides.pop(get_settings, None)


def test_webhook_rejects_missing_token(authed_client):
    resp = authed_client.post("/webhook/alert", json=FIRING_ALERT)
    assert resp.status_code == 401


def test_webhook_rejects_wrong_token(authed_client):
    resp = authed_client.post(
        "/webhook/alert", json=FIRING_ALERT, headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401


def test_webhook_accepts_valid_token(authed_client):
    resp = authed_client.post(
        "/webhook/alert", json=FIRING_ALERT, headers={"Authorization": "Bearer s3cret"}
    )
    assert resp.status_code == 202
