"""
Hybrid BigQuery Storage & Reconciliation Layer.

Supports two execution modes controlled by the `BQ_MODE` environment variable:
- `local` (default): Zero-dependency in-memory/file SQLite analytical engine that
  emulates BigQuery tables and Standard SQL reconciliation queries in milliseconds.
- `floci` / `gcp`: Lazy-loads `google-cloud-bigquery` SDK to connect either to a
  local Floci emulator (`http://localhost:4588`) or a production GCP project.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class OrderRecord:
    order_id: str
    customer_id: str
    order_total: float
    days_since_purchase: int
    item_category: str
    is_defective_warranty: bool


@dataclass
class RawTicketRecord:
    ticket_id: str
    order_id: str
    customer_id: str
    requested_amount: float
    reason: str
    batch_id: str


@dataclass
class AgentDecisionRecord:
    decision_id: str
    ticket_id: str
    order_id: str
    status: str  # APPROVED, REJECTED, ESCALATED_TO_HUMAN
    approved_amount: float
    confidence: float
    reasoning: str
    job_run_id: str


@dataclass
class AgentTraceRecord:
    trace_id: str
    ticket_id: str
    step_number: int
    tool_name: str
    tool_input: str
    tool_output: str


class HybridBigQueryStorage:
    """
    Emulates or connects to BigQuery datasets (`orders_catalog`, `raw_tickets`,
    `agent_decisions`, `agent_traces`) and provides SQL reconciliation utilities.
    """

    def __init__(
        self,
        mode: Optional[str] = None,
        project_id: str = "qa-agentic-gcp-lab",
        dataset_id: str = "refund_agent_dw",
        sqlite_path: str = ":memory:",
    ) -> None:
        self.mode = (mode or os.getenv("BQ_MODE", "local")).lower()
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.sqlite_path = sqlite_path
        self._conn: Optional[sqlite3.Connection] = None
        self._bq_client: Any = None
        self._processed_job_runs: set[str] = set()

        if self.mode == "local":
            self._conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_local_schema()
        else:
            self._init_gcp_or_floci_client()

    def _init_local_schema(self) -> None:
        assert self._conn is not None
        cursor = self._conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders_catalog (
                order_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                order_total REAL NOT NULL,
                days_since_purchase INTEGER NOT NULL,
                item_category TEXT NOT NULL,
                is_defective_warranty INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS raw_tickets (
                ticket_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                requested_amount REAL NOT NULL,
                reason TEXT NOT NULL,
                batch_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_decisions (
                decision_id TEXT,
                ticket_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                approved_amount REAL NOT NULL,
                confidence REAL NOT NULL,
                reasoning TEXT NOT NULL,
                job_run_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_traces (
                trace_id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_input TEXT NOT NULL,
                tool_output TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _init_gcp_or_floci_client(self) -> None:
        """Lazy-loads google-cloud-bigquery only when BQ_MODE is 'floci' or 'gcp'."""
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-bigquery is required for BQ_MODE='floci' or 'gcp'. "
                "Install it with: pip install google-cloud-bigquery"
            ) from exc

        if self.mode == "floci":
            endpoint = os.getenv("FLOCI_ENDPOINT", "http://localhost:4588")
            opts = ClientOptions(api_endpoint=endpoint)
            self._bq_client = bigquery.Client(
                project=self.project_id, client_options=opts
            )
        else:
            self._bq_client = bigquery.Client(project=self.project_id)

    def seed_default_data(self) -> None:
        """Populates deterministic baseline orders and raw tickets for testing."""
        orders = [
            OrderRecord("ORD-1001", "CUST-01", 120.00, 10, "ELECTRONICS", False),
            OrderRecord("ORD-1002", "CUST-02", 85.50, 45, "APPAREL", False),
            OrderRecord("ORD-1003", "CUST-03", 250.00, 60, "ELECTRONICS", True),
            OrderRecord("ORD-1004", "CUST-04", 1500.00, 5, "ENTERPRISE_HW", False),
            OrderRecord("ORD-1005", "CUST-05", 60.00, 14, "BOOKS", False),
        ]
        for order in orders:
            self.upsert_order(order)

        tickets = [
            RawTicketRecord(
                "TCK-5001", "ORD-1001", "CUST-01", 120.00, "Changed mind", "BATCH-01"
            ),
            RawTicketRecord(
                "TCK-5002",
                "ORD-1002",
                "CUST-02",
                85.50,
                "Late return request",
                "BATCH-01",
            ),
            RawTicketRecord(
                "TCK-5003",
                "ORD-1003",
                "CUST-03",
                250.00,
                "Device stopped working under warranty",
                "BATCH-01",
            ),
            RawTicketRecord(
                "TCK-5004",
                "ORD-1004",
                "CUST-04",
                1500.00,
                "High value enterprise server return",
                "BATCH-01",
            ),
        ]
        for ticket in tickets:
            self.insert_raw_ticket(ticket)

    def upsert_order(self, order: OrderRecord) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO orders_catalog
            (order_id, customer_id, order_total, days_since_purchase, item_category, is_defective_warranty)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.customer_id,
                order.order_total,
                order.days_since_purchase,
                order.item_category,
                1 if order.is_defective_warranty else 0,
            ),
        )
        self._conn.commit()

    def get_order(self, order_id: str) -> Optional[OrderRecord]:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM orders_catalog WHERE order_id = ?", (order_id,)
        ).fetchone()
        if not row:
            return None
        return OrderRecord(
            order_id=row["order_id"],
            customer_id=row["customer_id"],
            order_total=float(row["order_total"]),
            days_since_purchase=int(row["days_since_purchase"]),
            item_category=row["item_category"],
            is_defective_warranty=bool(row["is_defective_warranty"]),
        )

    def insert_raw_ticket(self, ticket: RawTicketRecord) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO raw_tickets
            (ticket_id, order_id, customer_id, requested_amount, reason, batch_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticket.ticket_id,
                ticket.order_id,
                ticket.customer_id,
                ticket.requested_amount,
                ticket.reason,
                ticket.batch_id,
            ),
        )
        self._conn.commit()

    def list_raw_tickets(self, batch_id: Optional[str] = None) -> List[RawTicketRecord]:
        assert self._conn is not None
        if batch_id:
            rows = self._conn.execute(
                "SELECT * FROM raw_tickets WHERE batch_id = ? ORDER BY ticket_id ASC",
                (batch_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM raw_tickets ORDER BY ticket_id ASC"
            ).fetchall()
        return [
            RawTicketRecord(
                ticket_id=r["ticket_id"],
                order_id=r["order_id"],
                customer_id=r["customer_id"],
                requested_amount=float(r["requested_amount"]),
                reason=r["reason"],
                batch_id=r["batch_id"],
            )
            for r in rows
        ]

    def has_processed_job_run(self, job_run_id: str) -> bool:
        return job_run_id in self._processed_job_runs

    def mark_job_run_processed(self, job_run_id: str) -> None:
        self._processed_job_runs.add(job_run_id)

    def insert_decision(self, decision: AgentDecisionRecord) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO agent_decisions
            (decision_id, ticket_id, order_id, status, approved_amount, confidence, reasoning, job_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.ticket_id,
                decision.order_id,
                decision.status,
                decision.approved_amount,
                decision.confidence,
                decision.reasoning,
                decision.job_run_id,
            ),
        )
        self._conn.commit()

    def insert_traces(self, traces: List[AgentTraceRecord]) -> None:
        assert self._conn is not None
        for trace in traces:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_traces
                (trace_id, ticket_id, step_number, tool_name, tool_input, tool_output)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.trace_id,
                    trace.ticket_id,
                    trace.step_number,
                    trace.tool_name,
                    trace.tool_input,
                    trace.tool_output,
                ),
            )
        self._conn.commit()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        """Executes a SQL query and returns rows as a list of dictionaries."""
        assert self._conn is not None
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Cross-Dataset Reconciliation Queries (BigQuery QA Pillar)
    # -------------------------------------------------------------------------

    def find_unprocessed_tickets(self) -> List[Dict[str, Any]]:
        """Returns raw_tickets that are missing from agent_decisions."""
        sql = """
            SELECT
                r.ticket_id,
                r.order_id,
                r.requested_amount,
                r.batch_id
            FROM raw_tickets r
            LEFT JOIN agent_decisions d ON r.ticket_id = d.ticket_id
            WHERE d.ticket_id IS NULL
            ORDER BY r.ticket_id ASC
        """
        return self.query(sql)

    def find_duplicate_decisions(self) -> List[Dict[str, Any]]:
        """Returns ticket_ids that appear more than once in agent_decisions."""
        sql = """
            SELECT
                ticket_id,
                COUNT(*) AS decision_count
            FROM agent_decisions
            GROUP BY ticket_id
            HAVING COUNT(*) > 1
            ORDER BY ticket_id ASC
        """
        return self.query(sql)

    def find_financial_or_policy_discrepancies(self) -> List[Dict[str, Any]]:
        """
        Reconciles agent_decisions against orders_catalog to detect:
        1. Non-existent order_id references (hallucinated order IDs).
        2. Approved refund amounts exceeding the ground-truth order_total.
        3. Non-zero approved amounts when the decision status is not APPROVED.
        """
        sql = """
            SELECT
                d.decision_id,
                d.ticket_id,
                d.order_id,
                d.status,
                d.approved_amount,
                o.order_total,
                CASE
                    WHEN o.order_id IS NULL THEN 'MISSING_ORDER_IN_CATALOG'
                    WHEN d.approved_amount > o.order_total THEN 'OVER_REFUND_EXCEEDS_ORDER_TOTAL'
                    WHEN d.status != 'APPROVED' AND d.approved_amount > 0 THEN 'NON_ZERO_AMOUNT_ON_NON_APPROVED_STATUS'
                    ELSE 'OK'
                END AS discrepancy_type
            FROM agent_decisions d
            LEFT JOIN orders_catalog o ON d.order_id = o.order_id
            WHERE o.order_id IS NULL
               OR d.approved_amount > o.order_total
               OR (d.status != 'APPROVED' AND d.approved_amount > 0)
            ORDER BY d.ticket_id ASC
        """
        return self.query(sql)

    def find_decisions_missing_traces(self) -> List[Dict[str, Any]]:
        """
        Reconciles agent_decisions against agent_traces to ensure every decision
        has a recorded tool-calling trajectory.
        """
        sql = """
            SELECT
                d.ticket_id,
                d.order_id,
                d.status,
                COUNT(t.trace_id) AS trace_count
            FROM agent_decisions d
            LEFT JOIN agent_traces t ON d.ticket_id = t.ticket_id
            GROUP BY d.ticket_id, d.order_id, d.status
            HAVING COUNT(t.trace_id) = 0
            ORDER BY d.ticket_id ASC
        """
        return self.query(sql)

    def run_full_reconciliation_report(self) -> Dict[str, Any]:
        """Runs all 4 cross-dataset reconciliation checks and summarizes health."""
        unprocessed = self.find_unprocessed_tickets()
        duplicates = self.find_duplicate_decisions()
        financial_discrepancies = self.find_financial_or_policy_discrepancies()
        missing_traces = self.find_decisions_missing_traces()

        is_healthy = not (
            unprocessed or duplicates or financial_discrepancies or missing_traces
        )
        return {
            "is_healthy": is_healthy,
            "unprocessed_tickets": unprocessed,
            "duplicate_decisions": duplicates,
            "financial_or_policy_discrepancies": financial_discrepancies,
            "decisions_missing_traces": missing_traces,
        }

