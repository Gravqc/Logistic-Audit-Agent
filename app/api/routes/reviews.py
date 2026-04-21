from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.dependencies import get_db_session
from app.schemas.review import ReviewSubmit, ReviewSubmitResponse
from app.db.models import ReviewQueue, ReviewQueueStatus, HumanReview, ReviewDecision, FreightBillProcessingStatus, FreightBill
from app.core.audit import log_audit_event
import json

router = APIRouter()

async def resume_agent(bill_id: str, reviewer_decision_dict: dict):
    from app.agent.graph import build_graph
    graph = build_graph()
    config = {"configurable": {"thread_id": bill_id}}
    await graph.ainvoke({"human_review": reviewer_decision_dict}, config=config)

@router.get("/review-queue")
async def get_review_queue(session: AsyncSession = Depends(get_db_session)):
    query = select(ReviewQueue, FreightBill).join(FreightBill).where(ReviewQueue.status == ReviewQueueStatus.pending)
    result = await session.execute(query)
    
    queue = []
    for queue_item, bill in result:
        queue.append({
            "review_queue_id": queue_item.id,
            "freight_bill_id": queue_item.freight_bill_id,
            "carrier_name": bill.carrier_name,
            "lane": bill.lane,
            "total_amount": bill.total_amount,
            "flag_reason": queue_item.flag_reason,
            "confidence_score": queue_item.confidence_score,
            "evidence": queue_item.evidence,
            "flagged_at": queue_item.created_at
        })
        
    return {"queue": queue, "total": len(queue)}

@router.post("/review/{id}", response_model=ReviewSubmitResponse)
async def submit_review(
    id: str,
    review: ReviewSubmit,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session)
):
    # Fetch ReviewQueue row for this freight_bill_id
    query = select(ReviewQueue).where(
        ReviewQueue.freight_bill_id == id,
        ReviewQueue.status == ReviewQueueStatus.pending
    )
    result = await session.execute(query)
    queue_item = result.scalars().first()
    
    if not queue_item:
        raise HTTPException(status_code=404, detail="Pending review item not found for this freight bill.")
        
    # Write HumanReview record
    human_review = HumanReview(
        review_queue_id=queue_item.id,
        freight_bill_id=id,
        reviewer_decision=ReviewDecision(review.reviewer_decision),
        reviewer_notes=review.reviewer_notes,
        corrected_amount=review.corrected_amount
    )
    session.add(human_review)
    
    # Update queue status (this is handled inside agent or here, but we can do it here for immediate feedback)
    queue_item.status = ReviewQueueStatus.reviewed
    
    # Also we might want to update the bill processing_status, but the agent will do that on resume.
    await session.commit()
    
    # Audit log
    await log_audit_event(session, "human_reviewed", id, {
        "reviewer_decision": review.reviewer_decision,
        "notes": review.reviewer_notes
    })
    
    # Resume LangGraph agent
    background_tasks.add_task(resume_agent, id, review.model_dump())
    
    return ReviewSubmitResponse(
        freight_bill_id=id,
        reviewer_decision=review.reviewer_decision,
        message="Review submitted. Agent resuming."
    )
