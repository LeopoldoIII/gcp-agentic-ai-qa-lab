"""
AI-Assisted Test Case Generation & Human-in-the-Loop (HITL) Validation Module.

Implements the two JD responsibilities:
1. "Use AI tools to generate test cases and test plans, validate generated outputs,
   and ensure adequate test coverage."
2. "Implement human review and validation processes for AI-generated test cases."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ReviewStatus = Literal["PENDING_REVIEW", "APPROVED", "EDITED_BY_HUMAN", "REJECTED"]


@dataclass
class RequirementSpec:
    requirement_id: str
    title: str
    description: str
    priority: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"


@dataclass
class AIGeneratedTestCase:
    test_id: str
    requirement_id: str
    title: str
    ticket_payload: Dict[str, Any]
    expected_status: str  # APPROVED, REJECTED, ESCALATED_TO_HUMAN
    expected_max_refund: float
    expected_required_tools: List[str]
    review_status: ReviewStatus = "PENDING_REVIEW"
    reviewer_id: Optional[str] = None
    reviewer_notes: Optional[str] = None
    validation_warnings: List[str] = field(default_factory=list)


def get_baseline_requirements() -> List[RequirementSpec]:
    """Returns the canonical business requirements for the Agentic AI Refund service."""
    return [
        RequirementSpec(
            requirement_id="REQ-001",
            title="Standard Eligible Return Within 30 Days",
            description="Orders within 30 days of purchase must be APPROVED up to the order total.",
        ),
        RequirementSpec(
            requirement_id="REQ-002",
            title="Expired Return Window Rejection",
            description="Non-warranty orders older than 30 days must be REJECTED with $0.00 refund.",
        ),
        RequirementSpec(
            requirement_id="REQ-003",
            title="Defective Warranty Override",
            description="Defective warranty orders past 30 days must be APPROVED for full order refund.",
        ),
        RequirementSpec(
            requirement_id="REQ-004",
            title="High-Value Enterprise Escalation",
            description="Orders with order_total >= $1000.00 must be ESCALATED_TO_HUMAN with $0.00 auto-refund.",
        ),
        RequirementSpec(
            requirement_id="REQ-005",
            title="Unknown Order ID Rejection",
            description="Tickets referencing a non-existent order_id must be REJECTED after calling fetch_order_details.",
        ),
    ]


class AITestGenerator:
    """
    Simulates an LLM Test Generator that produces candidate test cases from
    RequirementSpec items, including realistic AI generation flaws (such as a
    hallucinated test oracle and a redundant/unmapped test case) that must be
    caught by automated validation and Human-in-the-Loop (HITL) review.
    """

    def generate_candidate_test_suite(
        self, requirements: List[RequirementSpec]
    ) -> List[AIGeneratedTestCase]:
        req_ids = {r.requirement_id for r in requirements}
        candidates: List[AIGeneratedTestCase] = [
            # Valid AI candidate for REQ-001
            AIGeneratedTestCase(
                test_id="AI-TC-001",
                requirement_id="REQ-001",
                title="Approve standard return within 10 days for ORD-1001",
                ticket_payload={
                    "ticket_id": "TCK-HITL-01",
                    "order_id": "ORD-1001",
                    "customer_id": "CUST-01",
                    "requested_amount": 120.00,
                    "reason": "Changed mind within return window",
                },
                expected_status="APPROVED",
                expected_max_refund=120.00,
                expected_required_tools=[
                    "fetch_order_details",
                    "evaluate_refund_policy",
                    "calculate_refund_cap",
                ],
            ),
            # Valid AI candidate for REQ-002
            AIGeneratedTestCase(
                test_id="AI-TC-002",
                requirement_id="REQ-002",
                title="Reject expired 45-day return for ORD-1002",
                ticket_payload={
                    "ticket_id": "TCK-HITL-02",
                    "order_id": "ORD-1002",
                    "customer_id": "CUST-02",
                    "requested_amount": 85.50,
                    "reason": "Late apparel return",
                },
                expected_status="REJECTED",
                expected_max_refund=0.0,
                expected_required_tools=["fetch_order_details", "evaluate_refund_policy"],
            ),
            # Valid AI candidate for REQ-003
            AIGeneratedTestCase(
                test_id="AI-TC-003",
                requirement_id="REQ-003",
                title="Approve defective warranty claim at 60 days for ORD-1003",
                ticket_payload={
                    "ticket_id": "TCK-HITL-03",
                    "order_id": "ORD-1003",
                    "customer_id": "CUST-03",
                    "requested_amount": 250.00,
                    "reason": "Hardware failure under warranty",
                },
                expected_status="APPROVED",
                expected_max_refund=250.00,
                expected_required_tools=[
                    "fetch_order_details",
                    "evaluate_refund_policy",
                    "calculate_refund_cap",
                ],
            ),
            # Flawed AI candidate for REQ-004 (AI hallucinated a $1500 auto-refund on an ESCALATED_TO_HUMAN status)
            AIGeneratedTestCase(
                test_id="AI-TC-004",
                requirement_id="REQ-004",
                title="Escalate $1500 enterprise order ORD-1004 to human supervisor",
                ticket_payload={
                    "ticket_id": "TCK-HITL-04",
                    "order_id": "ORD-1004",
                    "customer_id": "CUST-04",
                    "requested_amount": 1500.00,
                    "reason": "Enterprise hardware return",
                },
                expected_status="ESCALATED_TO_HUMAN",
                expected_max_refund=1500.00,  # DEFECTIVE ORACLE: Escalated tickets must have 0.0 auto-refund!
                expected_required_tools=["fetch_order_details", "evaluate_refund_policy"],
            ),
            # Valid AI candidate for REQ-005
            AIGeneratedTestCase(
                test_id="AI-TC-005",
                requirement_id="REQ-005",
                title="Reject non-existent order ORD-9999 immediately",
                ticket_payload={
                    "ticket_id": "TCK-HITL-05",
                    "order_id": "ORD-9999",
                    "customer_id": "CUST-99",
                    "requested_amount": 50.00,
                    "reason": "Unknown order test",
                },
                expected_status="REJECTED",
                expected_max_refund=0.0,
                expected_required_tools=["fetch_order_details"],
            ),
            # Completely hallucinated AI test case mapping to non-existent REQ-999 with empty tools
            AIGeneratedTestCase(
                test_id="AI-TC-006",
                requirement_id="REQ-999",
                title="Hallucinated crypto loyalty bonus refund test",
                ticket_payload={
                    "ticket_id": "TCK-HITL-06",
                    "order_id": "ORD-1001",
                    "customer_id": "CUST-01",
                    "requested_amount": 9999.00,
                    "reason": "Crypto air-drop refund",
                },
                expected_status="APPROVED",
                expected_max_refund=9999.00,
                expected_required_tools=[],
            ),
        ]

        for tc in candidates:
            tc.validation_warnings = validate_ai_candidate(tc, valid_requirement_ids=req_ids)

        return candidates


def validate_ai_candidate(
    test_case: AIGeneratedTestCase,
    valid_requirement_ids: set[str],
) -> List[str]:
    """
    Automated static validator that flags defective or hallucinated AI-generated
    test cases prior to human review.
    """
    warnings: List[str] = []

    if test_case.requirement_id not in valid_requirement_ids:
        warnings.append(
            f"UNKNOWN_REQUIREMENT: '{test_case.requirement_id}' is not in the requirement specification."
        )

    if not test_case.expected_required_tools:
        warnings.append(
            "MISSING_TOOL_ASSERTIONS: AI test case does not specify any expected_required_tools."
        )

    if (
        test_case.expected_status in ("REJECTED", "ESCALATED_TO_HUMAN")
        and test_case.expected_max_refund > 0.0
    ):
        warnings.append(
            f"CONTRADICTORY_ORACLE: expected_status is '{test_case.expected_status}' "
            f"but expected_max_refund is ${test_case.expected_max_refund:.2f} (must be $0.00)."
        )

    return warnings


class HumanReviewGate:
    """
    Enforces Human-in-the-Loop (HITL) governance over AI-generated test cases.
    """

    def __init__(self, requirements: List[RequirementSpec]) -> None:
        self.requirements = requirements
        self.valid_req_ids = {r.requirement_id for r in requirements}

    def review_test_case(
        self,
        test_case: AIGeneratedTestCase,
        decision: Literal["APPROVED", "EDITED_BY_HUMAN", "REJECTED"],
        reviewer_id: str,
        reviewer_notes: str,
        updated_expected_max_refund: Optional[float] = None,
        updated_expected_tools: Optional[List[str]] = None,
    ) -> AIGeneratedTestCase:
        if decision == "EDITED_BY_HUMAN":
            if updated_expected_max_refund is not None:
                test_case.expected_max_refund = updated_expected_max_refund
            if updated_expected_tools is not None:
                test_case.expected_required_tools = updated_expected_tools
            # Re-run validation after human edits
            test_case.validation_warnings = validate_ai_candidate(
                test_case, self.valid_req_ids
            )

        if decision in ("APPROVED", "EDITED_BY_HUMAN") and test_case.validation_warnings:
            raise ValueError(
                f"Cannot mark test '{test_case.test_id}' as {decision} while unresolved "
                f"validation warnings remain: {test_case.validation_warnings}"
            )

        test_case.review_status = decision
        test_case.reviewer_id = reviewer_id
        test_case.reviewer_notes = reviewer_notes
        return test_case

    def get_executable_suite(
        self, candidates: List[AIGeneratedTestCase]
    ) -> List[AIGeneratedTestCase]:
        """
        Returns only test cases that have been explicitly APPROVED or EDITED_BY_HUMAN.
        Raises a governance error if any candidate is still PENDING_REVIEW.
        """
        pending = [c.test_id for c in candidates if c.review_status == "PENDING_REVIEW"]
        if pending:
            raise RuntimeError(
                f"HITL Governance Violation: Test cases still PENDING_REVIEW: {pending}"
            )
        return [
            c
            for c in candidates
            if c.review_status in ("APPROVED", "EDITED_BY_HUMAN")
        ]

    def calculate_coverage_report(
        self, candidates: List[AIGeneratedTestCase]
    ) -> Dict[str, Any]:
        executable = [
            c
            for c in candidates
            if c.review_status in ("APPROVED", "EDITED_BY_HUMAN")
        ]
        covered_reqs = {c.requirement_id for c in executable}
        missing_reqs = sorted(self.valid_req_ids - covered_reqs)
        coverage_pct = (
            round((len(covered_reqs) / len(self.valid_req_ids)) * 100.0, 2)
            if self.valid_req_ids
            else 100.0
        )
        return {
            "total_requirements": len(self.valid_req_ids),
            "covered_requirements": len(covered_reqs),
            "coverage_percentage": coverage_pct,
            "missing_requirements": missing_reqs,
            "approved_or_edited_count": len(executable),
            "rejected_count": sum(1 for c in candidates if c.review_status == "REJECTED"),
        }

