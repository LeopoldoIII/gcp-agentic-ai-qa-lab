"""
Agentic AI Workflow for Order Refund & Triage on Cloud Run.

Simulates an LLM-powered agent equipped with deterministic tools:
1. `fetch_order_details`: Queries BigQuery `orders_catalog` for ground-truth order metadata.
2. `evaluate_refund_policy`: Checks return window (30 days), warranty overrides, and restocking fees.
3. `calculate_refund_cap`: Computes the exact maximum allowed refund amount.

Supports controlled `fault_mode` injection for QA validation and Root Cause Analysis (RCA):
- `normal`: Follows instructions, executes all required tools, respects refund caps, writes clean data.
- `hallucination`: Hallucinates an approved refund ($500.00) higher than the actual order total.
- `skipped_tool`: Approves a refund without invoking `evaluate_refund_policy`.
- `data_discrepancy`: Writes duplicate decision records and omits audit traces in BigQuery.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from src.bq_storage import (
    AgentDecisionRecord,
    AgentTraceRecord,
    HybridBigQueryStorage,
    RawTicketRecord,
)


@dataclass
class ToolCallStep:
    step_number: int
    tool_name: str
    tool_input: Dict[str, Any]
    tool_output: Dict[str, Any]


@dataclass
class AgentEvaluationResult:
    decision_id: str
    ticket_id: str
    order_id: str
    status: str  # APPROVED, REJECTED, ESCALATED_TO_HUMAN
    approved_amount: float
    confidence: float
    reasoning: str
    job_run_id: str
    tool_trajectory: List[ToolCallStep] = field(default_factory=list)


class RefundAgentWorkflow:
    """
    Agentic AI orchestrator that evaluates refund tickets using deterministic tools
    and persists both decisions and tool traces to BigQuery.
    """

    HIGH_VALUE_ESCALATION_THRESHOLD = 1000.00
    STANDARD_RETURN_WINDOW_DAYS = 30

    def __init__(self, storage: HybridBigQueryStorage) -> None:
        self.storage = storage

    # -------------------------------------------------------------------------
    # Deterministic Agent Tools
    # -------------------------------------------------------------------------

    def tool_fetch_order_details(self, order_id: str) -> Dict[str, Any]:
        order = self.storage.get_order(order_id)
        if not order:
            return {"found": False, "order_id": order_id}
        return {
            "found": True,
            "order_id": order.order_id,
            "customer_id": order.customer_id,
            "order_total": order.order_total,
            "days_since_purchase": order.days_since_purchase,
            "item_category": order.item_category,
            "is_defective_warranty": order.is_defective_warranty,
        }

    def tool_evaluate_refund_policy(
        self,
        days_since_purchase: int,
        item_category: str,
        is_defective_warranty: bool,
        order_total: float,
    ) -> Dict[str, Any]:
        if order_total >= self.HIGH_VALUE_ESCALATION_THRESHOLD:
            return {
                "policy_action": "ESCALATE_HIGH_VALUE",
                "eligible": False,
                "restocking_fee_pct": 0.0,
                "policy_rule": "Orders >= $1000 require human supervisor approval.",
            }

        if is_defective_warranty:
            return {
                "policy_action": "ALLOW_WARRANTY_OVERRIDE",
                "eligible": True,
                "restocking_fee_pct": 0.0,
                "policy_rule": "Defective warranty claims qualify for 100% refund up to 90 days.",
            }

        if days_since_purchase > self.STANDARD_RETURN_WINDOW_DAYS:
            return {
                "policy_action": "REJECT_EXPIRED_WINDOW",
                "eligible": False,
                "restocking_fee_pct": 0.0,
                "policy_rule": f"Standard return window ({self.STANDARD_RETURN_WINDOW_DAYS} days) exceeded.",
            }

        # Standard eligible return within 30 days
        restocking_fee_pct = 0.10 if item_category == "OPEN_BOX_HARDWARE" else 0.0
        return {
            "policy_action": "APPROVE_STANDARD",
            "eligible": True,
            "restocking_fee_pct": restocking_fee_pct,
            "policy_rule": "Within 30-day standard return window.",
        }

    def tool_calculate_refund_cap(
        self,
        order_total: float,
        requested_amount: float,
        restocking_fee_pct: float,
    ) -> Dict[str, Any]:
        max_refundable = round(order_total * (1.0 - restocking_fee_pct), 2)
        recommended_refund = round(min(requested_amount, max_refundable), 2)
        return {
            "order_total": order_total,
            "max_refundable": max_refundable,
            "recommended_refund": max(0.0, recommended_refund),
        }

    # -------------------------------------------------------------------------
    # Agentic Execution Loop
    # -------------------------------------------------------------------------

    def evaluate_ticket(
        self,
        ticket: RawTicketRecord,
        job_run_id: str = "MANUAL-RUN",
        fault_mode: str = "normal",
        persist: bool = True,
    ) -> AgentEvaluationResult:
        """
        Executes the Agentic AI loop for a single ticket.
        `fault_mode` can be: 'normal', 'hallucination', 'skipped_tool', 'data_discrepancy'.
        """
        trajectory: List[ToolCallStep] = []
        decision_id = f"DEC-{uuid.uuid4().hex[:8].upper()}"

        # Step 1: Always invoke `fetch_order_details`
        step1_in = {"order_id": ticket.order_id}
        step1_out = self.tool_fetch_order_details(ticket.order_id)
        trajectory.append(
            ToolCallStep(
                step_number=1,
                tool_name="fetch_order_details",
                tool_input=step1_in,
                tool_output=step1_out,
            )
        )

        if not step1_out.get("found"):
            result = AgentEvaluationResult(
                decision_id=decision_id,
                ticket_id=ticket.ticket_id,
                order_id=ticket.order_id,
                status="REJECTED",
                approved_amount=0.0,
                confidence=0.99,
                reasoning=f"Order '{ticket.order_id}' does not exist in orders_catalog.",
                job_run_id=job_run_id,
                tool_trajectory=trajectory,
            )
            if persist:
                self._persist_result(result, fault_mode=fault_mode)
            return result

        order_total = float(step1_out["order_total"])

        # Fault Mode: 'skipped_tool' -> Agent skips `evaluate_refund_policy` and approves directly
        if fault_mode == "skipped_tool":
            result = AgentEvaluationResult(
                decision_id=decision_id,
                ticket_id=ticket.ticket_id,
                order_id=ticket.order_id,
                status="APPROVED",
                approved_amount=min(ticket.requested_amount, order_total),
                confidence=0.62,
                reasoning="Approved refund directly after checking order existence (skipped policy check).",
                job_run_id=job_run_id,
                tool_trajectory=trajectory,
            )
            if persist:
                self._persist_result(result, fault_mode=fault_mode)
            return result

        # Step 2: Invoke `evaluate_refund_policy`
        step2_in = {
            "days_since_purchase": step1_out["days_since_purchase"],
            "item_category": step1_out["item_category"],
            "is_defective_warranty": step1_out["is_defective_warranty"],
            "order_total": order_total,
        }
        step2_out = self.tool_evaluate_refund_policy(**step2_in)
        trajectory.append(
            ToolCallStep(
                step_number=2,
                tool_name="evaluate_refund_policy",
                tool_input=step2_in,
                tool_output=step2_out,
            )
        )

        policy_action = step2_out["policy_action"]

        if policy_action == "ESCALATE_HIGH_VALUE":
            result = AgentEvaluationResult(
                decision_id=decision_id,
                ticket_id=ticket.ticket_id,
                order_id=ticket.order_id,
                status="ESCALATED_TO_HUMAN",
                approved_amount=0.0,
                confidence=0.95,
                reasoning=step2_out["policy_rule"],
                job_run_id=job_run_id,
                tool_trajectory=trajectory,
            )
            if persist:
                self._persist_result(result, fault_mode=fault_mode)
            return result

        if not step2_out["eligible"]:
            result = AgentEvaluationResult(
                decision_id=decision_id,
                ticket_id=ticket.ticket_id,
                order_id=ticket.order_id,
                status="REJECTED",
                approved_amount=0.0,
                confidence=0.98,
                reasoning=step2_out["policy_rule"],
                job_run_id=job_run_id,
                tool_trajectory=trajectory,
            )
            if persist:
                self._persist_result(result, fault_mode=fault_mode)
            return result

        # Step 3: Invoke `calculate_refund_cap` for eligible orders
        step3_in = {
            "order_total": order_total,
            "requested_amount": ticket.requested_amount,
            "restocking_fee_pct": step2_out["restocking_fee_pct"],
        }
        step3_out = self.tool_calculate_refund_cap(**step3_in)
        trajectory.append(
            ToolCallStep(
                step_number=3,
                tool_name="calculate_refund_cap",
                tool_input=step3_in,
                tool_output=step3_out,
            )
        )

        # Fault Mode: 'hallucination' -> Agent ignores the calculated cap and hallucinates $500.00
        if fault_mode == "hallucination":
            hallucinated_amount = round(order_total + 380.00, 2)
            result = AgentEvaluationResult(
                decision_id=decision_id,
                ticket_id=ticket.ticket_id,
                order_id=ticket.order_id,
                status="APPROVED",
                approved_amount=hallucinated_amount,
                confidence=0.51,
                reasoning=(
                    f"Hallucinated compensation bonus included; approved ${hallucinated_amount} "
                    f"despite order total of ${order_total}."
                ),
                job_run_id=job_run_id,
                tool_trajectory=trajectory,
            )
            if persist:
                self._persist_result(result, fault_mode=fault_mode)
            return result

        # Normal deterministic grounded output
        approved_amount = float(step3_out["recommended_refund"])
        result = AgentEvaluationResult(
            decision_id=decision_id,
            ticket_id=ticket.ticket_id,
            order_id=ticket.order_id,
            status="APPROVED",
            approved_amount=approved_amount,
            confidence=0.96,
            reasoning=f"{step2_out['policy_rule']} Approved ${approved_amount:.2f}.",
            job_run_id=job_run_id,
            tool_trajectory=trajectory,
        )
        if persist:
            self._persist_result(result, fault_mode=fault_mode)
        return result

    def _persist_result(
        self, result: AgentEvaluationResult, fault_mode: str = "normal"
    ) -> None:
        decision_record = AgentDecisionRecord(
            decision_id=result.decision_id,
            ticket_id=result.ticket_id,
            order_id=result.order_id,
            status=result.status,
            approved_amount=result.approved_amount,
            confidence=result.confidence,
            reasoning=result.reasoning,
            job_run_id=result.job_run_id,
        )
        self.storage.insert_decision(decision_record)

        if fault_mode == "data_discrepancy":
            # Simulate a pipeline bug: insert a duplicate row and skip writing agent_traces
            self.storage.insert_decision(decision_record)
            return

        trace_records = [
            AgentTraceRecord(
                trace_id=f"{result.ticket_id}-STEP-{step.step_number}",
                ticket_id=result.ticket_id,
                step_number=step.step_number,
                tool_name=step.tool_name,
                tool_input=json.dumps(step.tool_input, sort_keys=True),
                tool_output=json.dumps(step.tool_output, sort_keys=True),
            )
            for step in result.tool_trajectory
        ]
        self.storage.insert_traces(trace_records)


# -------------------------------------------------------------------------
# QA Trajectory & Groundedness Auditor Utilities
# -------------------------------------------------------------------------


def audit_agent_evaluation(
    result: AgentEvaluationResult,
    ground_truth_order_total: Optional[float],
) -> List[str]:
    """
    Audits an AgentEvaluationResult for tool-calling trajectory completeness,
    output groundedness, and status/amount consistency. Returns a list of defects.
    """
    defects: List[str] = []
    called_tools = [step.tool_name for step in result.tool_trajectory]

    # 1. Verify mandatory first tool call
    if not called_tools or called_tools[0] != "fetch_order_details":
        defects.append("TRAJECTORY_DEFECT: Missing initial 'fetch_order_details' call.")

    # 2. Verify policy evaluation before any approval
    if result.status == "APPROVED":
        if "evaluate_refund_policy" not in called_tools:
            defects.append(
                "SKIPPED_TOOL_DEFECT: Ticket was APPROVED without calling 'evaluate_refund_policy'."
            )
        if "calculate_refund_cap" not in called_tools:
            defects.append(
                "SKIPPED_TOOL_DEFECT: Ticket was APPROVED without calling 'calculate_refund_cap'."
            )

    # 3. Verify groundedness against ground-truth order total
    if ground_truth_order_total is not None:
        if result.approved_amount > ground_truth_order_total:
            defects.append(
                f"HALLUCINATION_DEFECT: Approved amount (${result.approved_amount:.2f}) "
                f"exceeds ground-truth order total (${ground_truth_order_total:.2f})."
            )

    # 4. Verify status vs amount consistency
    if result.status in ("REJECTED", "ESCALATED_TO_HUMAN") and result.approved_amount > 0:
        defects.append(
            f"CONSISTENCY_DEFECT: Non-zero approved amount (${result.approved_amount:.2f}) "
            f"for status '{result.status}'."
        )

    return defects

