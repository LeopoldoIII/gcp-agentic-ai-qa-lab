"""
Pillar 3: BigQuery Cross-Dataset Integrity & SQL Reconciliation Tests.

Validates:
- Completeness reconciliation (`raw_tickets` vs `agent_decisions`).
- Uniqueness reconciliation (detection of duplicate `ticket_id` records).
- Financial & policy accuracy across `agent_decisions` and `orders_catalog`.
- Audit trail completeness across `agent_decisions` and `agent_traces`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.bq_storage import AgentDecisionRecord, HybridBigQueryStorage
from src.cloud_run_app import create_app


def test_bigquery_reconciliation_passes_on_healthy_batch() -> None:
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()
    client = TestClient(create_app(storage=storage))

    resp = client.post(
        "/scheduler/trigger",
        headers={"X-CloudScheduler": "true"},
        json={"job_run_id": "JOB-RECON-OK", "batch_id": "BATCH-01", "fault_mode": "normal"},
    )
    assert resp.status_code == 200

    report = storage.run_full_reconciliation_report()
    assert report["is_healthy"] is True
    assert report["unprocessed_tickets"] == []
    assert report["duplicate_decisions"] == []
    assert report["financial_or_policy_discrepancies"] == []
    assert report["decisions_missing_traces"] == []


def test_bigquery_reconciliation_detects_unprocessed_tickets() -> None:
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()

    # Before running the batch job, all 4 raw tickets must be flagged as unprocessed
    unprocessed = storage.find_unprocessed_tickets()
    assert len(unprocessed) == 4
    assert unprocessed[0]["ticket_id"] == "TCK-5001"


def test_bigquery_reconciliation_detects_duplicates_and_missing_traces() -> None:
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()
    client = TestClient(create_app(storage=storage))

    # Run batch with 'data_discrepancy' fault mode
    client.post(
        "/scheduler/trigger",
        headers={"X-CloudScheduler": "true"},
        json={
            "job_run_id": "JOB-RECON-DRIFT",
            "batch_id": "BATCH-01",
            "fault_mode": "data_discrepancy",
        },
    )

    report = storage.run_full_reconciliation_report()
    assert report["is_healthy"] is False
    assert len(report["duplicate_decisions"]) == 4
    assert len(report["decisions_missing_traces"]) == 4


def test_bigquery_reconciliation_detects_over_refund_and_missing_order() -> None:
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()

    # 1. Insert an over-refund decision ($500.00 on $120.00 ORD-1001)
    storage.insert_decision(
        AgentDecisionRecord(
            decision_id="DEC-BAD-01",
            ticket_id="TCK-5001",
            order_id="ORD-1001",
            status="APPROVED",
            approved_amount=500.00,
            confidence=0.50,
            reasoning="Hallucinated amount",
            job_run_id="JOB-BAD",
        )
    )

    # 2. Insert a decision referencing a ghost order not in orders_catalog
    storage.insert_decision(
        AgentDecisionRecord(
            decision_id="DEC-BAD-02",
            ticket_id="TCK-GHOST",
            order_id="ORD-GHOST-999",
            status="APPROVED",
            approved_amount=40.00,
            confidence=0.50,
            reasoning="Ghost order",
            job_run_id="JOB-BAD",
        )
    )

    discrepancies = storage.find_financial_or_policy_discrepancies()
    types_by_ticket = {d["ticket_id"]: d["discrepancy_type"] for d in discrepancies}
    assert types_by_ticket["TCK-5001"] == "OVER_REFUND_EXCEEDS_ORDER_TOTAL"
    assert types_by_ticket["TCK-GHOST"] == "MISSING_ORDER_IN_CATALOG"

