from app.agent.state import FreightBillState
from app.db.postgres import AsyncSessionLocal
from app.db.models import Carrier
from sqlalchemy import select

async def run(state: FreightBillState) -> dict:
    normalized_carrier_name = state.get("normalized_carrier_name")
    
    if normalized_carrier_name == "UNKNOWN":
        return {
            "should_escalate": True,
            "flag_reason": "Unknown carrier — no record or contract exists. Manual review required."
        }
        
    async with AsyncSessionLocal() as session:
        query = select(Carrier).where(Carrier.name == normalized_carrier_name)
        result = await session.execute(query)
        carrier = result.scalars().first()
        
        if carrier:
            # Return carrier as dict
            carrier_dict = {
                "id": carrier.id,
                "name": carrier.name,
                "carrier_code": carrier.carrier_code,
                "status": carrier.status.value
            }
            return {
                "resolved_carrier": carrier_dict,
                "should_escalate": False
            }
        else:
            return {
                "should_escalate": True,
                "flag_reason": "Unknown carrier — no record or contract exists. Manual review required."
            }
