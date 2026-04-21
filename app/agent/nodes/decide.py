from app.agent.state import FreightBillState
from app.db.postgres import AsyncSessionLocal
from app.db.models import AgentDecisionRecord, FreightBill, FreightBillProcessingStatus, AgentDecision, ReviewQueue
from app.core.audit import log_audit_event
from app.services.graph_service import GraphService
from langgraph.types import interrupt
import json

async def run(state: FreightBillState) -> dict:
    freight_bill = state["freight_bill"]
    decision_str = state.get("decision", "escalate" if state.get("should_escalate") else "unknown")
    score = state.get("confidence_score", 0.0)
    
    # We must save human_review logic first, to handle resume
    if "human_review" in state and state["human_review"] is not None:
        # Agent resumed from interrupt, human_review is provided
        async with AsyncSessionLocal() as session:
            # Update processing_status
            bill = await session.get(FreightBill, freight_bill["id"])
            if bill:
                bill.processing_status = FreightBillProcessingStatus.completed
            await session.commit()
            
            # The API route already wrote the HumanReview record and updated queue
        return {} # reached end
    
    # Writing the AgentDecisionRecord
    async with AsyncSessionLocal() as session:
        # Update freight bill processing status based on decision
        bill = await session.get(FreightBill, freight_bill["id"])
        if not bill:
            return {}
            
        decision_record = AgentDecisionRecord(
            freight_bill_id=freight_bill["id"],
            matched_contract_id=state.get("matched_contract", {}).get("id") if state.get("matched_contract") else None,
            matched_shipment_id=state.get("matched_shipment", {}).get("id") if state.get("matched_shipment") else None,
            matched_bol_ids=[b["id"] for b in state.get("matched_bols", [])],
            confidence_score=score,
            decision=AgentDecision(decision_str),
            validation_results=state.get("validation_results", []),
            evidence=state.get("evidence"),
            flag_reason=state.get("flag_reason")
        )
        session.add(decision_record)
        
        if decision_str == "auto_approve":
            bill.processing_status = FreightBillProcessingStatus.completed
            
            # Audit log
            await log_audit_event(session, "auto_approved", freight_bill["id"], {
                "score": score,
                "contract_id": decision_record.matched_contract_id,
                "shipment_id": decision_record.matched_shipment_id
            })
            await session.commit()
            
            # Update Neo4j
            if decision_record.matched_contract_id:
                graph_service = GraphService()
                await graph_service.link_freight_bill_to_contract(freight_bill["id"], decision_record.matched_contract_id)
                
        elif decision_str in ["flag_for_review", "dispute", "escalate"]:
            bill.processing_status = FreightBillProcessingStatus.awaiting_review
            
            # serialize state
            state_dict = dict(state)
            
            queue_item = ReviewQueue(
                freight_bill_id=freight_bill["id"],
                agent_state=state_dict,
                flag_reason=state.get("flag_reason"),
                confidence_score=score,
                evidence=state.get("evidence")
            )
            session.add(queue_item)
            
            await log_audit_event(session, "flagged_for_review", freight_bill["id"], {
                "reason": state.get("flag_reason"),
                "score": score,
                "decision": decision_str
            })
            await session.commit()
            
    if decision_str in ["flag_for_review", "dispute", "escalate"]:
        # Pause execution and wait for human review
        interrupt("Human review required")
            
    return {}
