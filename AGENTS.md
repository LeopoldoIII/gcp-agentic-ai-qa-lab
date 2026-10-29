# Workspace Rules & Engineering Standards

## 1. Strict English-Only Repository Policy (Mandatory)

- **All repository content MUST be written in English**, regardless of the language used by the user during chat or interactive sessions.
- This applies without exception to:
  - Source code identifiers, module names, variables, functions, and classes (`src/`, `tests/`).
  - Inline comments, docstrings, type annotations, and error messages.
  - Documentation, interview guides, architecture logs, and tutorials (`README.md`, `docs/*.md`).
  - Antigravity workspace customizations (`AGENTS.md`, `.agents/skills/**`, `.agents/agents/**`).
  - Git commit messages and pull request descriptions.
- When the user interacts in Spanish or any other language, you may reply in the user's preferred language in the chat interface, but **every file created or modified in this repository must remain strictly in English**.

## 2. System Architecture Context (GCP Agentic AI Pipeline)

This repository implements and tests a serverless **Agentic AI Order Triage & Refund Pipeline** modeled on Google Cloud Platform (GCP):

1. **Cloud Scheduler (`POST /scheduler/trigger`)**: Triggers periodic batch runs with idempotency guarantees (`job_run_id`) and required `X-CloudScheduler` headers.
2. **Cloud Run (`src/cloud_run_app.py` & `src/agent_workflow.py`)**: FastAPI microservice hosting an Agentic AI workflow that invokes deterministic tools (`fetch_order_details`, `evaluate_refund_policy`, `calculate_refund_cap`) before issuing a structured decision.
3. **BigQuery (`src/bq_storage.py`)**: Analytical storage layer holding four tables:
   - `orders_catalog`: Source-of-truth order totals, purchase dates, and item categories.
   - `raw_tickets`: Incoming customer refund/support requests queued for processing.
   - `agent_decisions`: Structured outputs produced by the AI agent (`APPROVED`, `REJECTED`, `ESCALATED_TO_HUMAN`).
   - `agent_traces`: Granular step-by-step tool invocation trajectories for auditability and Root Cause Analysis (RCA).

## 3. QA Engineering & Validation Standards

Every change to the agentic workflow or data layer must satisfy four testing pillars:

1. **API & Cloud Scheduler Contract Testing (`tests/test_01_api_and_scheduler.py`)**:
   - Enforce HTTP status codes, Pydantic schema validation, header checks, and batch idempotency.
2. **Agentic AI Workflow & Trajectory Validation (`tests/test_02_agentic_workflow.py`)**:
   - Verify tool-calling sequence (`fetch_order_details` -> `evaluate_refund_policy` -> `calculate_refund_cap`).
   - Assert groundedness/faithfulness: `approved_amount` must never exceed `order_total` or policy caps.
   - Maintain negative fault-injection tests (`hallucination`, `skipped_tool`, `data_discrepancy`) to verify defect detection.
3. **BigQuery Cross-Dataset Reconciliation (`tests/test_03_bigquery_reconciliation.py`)**:
   - Reconcile record counts, detect duplicates, verify foreign-key completeness across `raw_tickets`, `agent_decisions`, and `agent_traces`, and validate numerical boundaries via SQL.
4. **AI-Assisted Test Generation & Human-in-the-Loop Gate (`tests/test_04_ai_test_generation_hitl.py`)**:
   - Never execute raw AI-generated test cases directly against the SUT without passing through `HumanReviewGate` (`APPROVED` or `EDITED_BY_HUMAN`).

