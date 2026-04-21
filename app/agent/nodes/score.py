from app.agent.state import FreightBillState
from app.config import get_settings

async def run(state: FreightBillState) -> dict:
    score = 100.0
    
    settings = get_settings()
    
    # Contract matching
    if state.get("contract_ambiguous"):
        score -= 25.0
    elif not state.get("matched_contract"):
        # No contract or expired logic
        flag_reason = state.get("flag_reason", "")
        if "expired" in flag_reason.lower():
            score -= 30.0
        else:
            score -= 40.0
    else:
        # We matched uniquely or via rate heuristic
        # If there are multiple candidates and we are not ambiguous, it was a rate heuristic
        candidates = state.get("candidate_contracts", [])
        if len(candidates) > 1 and not state.get("contract_ambiguous") and not state.get("freight_bill", {}).get("shipment_reference"):
            score -= 10.0

    # Shipment matching
    shipment_found_via = state.get("shipment_found_via", "none")
    if shipment_found_via == "inferred":
        score -= 10.0
    elif shipment_found_via == "inferred_multiple":
        score -= 15.0
    elif shipment_found_via == "none":
        score -= 20.0
        
    # Validation results
    for res in state.get("validation_results", []):
        if res["severity"] == "warning":
            score -= 10.0
        elif res["severity"] == "fail":
            score -= 25.0
            
    # Hard overrides
    if state.get("is_duplicate"):
        score = 0.0
        
    score = max(0.0, score)
    
    # Decision mapping
    decision = "auto_approve"
    if score >= settings.CONFIDENCE_AUTO_APPROVE_THRESHOLD:
        decision = "auto_approve"
    elif score >= settings.CONFIDENCE_DISPUTE_THRESHOLD:
        decision = "flag_for_review"
    else:
        decision = "dispute"
        
    if state.get("should_escalate"):
        decision = "escalate"
    if state.get("is_duplicate"):
        decision = "dispute"
        
    return {
        "confidence_score": score,
        "decision": decision
    }
