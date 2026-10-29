"""
Pillar 2: Agentic AI Workflow, Tool Trajectory & Anti-Hallucination Tests.

Validates:
- Deterministic tool-calling sequence (`fetch_order_details` -> `evaluate_refund_policy` -> `calculate_refund_cap`).
- Business policy compliance (30-day return window, warranty overrides, high-value human escalation).
- Groundedness & faithfulness assertions (`approved_amount <= order_total`).
- Defect detection via negative fault-injection (`hallucination` and `skipped_tool`).
"""

from __future__ import annotations

import pytest

from src.agent_workflow import RefundAgentWorkflow, audit_agent_evaluation
from src.bq_storage import HybridBigQueryStorage, RawTicketRecord


@pytest.fixture()
def workflow_and_storage() -> tuple[RefundAgentWorkflow, HybridBigQueryStorage]:
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()
    return RefundAgentWorkflow(storage=storage), storage


def test_agent_approves_eligible_standard_return_with_full_tool_trajectory(
    workflow_and_storage: tuple[RefundAgentWorkflow, HybridBigQueryStorage],
) -> None:
    workflow, storage = workflow_and_storage
    ticket = RawTicketRecord(
        ticket_id="TCK-TEST-01",
        order_id="ORD-1001",
        customer_id="CUST-01",
        requested_amount=120.00,
        reason="Changed mind within 10 days",
        batch_id="BATCH-TEST",
    )
    result = workflow.evaluate_ticket(ticket, fault_mode="normal")

    assert result.status == "APPROVED"
    assert result.approved_amount == 120.00
    called_tools = [step.tool_name for step in result.tool_trajectory]
    assert called_tools == [
        "fetch_order_details",
        "evaluate_refund_policy",
        "calculate_refund_cap",
    ]
    assert audit_agent_evaluation(result, ground_truth_order_total=120.00) == []


def test_agent_rejects_expired_return_window(
    workflow_and_storage: tuple[RefundAgentWorkflow, HybridBigQueryStorage],
) -> None:
    workflow, _ = workflow_and_storage
    ticket = RawTicketRecord(
        ticket_id="TCK-TEST-02",
        order_id="ORD-1002",  # 45 days since purchase, non-warranty
        customer_id="CUST-02",
        requested_amount=85.50,
        reason="Returning old item",
        batch_id="BATCH-TEST",
    )
    result = workflow.evaluate_ticket(ticket, fault_mode="normal")

    assert result.status == "REJECTED"
    assert result.approved_amount == 0.0
    called_tools = [step.tool_name for step in result.tool_trajectory]
    assert called_tools == ["fetch_order_details", "evaluate_refund_policy"]
    assert audit_agent_evaluation(result, ground_truth_order_total=85.50) == []


def test_agent_approves_defective_warranty_past_30_days(
    workflow_and_storage: tuple[RefundAgentWorkflow, HybridBigQueryStorage],
) -> None:
    workflow, _ = workflow_and_storage
    ticket = RawTicketRecord(
        ticket_id="TCK-TEST-03",
        order_id="ORD-1003",  # 60 days old, but is_defective_warranty=True
        customer_id="CUST-03",
        requested_amount=250.00,
        reason="Defective unit under warranty",
        batch_id="BATCH-TEST",
    )
    result = workflow.evaluate_ticket(ticket, fault_mode="normal")

    assert result.status == "APPROVED"
    assert result.approved_amount == 250.00
    assert audit_agent_evaluation(result, ground_truth_order_total=250.00) == []


def test_agent_escalates_high_value_enterprise_order_to_human(
    workflow_and_storage: tuple[RefundAgentWorkflow, HybridBigQueryStorage],
) -> None:
    workflow, _ = workflow_and_storage
    ticket = RawTicketRecord(
        ticket_id="TCK-TEST-04",
        order_id="ORD-1004",  # $1500.00 order >= $1000 threshold
        customer_id="CUST-04",
        requested_amount=1500.00,
        reason="Enterprise return",
        batch_id="BATCH-TEST",
    )
    result = workflow.evaluate_ticket(ticket, fault_mode="normal")

    assert result.status == "ESCALATED_TO_HUMAN"
    assert result.approved_amount == 0.0
    assert audit_agent_evaluation(result, ground_truth_order_total=1500.00) == []


def test_qa_auditor_catches_llm_financial_hallucination(
    workflow_and_storage: tuple[RefundAgentWorkflow, HybridBigQueryStorage],
) -> None:
    workflow, _ = workflow_and_storage
    ticket = RawTicketRecord(
        ticket_id="TCK-FAULT-HALLUCINATION",
        order_id="ORD-1001",  # Ground-truth order total is $120.00
        customer_id="CUST-01",
        requested_amount=120.00,
        reason="Standard return",
        batch_id="BATCH-FAULT",
    )
    result = workflow.evaluate_ticket(ticket, fault_mode="hallucination")

    defects = audit_agent_evaluation(result, ground_truth_order_total=120.00)
    assert len(defects) == 1
    assert "HALLUCINATION_DEFECT" in defects[0]


def test_qa_auditor_catches_skipped_policy_tool_defect(
    workflow_and_storage: tuple[RefundAgentWorkflow, HybridBigQueryStorage],
) -> None:
    workflow, _ = workflow_and_storage
    ticket = RawTicketRecord(
        ticket_id="TCK-FAULT-SKIPPED-TOOL",
        order_id="ORD-1002",
        customer_id="CUST-02",
        requested_amount=85.50,
        reason="Late return",
        batch_id="BATCH-FAULT",
    )
    result = workflow.evaluate_ticket(ticket, fault_mode="skipped_tool")

    defects = audit_agent_evaluation(result, ground_truth_order_total=85.50)
    assert any("SKIPPED_TOOL_DEFECT" in d for d in defects)

