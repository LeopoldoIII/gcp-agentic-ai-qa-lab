"""
Pillar 1: Cloud Scheduler & Cloud Run API Contract and Idempotency Tests.

Validates:
- Service readiness (`/health`).
- Cloud Scheduler header enforcement (`X-CloudScheduler: true`).
- Pydantic request validation (HTTP 422 on invalid payloads).
- Batch idempotency (`job_run_id` retry protection so BigQuery rows are never duplicated).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.bq_storage import HybridBigQueryStorage
from src.cloud_run_app import create_app


@pytest.fixture()
def client_and_storage() -> tuple[TestClient, HybridBigQueryStorage]:
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()
    app = create_app(storage=storage)
    return TestClient(app), storage


def test_cloud_run_health_check(
    client_and_storage: tuple[TestClient, HybridBigQueryStorage],
) -> None:
    client, _ = client_and_storage
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "cloud-run-refund-agent"
    assert body["bq_mode"] == "local"


def test_scheduler_trigger_rejects_missing_header(
    client_and_storage: tuple[TestClient, HybridBigQueryStorage],
) -> None:
    client, _ = client_and_storage
    response = client.post(
        "/scheduler/trigger",
        json={"job_run_id": "JOB-UNAUTH-01", "batch_id": "BATCH-01"},
    )
    assert response.status_code == 403
    assert "X-CloudScheduler" in response.json()["detail"]


def test_scheduler_trigger_validates_payload_schema(
    client_and_storage: tuple[TestClient, HybridBigQueryStorage],
) -> None:
    client, _ = client_and_storage
    # job_run_id too short (< 3 chars)
    response = client.post(
        "/scheduler/trigger",
        headers={"X-CloudScheduler": "true"},
        json={"job_run_id": "1", "batch_id": "BATCH-01"},
    )
    assert response.status_code == 422


def test_scheduler_batch_execution_and_retry_idempotency(
    client_and_storage: tuple[TestClient, HybridBigQueryStorage],
) -> None:
    client, storage = client_and_storage

    # First trigger from Cloud Scheduler
    first_resp = client.post(
        "/scheduler/trigger",
        headers={
            "X-CloudScheduler": "true",
            "X-CloudScheduler-JobName": "refund-agent-cron",
        },
        json={"job_run_id": "JOB-20261028-01", "batch_id": "BATCH-01"},
    )
    assert first_resp.status_code == 200
    first_data = first_resp.json()
    assert first_data["idempotent_skip"] is False
    assert first_data["processed_count"] == 4

    # Simulate Cloud Scheduler retry with the exact same job_run_id
    retry_resp = client.post(
        "/scheduler/trigger",
        headers={
            "X-CloudScheduler": "true",
            "X-CloudScheduler-JobName": "refund-agent-cron",
        },
        json={"job_run_id": "JOB-20261028-01", "batch_id": "BATCH-01"},
    )
    assert retry_resp.status_code == 200
    retry_data = retry_resp.json()
    assert retry_data["idempotent_skip"] is True
    assert retry_data["processed_count"] == 0

    # Verify BigQuery has zero duplicate decisions after retry
    assert storage.find_duplicate_decisions() == []

