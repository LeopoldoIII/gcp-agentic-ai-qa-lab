"""
Pillar 4: AI-Assisted Test Generation & Human-in-the-Loop (HITL) Governance Tests.

Validates:
- AI candidate test generation from structured requirements (`REQ-001`..`REQ-005`).
- Automated static pre-validation catching contradictory AI oracles and unknown requirements.
- Human-in-the-Loop (`HumanReviewGate`) blocking unreviewed or flawed test cases.
- 100% requirement coverage verification and live execution of human-approved test cases
  against the Cloud Run Agentic API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.bq_storage import HybridBigQueryStorage
from src.cloud_run_app import create_app
from src.qa_ai_generator import (
    AITestGenerator,
    HumanReviewGate,
    get_baseline_requirements,
)


def test_hitl_blocks_unreviewed_ai_generated_test_cases() -> None:
    requirements = get_baseline_requirements()
    generator = AITestGenerator()
    gate = HumanReviewGate(requirements=requirements)

    candidates = generator.generate_candidate_test_suite(requirements)
    assert len(candidates) == 6

    # Attempting to execute before human review must raise a governance error
    with pytest.raises(RuntimeError, match="HITL Governance Violation"):
        gate.get_executable_suite(candidates)


def test_hitl_prevents_approving_flawed_ai_test_without_human_fix() -> None:
    requirements = get_baseline_requirements()
    generator = AITestGenerator()
    gate = HumanReviewGate(requirements=requirements)

    candidates = generator.generate_candidate_test_suite(requirements)
    flawed_tc = next(c for c in candidates if c.test_id == "AI-TC-004")
    assert any("CONTRADICTORY_ORACLE" in w for w in flawed_tc.validation_warnings)

    # Human cannot blindly approve a test case that still has validation warnings
    with pytest.raises(ValueError, match="unresolved validation warnings"):
        gate.review_test_case(
            flawed_tc,
            decision="APPROVED",
            reviewer_id="qa-lead@company.com",
            reviewer_notes="Blind approval attempt",
        )


def test_full_ai_test_generation_hitl_review_and_live_execution() -> None:
    requirements = get_baseline_requirements()
    generator = AITestGenerator()
    gate = HumanReviewGate(requirements=requirements)
    candidates = generator.generate_candidate_test_suite(requirements)

    by_id = {c.test_id: c for c in candidates}

    # 1. Approve valid AI-generated test cases
    for valid_id in ("AI-TC-001", "AI-TC-002", "AI-TC-003", "AI-TC-005"):
        gate.review_test_case(
            by_id[valid_id],
            decision="APPROVED",
            reviewer_id="qa-lead@company.com",
            reviewer_notes="Verified preconditions, tool trajectory, and expected refund cap.",
        )

    # 2. Edit AI-TC-004 (fix AI's contradictory $1500 auto-refund oracle to $0.0 for ESCALATED_TO_HUMAN)
    gate.review_test_case(
        by_id["AI-TC-004"],
        decision="EDITED_BY_HUMAN",
        reviewer_id="qa-lead@company.com",
        reviewer_notes="Corrected expected_max_refund from 1500.0 to 0.0 for human-escalated tickets.",
        updated_expected_max_refund=0.0,
    )

    # 3. Reject hallucinated AI-TC-006 (maps to non-existent REQ-999)
    gate.review_test_case(
        by_id["AI-TC-006"],
        decision="REJECTED",
        reviewer_id="qa-lead@company.com",
        reviewer_notes="Rejected hallucinated crypto loyalty requirement REQ-999.",
    )

    # 4. Verify 100% requirement coverage across REQ-001..REQ-005
    coverage = gate.calculate_coverage_report(candidates)
    assert coverage["coverage_percentage"] == 100.0
    assert coverage["missing_requirements"] == []
    assert coverage["approved_or_edited_count"] == 5
    assert coverage["rejected_count"] == 1

    # 5. Execute the human-approved test suite against the Cloud Run Agent API
    executable_suite = gate.get_executable_suite(candidates)
    storage = HybridBigQueryStorage(mode="local")
    storage.seed_default_data()
    client = TestClient(create_app(storage=storage))

    for tc in executable_suite:
        resp = client.post("/agent/evaluate", json=tc.ticket_payload)
        assert resp.status_code == 200, f"Failed HTTP call on {tc.test_id}"
        body = resp.json()
        assert body["status"] == tc.expected_status, f"Status mismatch on {tc.test_id}"
        assert body["approved_amount"] <= tc.expected_max_refund, (
            f"Refund cap exceeded on {tc.test_id}"
        )
        called_tools = [s["tool_name"] for s in body["tool_trajectory"]]
        assert called_tools == tc.expected_required_tools, (
            f"Tool trajectory mismatch on {tc.test_id}"
        )

