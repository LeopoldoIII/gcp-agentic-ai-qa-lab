"""
Cloud Run FastAPI Service for the Agentic AI Order Refund Pipeline.

Exposes:
- `GET /health`: Service health and storage mode status.
- `POST /scheduler/trigger`: Batch trigger endpoint invoked by Cloud Scheduler
  (requires `X-CloudScheduler: true` header and enforces `job_run_id` idempotency).
- `POST /agent/evaluate`: Direct synchronous endpoint to evaluate a single ticket
  and return the structured decision + tool-calling trajectory.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from src.agent_workflow import RefundAgentWorkflow
from src.bq_storage import HybridBigQueryStorage, RawTicketRecord


class SchedulerTriggerPayload(BaseModel):
    job_run_id: str = Field(..., min_length=3, description="Unique Cloud Scheduler execution ID")
    batch_id: str = Field(default="BATCH-01", min_length=1)
    fault_mode: Literal["normal", "hallucination", "skipped_tool", "data_discrepancy"] = "normal"


class SingleTicketEvaluationRequest(BaseModel):
    ticket_id: str = Field(..., min_length=3)
    order_id: str = Field(..., min_length=3)
    customer_id: str = Field(..., min_length=2)
    requested_amount: float = Field(..., ge=0.0)
    reason: str = Field(..., min_length=3)
    fault_mode: Literal["normal", "hallucination", "skipped_tool", "data_discrepancy"] = "normal"
    persist: bool = True


def create_app(storage: Optional[HybridBigQueryStorage] = None) -> FastAPI:
    app = FastAPI(
        title="GCP Cloud Run - Agentic AI Refund Service",
        version="1.0.0",
        description="Serverless Agentic AI microservice triggered by Cloud Scheduler and backed by BigQuery.",
    )

    if storage is None:
        storage = HybridBigQueryStorage(mode="local")
        storage.seed_default_data()

    app.state.storage = storage
    app.state.workflow = RefundAgentWorkflow(storage=storage)

    def get_storage() -> HybridBigQueryStorage:
        return app.state.storage

    def get_workflow() -> RefundAgentWorkflow:
        return app.state.workflow

    @app.get("/health")
    def health_check(bq: HybridBigQueryStorage = Depends(get_storage)) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "service": "cloud-run-refund-agent",
            "bq_mode": bq.mode,
            "project_id": bq.project_id,
            "dataset_id": bq.dataset_id,
        }

    @app.post("/scheduler/trigger")
    def trigger_scheduled_batch(
        payload: SchedulerTriggerPayload,
        x_cloudscheduler: Optional[str] = Header(default=None, alias="X-CloudScheduler"),
        x_cloudscheduler_jobname: Optional[str] = Header(
            default="refund-agent-hourly-job", alias="X-CloudScheduler-JobName"
        ),
        bq: HybridBigQueryStorage = Depends(get_storage),
        workflow: RefundAgentWorkflow = Depends(get_workflow),
    ) -> Dict[str, Any]:
        """
        Endpoint invoked by Google Cloud Scheduler.
        Validates the `X-CloudScheduler: true` header and guarantees idempotency
        when Cloud Scheduler retries an already-processed `job_run_id`.
        """
        if not x_cloudscheduler or x_cloudscheduler.lower() != "true":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing or invalid 'X-CloudScheduler: true' header.",
            )

        # Idempotency check: prevent duplicate processing on Cloud Scheduler retry
        if bq.has_processed_job_run(payload.job_run_id):
            return {
                "job_run_id": payload.job_run_id,
                "job_name": x_cloudscheduler_jobname,
                "batch_id": payload.batch_id,
                "idempotent_skip": True,
                "processed_count": 0,
                "decisions": [],
            }

        tickets = bq.list_raw_tickets(batch_id=payload.batch_id)
        decisions: List[Dict[str, Any]] = []
        for ticket in tickets:
            eval_result = workflow.evaluate_ticket(
                ticket=ticket,
                job_run_id=payload.job_run_id,
                fault_mode=payload.fault_mode,
                persist=True,
            )
            decisions.append(asdict(eval_result))

        bq.mark_job_run_processed(payload.job_run_id)
        return {
            "job_run_id": payload.job_run_id,
            "job_name": x_cloudscheduler_jobname,
            "batch_id": payload.batch_id,
            "idempotent_skip": False,
            "processed_count": len(decisions),
            "decisions": decisions,
        }

    @app.post("/agent/evaluate")
    def evaluate_single_ticket(
        request: SingleTicketEvaluationRequest,
        workflow: RefundAgentWorkflow = Depends(get_workflow),
    ) -> Dict[str, Any]:
        ticket = RawTicketRecord(
            ticket_id=request.ticket_id,
            order_id=request.order_id,
            customer_id=request.customer_id,
            requested_amount=request.requested_amount,
            reason=request.reason,
            batch_id="ADHOC-API",
        )
        result = workflow.evaluate_ticket(
            ticket=ticket,
            job_run_id="API-DIRECT",
            fault_mode=request.fault_mode,
            persist=request.persist,
        )
        return asdict(result)

    return app


app = create_app()

