from pydantic import BaseModel
from typing import List, Optional

class ValidationResultSchema(BaseModel):
    check: str
    passed: bool
    detail: str
    severity: str

class DecisionResponse(BaseModel):
    decision: str
    confidence_score: float
    matched_contract_id: Optional[str] = None
    matched_shipment_id: Optional[str] = None
    validation_results: List[ValidationResultSchema] = []
    evidence: Optional[str] = None
