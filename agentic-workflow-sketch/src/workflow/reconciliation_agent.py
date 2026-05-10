"""
LangGraph-based agentic reconciliation workflow.

Orchestrates:
1. Invoice-PO matching (calls matcher tool)
2. Reconciliation planning (LLM decides action: auto-match, flag, or escalate)
3. Draft email generation (for vendor disputes)
4. Human approval gate (required before sending)
5. Audit trail logging

Design Rationale for LangGraph:
- Explicit state graph for clear control flow
- Built-in checkpointing for resumable workflows
- Tool calling with guardrails (e.g., prevent sending emails without approval)
- Easier to debug than multi-agent systems like AutoGen
"""

import json
import logging
from enum import Enum
from typing import Dict, List, Any, Optional, Annotated
from datetime import datetime
from dataclasses import dataclass, asdict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- State and Message Types ---

class ReconciliationAction(str, Enum):
    """Possible actions for a mismatch."""
    AUTO_MATCH = "auto_match"  # High confidence, no human review needed
    FLAG_FOR_REVIEW = "flag_for_review"  # Medium confidence, requires human
    ESCALATE = "escalate"  # Low confidence or complex; escalate to senior analyst
    REQUIRES_VENDOR_CONTACT = "requires_vendor_contact"  # Mismatch; draft email


class MatchDecision(BaseModel):
    """LLM's decision on how to handle a match."""
    action: ReconciliationAction
    reasoning: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class ReconciliationState(BaseModel):
    """Workflow state shared across all nodes."""
    invoice_id: str
    po_id: str
    invoice_data: Dict[str, Any]
    po_data: Dict[str, Any]
    match_result: Optional[Dict[str, Any]] = None  # From matcher tool
    planned_action: Optional[ReconciliationAction] = None
    draft_email: Optional[str] = None
    human_approved: Optional[bool] = None  # None = not yet reviewed, True/False = reviewed
    approval_notes: Optional[str] = None
    audit_log: List[Dict[str, Any]] = Field(default_factory=list)


# --- Tool Definitions (Guardrails) ---

class ToolInvocation(BaseModel):
    """Wraps a tool call with guardrail metadata."""
    tool_name: str
    args: Dict[str, Any]
    requires_approval: bool = False  # Whether this tool needs human approval before execution


def create_match_tool(matcher_service):
    """
    Factory for the invoice-PO matching tool.
    
    Args:
        matcher_service: InvoicePoMatcher instance or similar
    
    Returns:
        A callable tool that returns match results
    """
    def match_invoice_to_po(invoice_id: str, po_id: str) -> Dict[str, Any]:
        """
        Call the matcher service to score an invoice-PO pair.
        
        Guardrail: This tool is read-only; no risk of false positives affecting data.
        """
        try:
            result = matcher_service.score_pair(invoice_id, po_id)
            return {
                'success': True,
                'match_confidence': result.get('confidence'),
                'matched': result.get('matched'),
                'rule_scores': result.get('rule_scores'),
                'ml_score': result.get('ml_score'),
                'reason': result.get('reason')
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'match_confidence': 0.0
            }
    
    return match_invoice_to_po


