# Executive Interview Guide: QA Engineer – AI with GCP

This guide provides a rapid-fire summary of the **12 highest-signal technical interview questions**, what hiring managers look for, and concrete code examples mapped directly to this repository.

---

## Pillar 1: Testing Agentic AI Workflows

### Q1. How does testing an Agentic AI workflow differ from testing a traditional deterministic microservice?
- **What the interviewer wants to hear**:
  - Traditional APIs map `Input X -> Deterministic Output Y`. Agentic AI workflows involve dynamic reasoning, multi-step **tool selection (`tool_trajectory`)**, and probabilistic text/JSON generation.
  - Therefore, QA must validate **three layers**:
    1. **Deterministic Tool Layer**: Unit-test each tool (`fetch_order_details`, `evaluate_refund_policy`, `calculate_refund_cap`) in isolation.
    2. **Agent Trajectory (Process Validation)**: Verify *which* tools the agent invoked, in *what order*, and with *what parameters* (e.g., never approving a refund before calling `evaluate_refund_policy`).
    3. **Output Groundedness & Guardrails (Outcome Validation)**: Verify schema adherence, business boundaries (`approved_amount <= order_total`), and consistency (`status == "REJECTED"` implies `approved_amount == 0.0`).
- **Repo Reference**: [`src/agent_workflow.py`](../src/agent_workflow.py) (`audit_agent_evaluation`) and [`tests/test_02_agentic_workflow.py`](../tests/test_02_agentic_workflow.py).

### Q2. How do you detect and prevent hallucinations in AI-generated outputs?
- **What the interviewer wants to hear**:
  - **Groundedness / Faithfulness checks**: Cross-reference every entity and number in the agent's final output against the ground-truth retrieval source (`orders_catalog` in BigQuery and the tool outputs in `agent_traces`).
  - **Deterministic Post-Processing Guardrails**: Enforce strict Pydantic schemas and hard numerical caps (`approved_amount <= min(requested_amount, order_total)`).
  - **Fault-Injection Testing**: Intentionally inject hallucinated responses (`fault_mode="hallucination"`) in CI to prove your QA assertions and reconciliation queries immediately flag the defect.

### Q3. How do you handle non-determinism (flakiness) when running automated E2E tests against LLM agents?
- **What the interviewer wants to hear**:
  - Pin `temperature=0.0` (or `seed` parameters) and model version IDs in test environments.
  - Evaluate **invariant properties** rather than exact free-text wording (e.g., assert `status == "APPROVED"`, `approved_amount == 120.0`, and `called_tools == [...]`, while allowing `reasoning` text to vary semantically).
  - Separate **fast deterministic CI runs** (using recorded tool/LLM fixtures or our local simulator) from **scheduled live-LLM evaluation benchmarks**.

---

## Pillar 2: GCP Serverless Architecture (Cloud Scheduler, Cloud Run, BigQuery)

### Q4. How do you test a pipeline triggered by Cloud Scheduler hitting a Cloud Run service?
- **What the interviewer wants to hear**:
  - **Authentication & Trigger Contract**: Verify that the Cloud Run endpoint validates OIDC bearer tokens or `X-CloudScheduler: true` / `X-CloudScheduler-JobName` headers and rejects unauthorized calls with `HTTP 401/403`.
  - **Idempotency on Retries**: Cloud Scheduler automatically retries on timeouts or `5xx` responses. If the job is not idempotent by `job_run_id` or `ticket_id`, retries will create duplicate records in BigQuery.
  - **Timeout & Cold-Start Handling**: Ensure batch sizes fit comfortably within the Cloud Run request timeout (default 5 min, max 60 min) and verify graceful partial-batch failure handling.
- **Repo Reference**: [`tests/test_01_api_and_scheduler.py`](../tests/test_01_api_and_scheduler.py).

### Q5. What specific failure modes occur between Cloud Run and BigQuery in production?
- **What the interviewer wants to hear**:
  - **Schema mismatches / Streaming insert errors**: `insert_rows_json` returns row-level errors without raising an HTTP exception unless explicitly checked (`if errors: ...`).
  - **Duplicate rows from at-least-once delivery**: Streaming inserts or retried Cloud Run jobs appending duplicate `ticket_id` rows into `agent_decisions`.
  - **Partial writes**: Writing the decision to `agent_decisions` but failing before persisting the audit trail to `agent_traces`.

### Q6. How do you structure local and CI testing for GCP services without incurring high cloud costs?
- **What the interviewer wants to hear**:
  - Use a **Hybrid Storage Adapter pattern** (`BQ_MODE=local` vs `BQ_MODE=floci` vs `BQ_MODE=gcp`).
  - Fast PR checks run against an in-memory SQLite/DuckDB analytical engine in milliseconds; pre-production integration tests run against an emulator or ephemeral BigQuery dataset with TTL expiration.

---

## Pillar 3: Data Integrity, Reconciliation & Root Cause Analysis (RCA)

### Q7. How do you validate data consistency across multiple BigQuery datasets after an agent batch run?
- **What the interviewer wants to hear**:
  - Write automated **SQL Reconciliation Queries** across four dimensions:
    1. **Completeness (`LEFT JOIN ... WHERE d.ticket_id IS NULL`)**: Ensure 100% of `raw_tickets` in the batch produced a decision in `agent_decisions`.
    2. **Uniqueness (`GROUP BY ticket_id HAVING COUNT(*) > 1`)**: Detect duplicate processing.
    3. **Accuracy / Business Rules (`JOIN orders_catalog`)**: Verify `d.approved_amount <= o.order_total` and `d.order_id` exists in `orders_catalog`.
    4. **Auditability (`LEFT JOIN agent_traces`)**: Confirm every decision has its corresponding tool trajectory rows.
