---
name: bigquery-data-reconciliation
description: >-
  Validate data accuracy, completeness, and consistency across BigQuery tables
  (orders_catalog, raw_tickets, agent_decisions, agent_traces). Use this skill
  when asked to run SQL reconciliation checks, investigate data discrepancies,
  detect orphan or duplicate records, or verify pipeline integrity.
---

# BigQuery Data Reconciliation & Integrity Skill

Use this runbook to reconcile datasets across the `Cloud Scheduler -> Cloud Run -> BigQuery` pipeline.

## 1. Dataset Schema Overview

The storage layer ([`src/bq_storage.py`](../../src/bq_storage.py)) manages four tables:

| Table | Primary Key | Purpose |
| :--- | :--- | :--- |
| `orders_catalog` | `order_id` | Ground-truth order totals, purchase dates, customer IDs, and item categories |
| `raw_tickets` | `ticket_id` | Incoming customer refund requests waiting for or processed by batch jobs |
| `agent_decisions` | `decision_id` (`ticket_id` unique per valid run) | Structured decisions (`APPROVED`, `REJECTED`, `ESCALATED_TO_HUMAN`) and `approved_amount` |
| `agent_traces` | `trace_id` | Step-by-step tool call audit trail (`step_number`, `tool_name`, `tool_input`, `tool_output`) |

## 2. Standard Reconciliation Queries

Execute these four reconciliation checks after any batch run:

### Check A: Completeness (Missing / Unprocessed Tickets)
Finds `raw_tickets` that have no corresponding record in `agent_decisions`:

```sql
SELECT r.ticket_id, r.order_id, r.requested_amount
FROM raw_tickets r
LEFT JOIN agent_decisions d ON r.ticket_id = d.ticket_id
WHERE d.ticket_id IS NULL;
```

### Check B: Uniqueness (Duplicate Decisions)
Detects idempotency failures where a single `ticket_id` was processed multiple times:

```sql
SELECT ticket_id, COUNT(*) AS decision_count
FROM agent_decisions
GROUP BY ticket_id
HAVING COUNT(*) > 1;
```

### Check C: Financial Accuracy & Groundedness
Detects decisions where `approved_amount` exceeds the ground-truth `order_total` in `orders_catalog` or violates rejection status:

```sql
SELECT
    d.ticket_id,
    d.order_id,
    d.status,
    d.approved_amount,
    o.order_total
FROM agent_decisions d
LEFT JOIN orders_catalog o ON d.order_id = o.order_id
WHERE o.order_id IS NULL
   OR d.approved_amount > o.order_total
   OR (d.status != 'APPROVED' AND d.approved_amount > 0);
```

### Check D: Traceability (Orphan Decisions Without Tool Traces)
Finds decisions in `agent_decisions` that lack mandatory audit logs in `agent_traces`:

```sql
SELECT d.ticket_id, d.status, COUNT(t.trace_id) AS trace_steps
FROM agent_decisions d
LEFT JOIN agent_traces t ON d.ticket_id = t.ticket_id
GROUP BY d.ticket_id, d.status
HAVING COUNT(t.trace_id) = 0;
```

## 3. Running Automated Reconciliation Tests

```bash
.venv/bin/pytest tests/test_03_bigquery_reconciliation.py -v
```

