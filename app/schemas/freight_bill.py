from pydantic import BaseModel
from typing import Optional

class FreightBillCreate(BaseModel):
    id: str
    carrier_id: Optional[str] = None
    carrier_name: str
    bill_number: str
    bill_date: str
    shipment_reference: Optional[str] = None
    lane: str
    billed_weight_kg: int
    rate_per_kg: Optional[float] = None
    billing_unit: Optional[str] = None
    base_charge: float
    fuel_surcharge: float
    gst_amount: float
    total_amount: float

class FreightBillResponse(BaseModel):
    id: str
    processing_status: str
    message: str
