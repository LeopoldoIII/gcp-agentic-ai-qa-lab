---
name: ai-testgen-hitl
description: >-
  Generate test cases and test plans using AI, validate generated outputs,
  enforce Human-in-the-Loop (HITL) review gates, and calculate requirement
  coverage. Use this skill when asked to generate new test scenarios or audit
  AI-generated test suites.
---

# AI-Assisted Test Generation & Human-in-the-Loop (HITL) Skill

Use this runbook to generate test cases from business requirements while preventing hallucinated or low-quality AI tests from entering the automated test suite.

## 1. Workflow Architecture

Implemented in [`src/qa_ai_generator.py`](../../src/qa_ai_generator.py):

1. **Requirement Ingestion**: Define structured requirement IDs (`REQ-001` through `REQ-005`).
2. **AI Candidate Generation (`AITestGenerator`)**: Produces candidate test cases covering positive, negative, boundary, and security/injection scenarios.
3. **Automated Pre-Validation (`validate_ai_candidate`)**: Flags common AI test generation defects:
   - Missing or unknown `requirement_id`.
   - Contradictory test oracle (e.g., `expected_status == "REJECTED"` while `expected_max_refund > 0`).
   - Hallucinated preconditions (e.g., referencing non-existent order IDs when testing valid approvals).
   - Weak assertions (missing required tool trajectory checks).
4. **Human-in-the-Loop Review Gate (`HumanReviewGate`)**:
   - Reviewers mark each candidate as `APPROVED`, `EDITED_BY_HUMAN` (fixing defective AI oracles), or `REJECTED` (discarding hallucinated/redundant cases) with mandatory `reviewer_notes`.
5. **Execution & Coverage Gate**:
   - Only `APPROVED` and `EDITED_BY_HUMAN` test cases are executable.
   - Requirement coverage must meet the threshold (`100%` of target requirements covered).

## 2. Running the HITL Test Suite

```bash
.venv/bin/pytest tests/test_04_ai_test_generation_hitl.py -v
```

