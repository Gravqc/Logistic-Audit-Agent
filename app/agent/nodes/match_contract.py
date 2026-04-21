from app.agent.state import FreightBillState
from app.services.graph_service import GraphService
from app.db.postgres import AsyncSessionLocal
from app.db.models import CarrierContract, ContractRateCard, Shipment
from sqlalchemy import select
from sqlalchemy.orm import selectinload

async def run(state: FreightBillState) -> dict:
    carrier = state.get("resolved_carrier")
    if not carrier:
        return {} # Escalation handled earlier

    carrier_id = carrier["id"]
    normalized_lane = state["normalized_lane"]
    bill_date = state["freight_bill"]["bill_date"]
    
    # Initialize graph service
    graph_service = GraphService()
    
    # 1. Query Graph for contracts
    candidate_contract_records = await graph_service.find_contracts_for_carrier_lane_date(
        carrier_id, normalized_lane, str(bill_date)
    )
    candidate_ids = [c["contract_id"] for c in candidate_contract_records]
    
    # 2. Fetch full contract + rate_card rows from Postgres for each candidate
    candidate_contracts = []
    async with AsyncSessionLocal() as session:
        if candidate_ids:
            query = select(CarrierContract).options(selectinload(CarrierContract.rate_cards)).where(CarrierContract.id.in_(candidate_ids))
            result = await session.execute(query)
            for contract in result.scalars():
                c_dict = {
                    "id": contract.id,
                    "effective_date": str(contract.effective_date),
                    "expiry_date": str(contract.expiry_date),
                    "status": contract.status.value,
                    "rate_cards": [
                        {
                            "id": rc.id,
                            "lane": rc.lane,
                            "rate_per_kg": rc.rate_per_kg,
                            "rate_per_unit": rc.rate_per_unit,
                            "unit": rc.unit,
                            "unit_capacity_kg": rc.unit_capacity_kg,
                            "alternate_rate_per_kg": rc.alternate_rate_per_kg,
                            "min_charge": rc.min_charge,
                            "fuel_surcharge_percent": rc.fuel_surcharge_percent,
                            "revised_on": str(rc.revised_on) if rc.revised_on else None,
                            "revised_fuel_surcharge_percent": rc.revised_fuel_surcharge_percent
                        } for rc in contract.rate_cards if rc.lane == normalized_lane
                    ]
                }
                candidate_contracts.append(c_dict)
                
        # 3. Resolution logic
        if len(candidate_contracts) == 0:
            # CASE A - Zero candidates
            expired = await graph_service.find_expired_contracts_for_lane(carrier_id, normalized_lane)
            if expired:
                exp = expired[0]
                return {
                    "should_escalate": True,
                    "flag_reason": f"Bill date falls after contract expiry. Nearest expired contract: {exp['contract_id']}, expired {exp['expired_on']}. Newer contract exists with different pricing. Human review required."
                }
            else:
                return {
                    "should_escalate": True,
                    "flag_reason": "No commercial relationship on this lane."
                }
                
        elif len(candidate_contracts) == 1:
            # CASE B - One candidate
            contract = candidate_contracts[0]
            rate_card = contract["rate_cards"][0] if contract["rate_cards"] else None
            return {
                "matched_contract": contract,
                "matched_rate_card": rate_card,
                "candidate_contracts": candidate_contracts,
                "contract_ambiguous": False
            }
            
        else:
            # CASE C - Multiple candidates
            shipment_ref = state["freight_bill"].get("shipment_reference")
            if shipment_ref:
                query = select(Shipment).where(Shipment.id == shipment_ref)
                result = await session.execute(query)
                shipment = result.scalars().first()
                if shipment:
                    matched = next((c for c in candidate_contracts if c["id"] == shipment.contract_id), None)
                    if matched:
                        rate_card = matched["rate_cards"][0] if matched["rate_cards"] else None
                        return {
                            "matched_contract": matched,
                            "matched_rate_card": rate_card,
                            "candidate_contracts": candidate_contracts,
                            "contract_ambiguous": False
                        }
            
            # If billed rate_per_kg matches exactly one candidate's rate_per_kg
            billed_rate = state["freight_bill"].get("rate_per_kg")
            if billed_rate is not None:
                matches = []
                for c in candidate_contracts:
                    for rc in c.get("rate_cards", []):
                        if rc.get("rate_per_kg") == billed_rate:
                            matches.append(c)
                if len(matches) == 1:
                    matched = matches[0]
                    rate_card = matched["rate_cards"][0] if matched["rate_cards"] else None
                    return {
                        "matched_contract": matched,
                        "matched_rate_card": rate_card,
                        "candidate_contracts": candidate_contracts,
                        "contract_ambiguous": False # Treated as false but penalty handled in score
                    }

            # Ambiguous
            return {
                "contract_ambiguous": True,
                "candidate_contracts": candidate_contracts,
                "flag_reason": f"Multiple overlapping contracts on {normalized_lane}. Cannot resolve without shipment reference. Human review required."
            }
