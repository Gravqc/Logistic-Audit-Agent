from app.agent.state import FreightBillState
from app.services.ai_client import get_ai_client
import json

async def run(state: FreightBillState) -> dict:
    freight_bill = state.get("freight_bill", {})
    decision = state.get("decision", "unknown")
    score = state.get("confidence_score", 0.0)
    validation_results = state.get("validation_results", [])
    
    context = {
        "freight_bill_summary": {
            "id": freight_bill.get("id"),
            "carrier_name": freight_bill.get("carrier_name"),
            "lane": freight_bill.get("lane"),
            "billed_weight_kg": freight_bill.get("billed_weight_kg"),
            "total_amount": freight_bill.get("total_amount")
        },
        "decision": decision,
        "confidence_score": score,
        "matched_contract": state.get("matched_contract", {}).get("id") if state.get("matched_contract") else "None",
        "matched_shipment": state.get("matched_shipment", {}).get("id") if state.get("matched_shipment") else "None",
        "validation_summary": [
            f"{r['check']} - {r['severity'].upper()}: {r['detail']}" for r in validation_results
        ]
    }
    
    context_str = json.dumps(context, indent=2)
    
    system_prompt = (
        "You are an audit assistant for a logistics operations team. Your job is to write\n"
        "clear, concise explanations of freight bill validation decisions for human reviewers.\n"
        "Write in plain English. Be specific about numbers. State what matched, what failed,\n"
        "and why the decision was made. Do not use jargon. Keep it under 200 words."
    )
    
    ai_client = get_ai_client()
    try:
        evidence = await ai_client.complete(system_prompt, context_str)
    except Exception as e:
        pass_count = sum(1 for r in validation_results if r["severity"] == "pass")
        warn_count = sum(1 for r in validation_results if r["severity"] == "warning")
        fail_count = sum(1 for r in validation_results if r["severity"] == "fail")
        first_fail = next((r["detail"] for r in validation_results if r["severity"] == "fail"), "None")
        
        evidence = (
            f"Bill {freight_bill.get('id')}: {decision} (confidence {score}%). "
            f"Checks: {pass_count} passed, {warn_count} warnings, {fail_count} failed. "
            f"Primary concern: {first_fail}"
        )
        
    return {"evidence": evidence}
