from typing import TypedDict, Optional, Any, List

class ValidationResult(TypedDict):
    check: str
    passed: bool
    detail: str
    severity: str   # "pass" | "warning" | "fail"

class FreightBillState(TypedDict):
    # Input — raw freight bill dict as received from API
    freight_bill: dict

    # Normalisation output
    normalized_carrier_name: Optional[str]
    normalized_lane: Optional[str]

    # Carrier resolution
    resolved_carrier: Optional[dict]

    # Contract matching
    candidate_contracts: List[dict]
    matched_contract: Optional[dict]
    matched_rate_card: Optional[dict]
    contract_ambiguous: bool

    # Shipment + BOL
    matched_shipment: Optional[dict]
    matched_bols: List[dict]
    prior_bills_on_shipment: List[dict]
    shipment_found_via: Optional[str]   # "reference" | "fuzzy" | "none"

    # Validation
    validation_results: List[ValidationResult]

    # Scoring + decision
    confidence_score: Optional[float]
    decision: Optional[str]
    flag_reason: Optional[str]

    # Evidence
    evidence: Optional[str]

    # Human review (injected on resume)
    human_review: Optional[dict]

    # Internal routing flags
    should_escalate: bool
    is_duplicate: bool