- **Repo Reference**: [`src/bq_storage.py`](../src/bq_storage.py) and [`tests/test_03_bigquery_reconciliation.py`](../tests/test_03_bigquery_reconciliation.py).

### Q8. Walk me through how you perform Root Cause Analysis (RCA) when a BigQuery reconciliation check fails.
- **What the interviewer wants to hear**:
  1. **Isolate the discrepant record**: Identify the `ticket_id`, `decision_id`, and `job_run_id` from the failing SQL reconciliation query.
  2. **Inspect `agent_traces` by `ticket_id`**: Check the exact sequence of tools called (`step_number`, `tool_name`, `tool_input`, `tool_output`).
  3. **Classify the root cause layer**:
     - *Missing trace rows + duplicate decisions*: Persistence/idempotency bug in the Cloud Run data adapter (`data_discrepancy`).
     - *`evaluate_refund_policy` missing from `agent_traces`*: Agent orchestration / prompt adherence failure (`skipped_tool`).
     - *`calculate_refund_cap` returned `$120.00` in `agent_traces`, but `agent_decisions.approved_amount` is `$500.00`*: LLM output hallucination (`hallucination`) requiring a post-processing guardrail.
- **Repo Reference**: Run `.venv/bin/python -m src.rca_demo` ([`src/rca_demo.py`](../src/rca_demo.py)).

---

## Pillar 4: AI-Assisted Test Generation & Human-in-the-Loop (HITL)

### Q9. How do you use AI tools to generate test cases and test plans effectively?
- **What the interviewer wants to hear**:
  - Provide the LLM with structured context: **business requirements (`REQ-001`..`REQ-005`)**, **API Pydantic schemas**, **tool definitions**, and **BigQuery table schemas**.
  - Prompt the AI to generate a matrix covering:
    - Positive happy paths
    - Boundary values (e.g., exactly 30 days vs. 31 days; `$999.99` vs. `$1000.00`)
    - Negative/error scenarios (non-existent `order_id`, malformed headers)
    - Adversarial / prompt-injection inputs

### Q10. Why is Human-in-the-Loop (HITL) validation mandatory for AI-generated test cases, and how do you implement it?
- **What the interviewer wants to hear**:
  - **The Risk**: If AI generates both the code/behavior and the test oracle without human review, it frequently produces **tautological assertions** or **contradictory test oracles** (e.g., expecting `$1500.00` auto-refund on an `ESCALATED_TO_HUMAN` status, or mapping a test to a hallucinated requirement `REQ-999`).
  - **The Implementation (`HumanReviewGate`)**:
    1. Run an automated static pre-validator (`validate_ai_candidate`) to flag missing requirement mappings, empty tool assertions, or contradictory expected values.
    2. Require a human QA engineer to explicitly transition each test case from `PENDING_REVIEW` to `APPROVED`, `EDITED_BY_HUMAN`, or `REJECTED` with `reviewer_id` and `reviewer_notes`.
    3. Block CI test execution if any candidate remains `PENDING_REVIEW` or if requirement coverage falls below threshold.
- **Repo Reference**: [`src/qa_ai_generator.py`](../src/qa_ai_generator.py) and [`tests/test_04_ai_test_generation_hitl.py`](../tests/test_04_ai_test_generation_hitl.py).

---

## Rapid-Fire Behavioral & Scenario Questions

### Q11. "Tell me about a defect you found in an AI-driven workflow that traditional API testing would have missed."
- **STAR Answer Template**:
  - **Situation**: Our refund processing agent on Cloud Run returned `HTTP 200 OK` and a valid JSON schema for every ticket in staging.
  - **Task**: Validate not just the HTTP contract, but the agent's decision accuracy and tool usage before production launch.
  - **Action**: I built a trajectory auditor (`audit_agent_evaluation`) and BigQuery SQL reconciliation suite joining `agent_decisions` with `agent_traces` and `orders_catalog`.
  - **Result**: Discovered that on expired warranty claims, the agent occasionally skipped `evaluate_refund_policy` (`skipped_tool` defect) or approved amounts exceeding the original order total (`hallucination`). Adding trajectory assertions and a deterministic post-LLM cap check eliminated 100% of over-refund defects.

### Q12. "How do you measure 'adequate test coverage' for an Agentic AI application?"
- **What the interviewer wants to hear**:
  - Code line coverage alone is insufficient for AI agents. Combine **4 coverage metrics**:
    1. **Requirement Coverage**: % of business rules (`REQ-001`..`REQ-005`) covered by human-approved test cases.
    2. **Tool & Branch Trajectory Coverage**: Every tool (`fetch_order_details`, `evaluate_refund_policy`, `calculate_refund_cap`) and decision status (`APPROVED`, `REJECTED`, `ESCALATED_TO_HUMAN`) exercised.
    3. **Guardrail / Fault-Mode Coverage**: Every failure mode (`hallucination`, `skipped_tool`, `data_discrepancy`, `idempotent_retry`) tested via negative tests.
    4. **Dataset Reconciliation Coverage**: 100% of output tables validated for completeness, uniqueness, and referential integrity in BigQuery.

