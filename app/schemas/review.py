from pydantic import BaseModel
from typing import Optional

class ReviewSubmit(BaseModel):
    reviewer_decision: str # "approve", "dispute", "modify"
    reviewer_notes: Optional[str] = None
    corrected_amount: Optional[float] = None

class ReviewSubmitResponse(BaseModel):
    freight_bill_id: str
    reviewer_decision: str
    message: str
