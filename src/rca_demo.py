"""
End-to-End Root Cause Analysis (RCA) & BigQuery Reconciliation Demonstration Script.

Run with:
    .venv/bin/python -m src.rca_demo
"""

from __future__ import annotations

import json
from fastapi.testclient import TestClient

from src.agent_workflow import AgentEvaluationResult, ToolCallStep, audit_agent_evaluation
from src.bq_storage import HybridBigQueryStorage
from src.cloud_run_app import create_app


def _reconstruct_eval_result(decision_dict: dict) -> AgentEvaluationResult:
    steps = [
        ToolCallStep(
            step_number=s["step_number"],
            tool_name=s["tool_name"],
            tool_input=s["tool_input"],
            tool_output=s["tool_output"],
        )
        for s in decision_dict.get("tool_trajectory", [])
    ]
    return AgentEvaluationResult(
        decision_id=decision_dict["decision_id"],
        ticket_id=decision_dict["ticket_id"],
        order_id=decision_dict["order_id"],
        status=decision_dict["status"],
        approved_amount=decision_dict["approved_amount"],
        confidence=decision_dict["confidence"],
        reasoning=decision_dict["reasoning"],
        job_run_id=decision_dict["job_run_id"],
        tool_trajectory=steps,
    )


def run_demo() -> None:
    print("=" * 78)
    print("1. HEALTHY BASELINE RUN (Cloud Scheduler -> Cloud Run Agent -> BigQuery)")
    print("=" * 78)
    healthy_storage = HybridBigQueryStorage(mode="local")
    healthy_storage.seed_default_data()
    client = TestClient(create_app(storage=healthy_storage))

    resp = client.post(
        "/scheduler/trigger",
        headers={"X-CloudScheduler": "true"},
        json={"job_run_id": "JOB-HEALTHY-001", "batch_id": "BATCH-01", "fault_mode": "normal"},
    )
    body = resp.json()
    print(f"HTTP Status: {resp.status_code} | Processed Tickets: {body['processed_count']}")
    recon_healthy = healthy_storage.run_full_reconciliation_report()
    print("BigQuery Reconciliation Report (Healthy):", json.dumps(recon_healthy, indent=2))

    print("\n" + "=" * 78)
    print("2. FAULT-INJECTION & ROOT CAUSE ANALYSIS (RCA) SIMULATION")
    print("=" * 78)
    faulty_storage = HybridBigQueryStorage(mode="local")
    faulty_storage.seed_default_data()
    faulty_client = TestClient(create_app(storage=faulty_storage))

    # Inject Scenario A: Financial Hallucination on TCK-5001
    hallucinated_resp = faulty_client.post(
        "/agent/evaluate",
        json={
            "ticket_id": "TCK-5001",
            "order_id": "ORD-1001",
            "customer_id": "CUST-01",
            "requested_amount": 120.00,
            "reason": "Changed mind",
            "fault_mode": "hallucination",
            "persist": True,
        },
    ).json()
    eval_a = _reconstruct_eval_result(hallucinated_resp)
    defects_a = audit_agent_evaluation(eval_a, ground_truth_order_total=120.00)
    print("\n[Defect Scenario A - LLM Hallucination on TCK-5001]")
    for d in defects_a:
        print(f"  -> Caught by Agent Auditor: {d}")

    # Inject Scenario B: Skipped Policy Tool on TCK-5002
    skipped_resp = faulty_client.post(
        "/agent/evaluate",
        json={
            "ticket_id": "TCK-5002",
            "order_id": "ORD-1002",
            "customer_id": "CUST-02",
            "requested_amount": 85.50,
            "reason": "Late return request",
            "fault_mode": "skipped_tool",
            "persist": True,
        },
    ).json()
    eval_b = _reconstruct_eval_result(skipped_resp)
    defects_b = audit_agent_evaluation(eval_b, ground_truth_order_total=85.50)
    print("\n[Defect Scenario B - Skipped Policy Tool on TCK-5002]")
    for d in defects_b:
        print(f"  -> Caught by Agent Auditor: {d}")

    # Inject Scenario C: BigQuery Data Discrepancy (Duplicate Decision + Missing Traces) on TCK-5003
    faulty_client.post(
        "/agent/evaluate",
        json={
            "ticket_id": "TCK-5003",
            "order_id": "ORD-1003",
            "customer_id": "CUST-03",
            "requested_amount": 250.00,
            "reason": "Warranty claim",
            "fault_mode": "data_discrepancy",
            "persist": True,
        },
    )

    recon_faulty = faulty_storage.run_full_reconciliation_report()
    print("\n[BigQuery Cross-Dataset Reconciliation Report (After Fault Injection)]")
    print(json.dumps(recon_faulty, indent=2))
    print("=" * 78)


if __name__ == "__main__":
    run_demo()

