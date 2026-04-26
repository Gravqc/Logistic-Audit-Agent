from pydantic import BaseModel, Field
from typing import Optional
from app.db.models import ReviewDecision

class ReviewSubmit(BaseModel):
    reviewer_decision: ReviewDecision = Field(
        ..., 
        description="The decision made by the reviewer."
    )
    reviewer_notes: Optional[str] = Field(
        default=None, 
        description="Optional notes explaining the decision."
    )
    corrected_amount: Optional[float] = Field(
        default=None, 
        description="The corrected amount. Usually only provided if the decision is 'modify'."
    )

class ReviewSubmitResponse(BaseModel):
    freight_bill_id: str
    reviewer_decision: ReviewDecision
    message: str
