from app.agent.state import FreightBillState
from app.services.ai_client import get_ai_client
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
    # In a real system, these might come from the DB, but using a static list for demo
    known_names = [
        "Safexpress Logistics", 
        "Delhivery Freight", 
        "TCI Express", 
        "Blue Dart Aviation", 
        "VRL Logistics"
    ]
    
    system_prompt = (
        "You are a carrier name normaliser for an Indian logistics system.\n"
        "Given a raw carrier name and a list of known carrier names, return ONLY the best\n"
        "matching known carrier name. If no match exists, return 'UNKNOWN'. No explanation."
    )
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