def create_email_draft_tool(llm_client):
    """
    Factory for the email drafting tool.
    
    Args:
        llm_client: LLM client (e.g., OpenAI, Anthropic)
    
    Returns:
        A callable tool that generates dispute email drafts
    
    Guardrail: Drafts are NOT sent; requires human approval.
    """
    def draft_vendor_email(
        vendor_name: str,
        invoice_id: str,
        po_id: str,
        mismatch_reason: str,
        amount_variance: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Draft a professional email to vendor regarding a mismatch.
        
        Guardrail: Output is a draft only. Email is NOT sent without explicit approval.
        """
        prompt = f"""
Draft a professional, courteous email to {vendor_name} regarding a discrepancy in invoice {invoice_id}.

Context:
- PO ID: {po_id}
- Mismatch reason: {mismatch_reason}
- Amount variance: {amount_variance if amount_variance else 'N/A'}

Email should:
1. Be polite and professional
2. Clearly state the discrepancy
3. Request clarification or corrected invoice
4. Include contact info for follow-up
5. Be concise (< 200 words)

Generate ONLY the email body, no subject line.
"""
        
        try:
            response = llm_client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7,
                max_tokens=300
            )
            
            draft_text = response.choices[0].message.content.strip()
            
            return {
                'success': True,
                'draft': draft_text,
                'model': 'gpt-4-turbo',
                'note': 'This is a draft. Send only after human approval.'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    return draft_vendor_email


# --- Workflow Nodes ---

def node_match_invoice_to_po(state: ReconciliationState, tools: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node 1: Call matching service to score the invoice-PO pair.
    
    Args:
        state: Current workflow state
        tools: Dict with 'match_tool' callable
    
    Returns:
        Updated state with match_result
    """
    logger.info(f"[NODE] Matching invoice {state.invoice_id} to PO {state.po_id}")
    
    match_tool = tools.get('match_tool')
    if not match_tool:
        logger.error("Match tool not configured")
        return {'match_result': {'error': 'Match tool not available'}}
    
    result = match_tool(state.invoice_id, state.po_id)
    
    state.audit_log.append({
        'timestamp': datetime.utcnow().isoformat(),
        'node': 'match_invoice_to_po',
        'action': 'scoring',
        'result': result
    })
    
    return {'match_result': result}


def node_plan_reconciliation(state: ReconciliationState, llm_client) -> Dict[str, Any]:
    """
    Node 2: LLM decides what action to take based on match result.
    
    Args:
        state: Current workflow state (includes match_result)
        llm_client: LLM client for decision-making
    
    Returns:
        Dict with planned_action
    """
    logger.info(f"[NODE] Planning reconciliation for invoice {state.invoice_id}")
    
    match_result = state.match_result or {}
    confidence = match_result.get('match_confidence', 0.0)
    matched = match_result.get('matched', False)
    reason = match_result.get('reason', 'Unknown')
    
    # Decision logic (can be LLM or rule-based)
    if confidence >= 0.90 and matched:
        action = ReconciliationAction.AUTO_MATCH
        reasoning = "High confidence match; auto-approve"
    elif confidence >= 0.65 and matched:
        action = ReconciliationAction.FLAG_FOR_REVIEW
        reasoning = "Medium confidence; human review recommended"
    elif not matched and confidence < 0.50:
        action = ReconciliationAction.REQUIRES_VENDOR_CONTACT
        reasoning = "Low confidence mismatch; draft email for vendor inquiry"
    else:
        action = ReconciliationAction.ESCALATE
        reasoning = f"Uncertain case: {reason}"
    
    decision = MatchDecision(
        action=action,
        reasoning=reasoning,
        confidence=confidence
    )
    
    state.audit_log.append({
        'timestamp': datetime.utcnow().isoformat(),
        'node': 'plan_reconciliation',
        'action': 'decision',
        'decision': asdict(decision)
    })
    
    return {
        'planned_action': decision.action
    }


def node_draft_email(state: ReconciliationState, tools: Dict[str, Any]) -> Dict[str, Any]:
    """
    Node 3: If action is REQUIRES_VENDOR_CONTACT, draft an email.
    
    Conditional: Only executed if planned_action == REQUIRES_VENDOR_CONTACT
    """
    if state.planned_action != ReconciliationAction.REQUIRES_VENDOR_CONTACT:
        logger.info("[NODE] Skipping email draft (action != REQUIRES_VENDOR_CONTACT)")
        return {}
    
    logger.info(f"[NODE] Drafting email for invoice {state.invoice_id}")
    
    email_tool = tools.get('email_tool')
    if not email_tool:
        logger.warning("Email tool not available")
        return {}
    
    vendor_name = state.invoice_data.get('vendor_id', 'Vendor')
    mismatch_reason = state.match_result.get('reason', 'Amount mismatch')
    amount_var = state.match_result.get('rule_details', {}).get('amount_similarity', {}).get('pct_diff')
    
    result = email_tool(
        vendor_name=vendor_name,
        invoice_id=state.invoice_id,
        po_id=state.po_id,
        mismatch_reason=mismatch_reason,
        amount_variance=amount_var
    )
    
    state.audit_log.append({
        'timestamp': datetime.utcnow().isoformat(),
        'node': 'draft_email',
        'action': 'draft_generation',
        'result': result
    })
    
    return {
        'draft_email': result.get('draft', '') if result.get('success') else None
    }


def node_approval_gate(state: ReconciliationState) -> Dict[str, Any]:
    """
    Node 4: Approval gate - workflow pauses here for human review.
    
    Guardrail: This node enforces that certain actions (like sending emails)
    cannot proceed without explicit human approval.
    
    In production, this would pause and wait for human input via an API callback.
    """
    logger.info(f"[NODE] Approval gate for invoice {state.invoice_id}")
    logger.info(f"  Planned action: {state.planned_action}")
    
    # Determine if human approval is required
    requires_approval = state.planned_action in [
        ReconciliationAction.FLAG_FOR_REVIEW,
        ReconciliationAction.REQUIRES_VENDOR_CONTACT,
        ReconciliationAction.ESCALATE
    ]
    
    state.audit_log.append({
        'timestamp': datetime.utcnow().isoformat(),
        'node': 'approval_gate',
        'action': 'gate_check',
        'requires_approval': requires_approval,
        'status': 'waiting_for_human' if requires_approval else 'auto_approved'
    })
    
    if requires_approval:
        logger.warning(f"  PAUSED: Waiting for human approval")
        return {
            'human_approved': None  # None indicates waiting for decision
        }
    else:
        logger.info(f"  Auto-approved (high confidence match)")
        return {
            'human_approved': True  # Auto-approve for high-confidence matches
        }


def node_execute_action(state: ReconciliationState) -> Dict[str, Any]:
    """
    Node 5: Execute the approved action.
    
    In production, this would:
    - AUTO_MATCH: Mark invoice-PO pair as matched in system
    - FLAG_FOR_REVIEW: Queue for manual analyst review
    - ESCALATE: Escalate to senior analyst
    - REQUIRES_VENDOR_CONTACT: Send email (only if approved)
    
    Guardrail: Only executes if human_approved == True
    """
    logger.info(f"[NODE] Executing action: {state.planned_action}")
    
    if state.human_approved is not True:
        logger.warning(f"  BLOCKED: human_approved={state.human_approved}")
        state.audit_log.append({
            'timestamp': datetime.utcnow().isoformat(),
            'node': 'execute_action',
            'action': 'execution_blocked',
            'reason': 'Not approved by human'
        })
        return {'audit_log': state.audit_log}
    
    action_result = None
    
    if state.planned_action == ReconciliationAction.AUTO_MATCH:
        action_result = {
            'action': 'auto_match_executed',
            'invoice_id': state.invoice_id,
            'po_id': state.po_id,
            'status': 'matched_in_system'
        }
    
    elif state.planned_action == ReconciliationAction.FLAG_FOR_REVIEW:
        action_result = {
            'action': 'flagged_for_review',
            'invoice_id': state.invoice_id,
            'queue': 'manual_analyst_review'
        }
    
    elif state.planned_action == ReconciliationAction.ESCALATE:
        action_result = {
            'action': 'escalated',
            'invoice_id': state.invoice_id,
            'queue': 'senior_analyst'
        }
    
    elif state.planned_action == ReconciliationAction.REQUIRES_VENDOR_CONTACT:
        if state.draft_email:
            action_result = {
                'action': 'email_approved_and_sent',
                'invoice_id': state.invoice_id,
                'recipient': state.invoice_data.get('vendor_id'),
                'email_preview': state.draft_email[:100] + '...'
            }
        else:
            action_result = {
                'action': 'email_send_failed',
                'reason': 'No draft email available'
            }
    
    state.audit_log.append({
        'timestamp': datetime.utcnow().isoformat(),
        'node': 'execute_action',
        'action': 'execution',
        'result': action_result
    })
    
    return {
        'audit_log': state.audit_log
    }


# --- Graph Construction ---

def build_reconciliation_graph(matcher_service, llm_client):
    """
    Build the LangGraph state machine for reconciliation.
    
    Returns:
        Compiled graph ready for invocation
    """
    graph_builder = StateGraph(ReconciliationState)
    
    # Create tools
    tools = {
        'match_tool': create_match_tool(matcher_service),
        'email_tool': create_email_draft_tool(llm_client)
    }
    
    # Add nodes
    graph_builder.add_node(
        "match_invoice_to_po",
        lambda state: node_match_invoice_to_po(state, tools)
    )
    graph_builder.add_node(
        "plan_reconciliation",
        lambda state: node_plan_reconciliation(state, llm_client)
    )
    graph_builder.add_node(
        "draft_email",
        lambda state: node_draft_email(state, tools)
    )
    graph_builder.add_node(
        "approval_gate",
        node_approval_gate
    )
    graph_builder.add_node(
        "execute_action",
        node_execute_action
    )
    
    # Add edges
    graph_builder.add_edge(START, "match_invoice_to_po")
    graph_builder.add_edge("match_invoice_to_po", "plan_reconciliation")
    graph_builder.add_edge("plan_reconciliation", "draft_email")
    graph_builder.add_edge("draft_email", "approval_gate")
    graph_builder.add_edge("approval_gate", "execute_action")
    graph_builder.add_edge("execute_action", END)
    
    graph = graph_builder.compile()
    
    return graph


# --- Workflow Runner ---

class ReconciliationWorkflowRunner:
    """Orchestrates reconciliation workflows with error handling and logging."""
    
    def __init__(self, matcher_service, llm_client):
        self.matcher_service = matcher_service
        self.llm_client = llm_client
        self.graph = build_reconciliation_graph(matcher_service, llm_client)
        self.logger = logging.getLogger(__name__)
    
    def run_reconciliation(
        self,
        invoice_id: str,
        invoice_data: Dict[str, Any],
        po_id: str,
        po_data: Dict[str, Any],
        human_approval_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Execute a full reconciliation workflow.
        
        Args:
            invoice_id: Unique invoice identifier
            invoice_data: Invoice record dict
            po_id: Unique PO identifier
            po_data: PO record dict
            human_approval_callback: Optional function to obtain human approval
                                     Signature: (state) -> bool
        
        Returns:
            Final workflow result dict with audit trail
        """
        initial_state = ReconciliationState(
            invoice_id=invoice_id,
            invoice_data=invoice_data,
            po_id=po_id,
            po_data=po_data
        )
        
        try:
            # Run graph
            final_state = self.graph.invoke(initial_state)
            
            # Handle approval gate if needed
            if final_state.human_approved is None:
                self.logger.info(f"Workflow paused at approval gate for {invoice_id}")
                
                if human_approval_callback:
                    approved = human_approval_callback(final_state)
                    final_state.human_approved = approved
                    
                    if approved:
                        # Resume from approval gate
                        result = self.graph.invoke(
                            final_state,
                            config={'thread_id': invoice_id}
                        )
                        return asdict(result)
                else:
                    return {
                        'status': 'paused_at_approval',
                        'invoice_id': invoice_id,
                        'po_id': po_id,
                        'planned_action': final_state.planned_action.value,
                        'audit_log': final_state.audit_log,
                        'draft_email': final_state.draft_email
                    }
            
            return asdict(final_state)
        
        except Exception as e:
            self.logger.error(f"Workflow error for {invoice_id}: {e}", exc_info=True)
            return {
                'status': 'error',
                'invoice_id': invoice_id,
                'error': str(e),
                'audit_log': initial_state.audit_log
            }
