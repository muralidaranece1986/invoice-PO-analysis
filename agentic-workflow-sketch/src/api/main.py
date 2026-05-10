"""
FastAPI service for invoice-PO reconciliation.

Endpoints:
- POST /reconcile: Start a reconciliation workflow
- GET /reconcile/{invoice_id}: Get workflow status
- POST /reconcile/{invoice_id}/approve: Provide human approval for a paused workflow
- POST /batch_reconcile: Batch reconciliation of multiple invoices
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import pandas as pd

# Import workflow and matcher
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from workflow.reconciliation_agent import (
    ReconciliationWorkflowRunner,
    ReconciliationAction,
    ReconciliationState
)
from matcher.matcher import InvoicePoMatcher

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Invoice-PO Reconciliation Service",
    description="Agentic workflow for matching invoices to purchase orders",
    version="1.0.0"
)

# Global state (in production, use persistent storage)
ACTIVE_WORKFLOWS = {}  # {invoice_id: workflow_state}
WORKFLOW_HISTORY = []  # Audit log


# --- Request/Response Models ---

class InvoiceData(BaseModel):
    invoice_id: str
    vendor_id: str
    invoice_date: str
    amount: float
    description: str


class POData(BaseModel):
    po_id: str
    vendor_id: str
    po_date: str
    line_item_amount: float
    description: str


class ReconcileRequest(BaseModel):
    invoice: InvoiceData
    po: POData


class ReconcileResponse(BaseModel):
    invoice_id: str
    po_id: str
    status: str  # pending, paused_at_approval, approved, executed, error
    planned_action: Optional[str] = None
    match_confidence: Optional[float] = None
    requires_approval: bool = False
    draft_email: Optional[str] = None
    audit_log: List[Dict[str, Any]] = []


class ApprovalRequest(BaseModel):
    approved: bool
    notes: Optional[str] = None


class ApprovalResponse(BaseModel):
    invoice_id: str
    status: str
    action_taken: Optional[str] = None


class BatchReconcileRequest(BaseModel):
    invoices_csv: str  # File path to CSV
    pos_csv: str  # File path to CSV
    concurrent: int = 5


class BatchReconcileResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    status_url: str


# --- Initialization ---

def init_services():
    """Initialize matcher and workflow runner."""
    global matcher, workflow_runner
    
    # Initialize matcher
    model_path = os.path.join(
        os.path.dirname(__file__),
        '..',
        'matcher',
        'model_artifacts.pkl'
    )
    
    matcher = InvoicePoMatcher(model_path=model_path if os.path.exists(model_path) else None)
    logger.info("Matcher initialized")
    
    # Initialize workflow runner (with mock LLM client for now)
    class MockLLMClient:
        class ChatCompletions:
            def create(self, *args, **kwargs):
                class Response:
                    class Choice:
                        class Message:
                            content = "Mock LLM response"
                        message = Message()
                    choices = [Choice()]
                return Response()
        
        chat = ChatCompletions()
    
    workflow_runner = ReconciliationWorkflowRunner(
        matcher_service=matcher,
        llm_client=MockLLMClient()
    )
    logger.info("Workflow runner initialized")


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    init_services()


# --- Endpoints ---

@app.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_invoice(request: ReconcileRequest) -> ReconcileResponse:
    """
    Start a reconciliation workflow for an invoice-PO pair.
    
    Returns:
    - If high confidence match: immediately returns success
    - If requires review: pauses at approval gate, returns draft email if dispute
    - If error: returns error details
    """
    invoice_id = request.invoice.invoice_id
    po_id = request.po.po_id
    
    logger.info(f"Reconciling invoice {invoice_id} to PO {po_id}")
    
    try:
        # Run workflow
        result = workflow_runner.run_reconciliation(
            invoice_id=invoice_id,
            invoice_data=request.invoice.dict(),
            po_id=po_id,
            po_data=request.po.dict()
        )
        
        # Extract key fields for response
        status = result.get('status', 'pending')
        if status == 'paused_at_approval':
            status = 'requires_approval'
        
        match_confidence = None
        if result.get('match_result'):
            match_confidence = result['match_result'].get('match_confidence')
        
        response = ReconcileResponse(
            invoice_id=invoice_id,
            po_id=po_id,
            status=status,
            planned_action=result.get('planned_action'),
            match_confidence=match_confidence,
            requires_approval=status == 'requires_approval',
            draft_email=result.get('draft_email'),
            audit_log=result.get('audit_log', [])
        )
        
        # Store workflow state for later approval
        if status == 'requires_approval':
            ACTIVE_WORKFLOWS[invoice_id] = result
        
        # Log to history
        WORKFLOW_HISTORY.append({
            'timestamp': datetime.utcnow().isoformat(),
            'invoice_id': invoice_id,
            'po_id': po_id,
            'status': status
        })
        
        return response
    
    except Exception as e:
        logger.error(f"Reconciliation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reconcile/{invoice_id}", response_model=ReconcileResponse)
async def get_reconciliation_status(invoice_id: str) -> ReconcileResponse:
    """Get the current status of a reconciliation workflow."""
    if invoice_id not in ACTIVE_WORKFLOWS:
        raise HTTPException(status_code=404, detail=f"No active workflow for {invoice_id}")
    
    result = ACTIVE_WORKFLOWS[invoice_id]
    
    return ReconcileResponse(
        invoice_id=invoice_id,
        po_id=result.get('po_id'),
        status=result.get('status', 'pending'),
        planned_action=result.get('planned_action'),
        match_confidence=result.get('match_result', {}).get('match_confidence'),
        requires_approval=result.get('status') == 'paused_at_approval',
        draft_email=result.get('draft_email'),
        audit_log=result.get('audit_log', [])
    )


@app.post("/reconcile/{invoice_id}/approve", response_model=ApprovalResponse)
async def approve_reconciliation(
    invoice_id: str,
    request: ApprovalRequest
) -> ApprovalResponse:
    """
    Provide human approval for a paused reconciliation workflow.
    
    This endpoint is called by a human analyst after reviewing the
    draft email or reconciliation details.
    
    Guardrail: Actions like sending vendor emails can only proceed
    after this explicit approval.
    """
    if invoice_id not in ACTIVE_WORKFLOWS:
        raise HTTPException(status_code=404, detail=f"No active workflow for {invoice_id}")
    
    workflow_state = ACTIVE_WORKFLOWS[invoice_id]
    
    logger.info(f"Approving reconciliation for {invoice_id}: approved={request.approved}")
    
    if request.approved:
        # Update state and execute action
        workflow_state['human_approved'] = True
        workflow_state['approval_notes'] = request.notes
        
        # Log approval
        workflow_state['audit_log'].append({
            'timestamp': datetime.utcnow().isoformat(),
            'node': 'human_approval',
            'action': 'approved',
            'notes': request.notes
        })
        
        action_taken = workflow_state.get('planned_action', 'unknown')
        
        logger.info(f"Executing approved action: {action_taken}")
        
        return ApprovalResponse(
            invoice_id=invoice_id,
            status='executed',
            action_taken=action_taken
        )
    else:
        # Reject
        workflow_state['human_approved'] = False
        workflow_state['approval_notes'] = request.notes or "Rejected by analyst"
        
        workflow_state['audit_log'].append({
            'timestamp': datetime.utcnow().isoformat(),
            'node': 'human_approval',
            'action': 'rejected',
            'notes': request.notes
        })
        
        logger.info(f"Reconciliation rejected for {invoice_id}")
        
        return ApprovalResponse(
            invoice_id=invoice_id,
            status='rejected',
            action_taken=None
        )


@app.post("/batch_reconcile", response_model=BatchReconcileResponse)
async def batch_reconcile(
    request: BatchReconcileRequest,
    background_tasks: BackgroundTasks
) -> BatchReconcileResponse:
    """
    Batch reconcile multiple invoices against POs.
    
    Loads CSVs, matches all invoices to POs, and returns summary.
    Details available at /batch/{batch_id}/results.
    """
    batch_id = f"batch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    
    logger.info(f"Starting batch reconciliation: {batch_id}")
    
    try:
        # Load data
        invoices_df = pd.read_csv(request.invoices_csv)
        pos_df = pd.read_csv(request.pos_csv)
        
        logger.info(f"Loaded {len(invoices_df)} invoices and {len(pos_df)} POs")
        
        # Run batch matching in background
        def run_batch():
            results = matcher.batch_match(invoices_df, pos_df)
            
            # Store results
            results_path = f"/tmp/{batch_id}_results.csv"
            results.to_csv(results_path, index=False)
            
            logger.info(f"Batch {batch_id} completed: {len(results)} results")
        
        background_tasks.add_task(run_batch)
        
        return BatchReconcileResponse(
            batch_id=batch_id,
            total=len(invoices_df),
            completed=0,
            failed=0,
            status_url=f"/batch/{batch_id}/results"
        )
    
    except Exception as e:
        logger.error(f"Batch reconciliation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {
        'status': 'healthy',
        'service': 'invoice-po-reconciliation',
        'timestamp': datetime.utcnow().isoformat()
    }


@app.get("/metrics")
async def get_metrics() -> Dict[str, Any]:
    """Get service metrics."""
    return {
        'active_workflows': len(ACTIVE_WORKFLOWS),
        'total_workflows': len(WORKFLOW_HISTORY),
        'timestamp': datetime.utcnow().isoformat()
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
