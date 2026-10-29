---
name: agentic-workflow-eval
description: >-
  Evaluate and troubleshoot Agentic AI workflows running on Cloud Run. Use this
  skill when asked to test agent tool-calling trajectories, check output
  groundedness/faithfulness, run fault-injection scenarios (hallucination,
  skipped_tool, data_discrepancy), or perform Root Cause Analysis (RCA) on agent
  failures.
---

# Agentic AI Workflow Evaluation Skill

Use this procedural runbook to validate non-deterministic and semi-deterministic Agentic AI workflows in this repository.

## 1. Core Evaluation Dimensions

When evaluating an execution of [`RefundAgentWorkflow`](../../src/agent_workflow.py), verify these four dimensions:

1. **Tool Trajectory Completeness & Order**:
   - Required tool sequence for valid order IDs:
     1. `fetch_order_details`
     2. `evaluate_refund_policy`
     3. `calculate_refund_cap` (whenever policy is eligible)
   - An approval decision (`status == "APPROVED"`) without prior execution of both `fetch_order_details` and `evaluate_refund_policy` is a critical **Skipped Tool Defect**.
2. **Groundedness & Faithfulness (Anti-Hallucination)**:
   - Compare `decision.approved_amount` against `orders_catalog.order_total` and `calculated_cap`.
   - Any `approved_amount > order_total` is a **Financial Hallucination Defect**.
   - Any `approved_amount > 0` when `decision.status == "REJECTED"` is a **Contradictory Output Defect**.
3. **Policy Compliance**:
   - Orders older than 30 days (unless marked `DEFECTIVE_WARRANTY`) must be `REJECTED`.
   - High-value orders (`order_total >= 1000.0`) or low-confidence cases must be `ESCALATED_TO_HUMAN`.
4. **Schema & Trace Persistence**:
   - Every decision in `agent_decisions` must have corresponding step logs in `agent_traces`.

## 2. Running Agentic Workflow Tests

Run the dedicated Pytest module:

```bash
.venv/bin/pytest tests/test_02_agentic_workflow.py -v
```

## 3. Running Fault-Injection & Root Cause Analysis (RCA)

To simulate production defects (`hallucination`, `skipped_tool`, `data_discrepancy`) and generate an automated RCA report:

```bash
.venv/bin/python -m src.rca_demo
```

## 4. Troubleshooting Checklist

1. Inspect the returned `tool_trajectory` list on the API response or query `agent_traces` by `ticket_id`.
2. Verify whether the failure originated in:
   - **Tool Input/Output**: Did `fetch_order_details` return `None`?
   - **Agent Reasoning / Prompt Drift**: Did the LLM ignore the output of `evaluate_refund_policy`?
   - **Persistence Layer**: Did `bq_storage` fail to write traces or duplicate the decision row?

