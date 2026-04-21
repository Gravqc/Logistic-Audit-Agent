from app.agent.state import FreightBillState
from app.services.graph_service import GraphService
from app.db.postgres import AsyncSessionLocal
from app.db.models import Shipment, BillOfLading, FreightBill
from sqlalchemy import select

async def run(state: FreightBillState) -> dict:
    freight_bill = state["freight_bill"]
    shipment_ref = freight_bill.get("shipment_reference")
    carrier = state.get("resolved_carrier")
    
    if not carrier:
        return {}
        
    carrier_id = carrier["id"]
    normalized_lane = state["normalized_lane"]
    bill_date = str(freight_bill["bill_date"])
    
    graph_service = GraphService()
    
    matched_shipment = None
    shipment_found_via = "none"
    matched_bols = []
    prior_bills_on_shipment = []
    
    async with AsyncSessionLocal() as session:
        if shipment_ref:
            query = select(Shipment).where(Shipment.id == shipment_ref)
            result = await session.execute(query)
            shipment = result.scalars().first()
            if shipment:
                matched_shipment = {
                    "id": shipment.id,
                    "carrier_id": shipment.carrier_id,
                    "contract_id": shipment.contract_id,
                    "lane": shipment.lane,
                    "shipment_date": str(shipment.shipment_date),
                    "status": shipment.status.value,
                    "total_weight_kg": shipment.total_weight_kg
                }
                shipment_found_via = "reference"
        else:
            candidates = await graph_service.find_shipments_by_carrier_lane_date_window(
                carrier_id, normalized_lane, bill_date
            )
            if len(candidates) == 1:
                shipment_id = candidates[0]["shipment_id"]
                query = select(Shipment).where(Shipment.id == shipment_id)
                result = await session.execute(query)
                shipment = result.scalars().first()
                if shipment:
                    matched_shipment = {
                        "id": shipment.id,
                        "carrier_id": shipment.carrier_id,
                        "contract_id": shipment.contract_id,
                        "lane": shipment.lane,
                        "shipment_date": str(shipment.shipment_date),
                        "status": shipment.status.value,
                        "total_weight_kg": shipment.total_weight_kg
                    }
                    shipment_found_via = "fuzzy"
            elif len(candidates) > 1:
                # multiple candidates
                shipment_found_via = "fuzzy_multiple"
                
        if matched_shipment:
            # Fetch BOLs
            query = select(BillOfLading).where(BillOfLading.shipment_id == matched_shipment["id"])
            result = await session.execute(query)
            for bol in result.scalars():
                matched_bols.append({
                    "id": bol.id,
                    "shipment_id": bol.shipment_id,
                    "delivery_date": str(bol.delivery_date),
                    "actual_weight_kg": bol.actual_weight_kg
                })
                
            # Fetch prior bills via graph
            prior_bills = await graph_service.find_prior_freight_bills_on_shipment(matched_shipment["id"])
            # filter out current bill if it's somehow already linked
            prior_bills = [pb for pb in prior_bills if pb["freight_bill_id"] != freight_bill["id"]]
            
            # Fetch their full records
            if prior_bills:
                pb_ids = [pb["freight_bill_id"] for pb in prior_bills]
                query = select(FreightBill).where(FreightBill.id.in_(pb_ids))
                result = await session.execute(query)
                for fb in result.scalars():
                    prior_bills_on_shipment.append({
                        "id": fb.id,
                        "billed_weight_kg": fb.billed_weight_kg,
                        "total_amount": fb.total_amount
                    })
                    
    return {
        "matched_shipment": matched_shipment,
        "matched_bols": matched_bols,
        "shipment_found_via": shipment_found_via,
        "prior_bills_on_shipment": prior_bills_on_shipment
    }
