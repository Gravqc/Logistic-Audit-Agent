from app.agent.state import FreightBillState
from app.services.ai_client import get_ai_client
from app.db.postgres import AsyncSessionLocal
from app.db.models import Carrier
from sqlalchemy import select
from app.prompts.normalize import NORMALIZE_PROMPT
from datetime import datetime

async def run(state: FreightBillState) -> dict:
    freight_bill = state["freight_bill"]
    
    # 1. Uppercase and strip lane code
    normalized_lane = freight_bill.get("lane", "").strip().upper()
    
    # 2. Parse bill_date to ISO string if not already
    bill_date = freight_bill.get("bill_date")
    if isinstance(bill_date, str) == False:
        bill_date = str(bill_date)
        
    # 3. Numeric fields are already handled by Pydantic validation on ingest
    
    # 4. Call AI to normalise carrier name
    raw_name = freight_bill.get("carrier_name", "")
    
    # Query database for all known carrier names
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Carrier.name))
        known_names = result.scalars().all()
    
    system_prompt = NORMALIZE_PROMPT
    
    user_prompt = f"Raw name: {raw_name}\nKnown carriers: {known_names}"
    
    ai_client = get_ai_client()
    try:
        normalized_name = await ai_client.complete(system_prompt, user_prompt)
        normalized_name = normalized_name.strip()
        if normalized_name not in known_names and normalized_name != "UNKNOWN":
            normalized_name = "UNKNOWN"
    except Exception:
        normalized_name = "UNKNOWN"

    return {
        "normalized_carrier_name": normalized_name,
        "normalized_lane": normalized_lane
    }
