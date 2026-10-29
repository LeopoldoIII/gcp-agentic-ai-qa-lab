# Step-by-Step Practical Tutorial: QA Engineering for Agentic AI on GCP

This tutorial walks you through every component of the repository step by step so you can run, modify, break, and explain the entire pipeline during a technical interview.

---

## Step 1: Understand the Business Scenario & GCP Architecture

We are testing an **Automated E-Commerce Order Refund & Triage Pipeline** deployed on Google Cloud Platform:

1. **Cloud Scheduler** sends an HTTP `POST /scheduler/trigger` request with header `X-CloudScheduler: true` and a JSON payload containing `job_run_id` and `batch_id`.
2. **Cloud Run ([`src/cloud_run_app.py`](../src/cloud_run_app.py))** receives the request, checks whether `job_run_id` was already processed (idempotency check), and fetches unprocessed tickets from BigQuery (`raw_tickets`).
3. **Agentic AI Workflow ([`src/agent_workflow.py`](../src/agent_workflow.py))** processes each ticket by orchestrating three deterministic tools:
   - `fetch_order_details(order_id)`: Queries `orders_catalog` in BigQuery.
   - `evaluate_refund_policy(...)`: Evaluates the 30-day return window, defective warranty overrides, and the `$1000` human-escalation threshold.
   - `calculate_refund_cap(...)`: Calculates the maximum allowed refund amount.
4. **BigQuery ([`src/bq_storage.py`](../src/bq_storage.py))** stores the final decision in `agent_decisions` and the step-by-step tool call audit trail in `agent_traces`.

---

## Step 2: Explore the Seed Dataset in BigQuery (`src/bq_storage.py`)

When `seed_default_data()` runs, it populates four representative orders in `orders_catalog` and four corresponding tickets in `raw_tickets`:

| Ticket ID | Order ID | Order Total | Days Since Purchase | Defective Warranty? | Expected Agent Decision |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `TCK-5001` | `ORD-1001` | `$120.00` | `10` days | `False` | `APPROVED` (`$120.00`) |
| `TCK-5002` | `ORD-1002` | `$85.50` | `45` days | `False` | `REJECTED` (`$0.00` - expired window) |
| `TCK-5003` | `ORD-1003` | `$250.00` | `60` days | `True` | `APPROVED` (`$250.00` - warranty override) |
| `TCK-5004` | `ORD-1004` | `$1500.00` | `5` days | `False` | `ESCALATED_TO_HUMAN` (`$0.00` - high value >= `$1000`) |

---

## Step 3: Run Pillar 1 — API & Cloud Scheduler Contract Tests

File: [`tests/test_01_api_and_scheduler.py`](../tests/test_01_api_and_scheduler.py)

What it tests:
- `GET /health` returns service metadata and active `bq_mode`.
- `POST /scheduler/trigger` without `X-CloudScheduler: true` returns `403 Forbidden`.
- Malformed JSON payloads return `422 Unprocessable Entity`.
- Re-sending the exact same `job_run_id` returns `idempotent_skip: True` and `processed_count: 0`, preventing duplicate rows in BigQuery.

Run it:
```bash
.venv/bin/pytest tests/test_01_api_and_scheduler.py -v
```

---

## Step 4: Run Pillar 2 — Agentic AI Trajectory & Anti-Hallucination Tests

File: [`tests/test_02_agentic_workflow.py`](../tests/test_02_agentic_workflow.py)

What it tests:
- **Positive Trajectory Validation**: Verifies that `ORD-1001` invokes `['fetch_order_details', 'evaluate_refund_policy', 'calculate_refund_cap']` in exact sequence.
- **Negative Fault Injection (`fault_mode="hallucination"`)**: Forces the agent to approve `$500.00` on a `$120.00` order and proves that `audit_agent_evaluation()` catches `HALLUCINATION_DEFECT`.
- **Negative Fault Injection (`fault_mode="skipped_tool"`)**: Forces the agent to skip `evaluate_refund_policy` and proves that `audit_agent_evaluation()` catches `SKIPPED_TOOL_DEFECT`.

Run it:
```bash
.venv/bin/pytest tests/test_02_agentic_workflow.py -v
```

---

## Step 5: Run Pillar 3 — BigQuery Cross-Dataset SQL Reconciliation

File: [`tests/test_03_bigquery_reconciliation.py`](../tests/test_03_bigquery_reconciliation.py)

What it tests:
- **Completeness SQL**: `LEFT JOIN` between `raw_tickets` and `agent_decisions` to detect unprocessed tickets.
- **Uniqueness SQL**: `GROUP BY ticket_id HAVING COUNT(*) > 1` to detect duplicate decisions.
- **Financial & Referential Integrity SQL**: `LEFT JOIN` between `agent_decisions` and `orders_catalog` to detect over-refunds (`d.approved_amount > o.order_total`) or ghost order IDs (`o.order_id IS NULL`).
- **Audit Traceability SQL**: `LEFT JOIN` between `agent_decisions` and `agent_traces` to detect decisions missing tool execution logs.

Run it:
```bash
.venv/bin/pytest tests/test_03_bigquery_reconciliation.py -v
```

---

## Step 6: Run Pillar 4 — AI-Assisted Test Generation & Human-in-the-Loop (HITL)

File: [`tests/test_04_ai_test_generation_hitl.py`](../tests/test_04_ai_test_generation_hitl.py)

What it tests:
1. `AITestGenerator` generates 6 candidate test cases (`AI-TC-001` through `AI-TC-006`) from business requirements `REQ-001`..`REQ-005`.
2. Two realistic AI test generation defects are embedded in the candidates:
   - `AI-TC-004` has a **contradictory oracle** (`expected_status="ESCALATED_TO_HUMAN"` but `expected_max_refund=1500.00`).
   - `AI-TC-006` hallucinates a non-existent requirement (`REQ-999`) with an empty tool trajectory.
3. `HumanReviewGate` blocks execution until a human reviewer:
   - Approves valid cases (`AI-TC-001`, `AI-TC-002`, `AI-TC-003`, `AI-TC-005`),
   - Edits `AI-TC-004` (`EDITED_BY_HUMAN`, correcting `expected_max_refund` to `0.0`), and
   - Rejects `AI-TC-006` (`REJECTED`).
4. Verifies `100.0%` requirement coverage and executes the 5 approved tests live against `POST /agent/evaluate`.

Run it:
```bash
.venv/bin/pytest tests/test_04_ai_test_generation_hitl.py -v
```

---

## Step 7: Run the Interactive Root Cause Analysis (RCA) Demo

File: [`src/rca_demo.py`](../src/rca_demo.py)

Execute the end-to-end CLI report comparing a healthy batch run against fault-injected runs:

```bash
.venv/bin/python -m src.rca_demo
```

