from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.dependencies import get_db_session, get_graph_service
from app.schemas.freight_bill import FreightBillCreate, FreightBillResponse
from app.db.models import FreightBill, FreightBillProcessingStatus, HumanReview, ReviewQueue, AgentDecisionRecord, AuditLog
from app.core.audit import log_audit_event
from app.services.graph_service import GraphService
from datetime import datetime
import asyncio
from sqlalchemy import delete
# We will import the agent runner later once the agent is built
# from app.agent.graph import run_agent

router = APIRouter()

async def start_agent_processing(bill_id: str, bill_dict: dict):
    # This acts as a placeholder to be replaced when the graph is wired
    from app.agent.graph import build_graph
    graph = build_graph()
    config = {"configurable": {"thread_id": bill_id}}
    await graph.ainvoke({"freight_bill": bill_dict}, config=config)

@router.delete("/reset")
async def reset_transactional_data(
    session: AsyncSession = Depends(get_db_session),
    graph_service: GraphService = Depends(get_graph_service)
):
    """
    Clear all freight bills, decisions, review queues, and audit logs.
    Restores the system to a clean state with only seed data (carriers, contracts, shipments, etc).
    """
    # Postgres Deletions (respecting foreign keys)
    await session.execute(delete(HumanReview))
    await session.execute(delete(ReviewQueue))
    await session.execute(delete(AgentDecisionRecord))
    await session.execute(delete(AuditLog))
    await session.execute(delete(FreightBill))
    await session.commit()
    
    # Neo4j Deletions
    driver = await graph_service.get_driver()
    async with driver.session() as driver_session:
        await driver_session.run("MATCH (fb:FreightBill) DETACH DELETE fb")
        
    return {"message": "All transactional data successfully wiped. Ready for fresh test runs."}

@router.post("/", response_model=FreightBillResponse, status_code=202)
async def ingest_freight_bill(
    bill: FreightBillCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
    graph_service: GraphService = Depends(get_graph_service)
):
    # 1. Check if bill already exists in DB
    result = await session.execute(select(FreightBill).where(FreightBill.id == bill.id))
    existing = result.scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="Freight bill already ingested.")

    # 2. Write FreightBill to Postgres
    bill_date_parsed = datetime.strptime(bill.bill_date, "%Y-%m-%d").date()
    db_bill = FreightBill(
        id=bill.id,
        carrier_id=bill.carrier_id,
        carrier_name=bill.carrier_name,
        bill_number=bill.bill_number,
        bill_date=bill_date_parsed,
        shipment_reference=bill.shipment_reference,
        lane=bill.lane,
        billed_weight_kg=bill.billed_weight_kg,
        rate_per_kg=bill.rate_per_kg,
        billing_unit=bill.billing_unit,
        base_charge=bill.base_charge,
        fuel_surcharge=bill.fuel_surcharge,
        gst_amount=bill.gst_amount,
        total_amount=bill.total_amount,
        processing_status=FreightBillProcessingStatus.ingested
    )
    session.add(db_bill)
    await session.commit()
    
    # 3. Create Node in Neo4j
    await graph_service.create_freight_bill_node(bill.model_dump())
    if bill.shipment_reference:
        await graph_service.link_freight_bill_to_shipment(bill.id, bill.shipment_reference)
    
    # 4. Audit Log
    await log_audit_event(session, "bill_ingested", bill.id, {"bill_number": bill.bill_number})

    # 5. Launch agent in background
    background_tasks.add_task(start_agent_processing, bill.id, bill.model_dump())

    return FreightBillResponse(
        id=bill.id,
        processing_status="ingested",
        message="Freight bill accepted. Processing started."
    )

@router.get("/{id}")
async def get_freight_bill(id: str, session: AsyncSession = Depends(get_db_session)):
    from app.db.models import AgentDecisionRecord
    
    result = await session.execute(select(FreightBill).where(FreightBill.id == id))
    bill = result.scalars().first()
    if not bill:
        raise HTTPException(status_code=404, detail="Freight bill not found")
        
    decision_result = await session.execute(
        select(AgentDecisionRecord).where(AgentDecisionRecord.freight_bill_id == id)
    )
    decision = decision_result.scalars().first()
    
    response = {
        "id": bill.id,
        "carrier_name": bill.carrier_name,
        "lane": bill.lane,
        "total_amount": bill.total_amount,
        "processing_status": bill.processing_status.value
    }
    
    if decision:
        response["decision"] = {
            "decision": decision.decision.value,
            "confidence_score": decision.confidence_score,
            "matched_contract_id": decision.matched_contract_id,
            "matched_shipment_id": decision.matched_shipment_id,
            "validation_results": decision.validation_results,
            "evidence": decision.evidence
        }
        
    return response
