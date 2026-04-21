import uuid
from datetime import datetime, date
from sqlalchemy import (
    String, Integer, Float, Boolean, Date, DateTime,
    ForeignKey, Text, JSON, Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

class Base(DeclarativeBase):
    pass

# ── Enums ────────────────────────────────────────────────────────────────────

class CarrierStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"

class ContractStatus(str, enum.Enum):
    active = "active"
    expired = "expired"

class ShipmentStatus(str, enum.Enum):
    in_transit = "in_transit"
    partially_delivered = "partially_delivered"
    delivered = "delivered"

class FreightBillProcessingStatus(str, enum.Enum):
    ingested = "ingested"
    processing = "processing"
    awaiting_review = "awaiting_review"
    completed = "completed"
    escalated = "escalated"

class AgentDecision(str, enum.Enum):
    auto_approve = "auto_approve"
    flag_for_review = "flag_for_review"
    dispute = "dispute"
    escalate = "escalate"

class ReviewDecision(str, enum.Enum):
    approve = "approve"
    dispute = "dispute"
    modify = "modify"

class ReviewQueueStatus(str, enum.Enum):
    pending = "pending"
    reviewed = "reviewed"

# ── Reference / Config Tables ────────────────────────────────────────────────

class Carrier(Base):
    __tablename__ = "carriers"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    carrier_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    gstin: Mapped[str] = mapped_column(String(20), nullable=True)
    bank_account: Mapped[str] = mapped_column(String(100), nullable=True)
    status: Mapped[CarrierStatus] = mapped_column(SAEnum(CarrierStatus), default=CarrierStatus.active)
    onboarded_on: Mapped[date] = mapped_column(Date, nullable=True)

    contracts: Mapped[list["CarrierContract"]] = relationship(back_populates="carrier")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="carrier")


class CarrierContract(Base):
    __tablename__ = "carrier_contracts"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    carrier_id: Mapped[str] = mapped_column(ForeignKey("carriers.id"), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ContractStatus] = mapped_column(SAEnum(ContractStatus), default=ContractStatus.active)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    carrier: Mapped["Carrier"] = relationship(back_populates="contracts")
    rate_cards: Mapped[list["ContractRateCard"]] = relationship(back_populates="contract")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="contract")


class ContractRateCard(Base):
    """
    One row per lane per contract.
    Supports both per-kg and FTL billing models.
    Supports mid-term fuel surcharge revisions.
    """
    __tablename__ = "contract_rate_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("carrier_contracts.id"), nullable=False)
    lane: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=True)

    # Per-kg billing
    rate_per_kg: Mapped[float] = mapped_column(Float, nullable=True)

    # FTL billing
    rate_per_unit: Mapped[float] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=True)        # e.g. "FTL"
    unit_capacity_kg: Mapped[int] = mapped_column(Integer, nullable=True)
    alternate_rate_per_kg: Mapped[float] = mapped_column(Float, nullable=True)

    min_charge: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_surcharge_percent: Mapped[float] = mapped_column(Float, nullable=False)

    # Mid-term revision
    revised_on: Mapped[date] = mapped_column(Date, nullable=True)
    revised_fuel_surcharge_percent: Mapped[float] = mapped_column(Float, nullable=True)

    contract: Mapped["CarrierContract"] = relationship(back_populates="rate_cards")


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    carrier_id: Mapped[str] = mapped_column(ForeignKey("carriers.id"), nullable=False)
    contract_id: Mapped[str] = mapped_column(ForeignKey("carrier_contracts.id"), nullable=False)
    lane: Mapped[str] = mapped_column(String(50), nullable=False)
    shipment_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ShipmentStatus] = mapped_column(SAEnum(ShipmentStatus))
    total_weight_kg: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    carrier: Mapped["Carrier"] = relationship(back_populates="shipments")
    contract: Mapped["CarrierContract"] = relationship(back_populates="shipments")
    bols: Mapped[list["BillOfLading"]] = relationship(back_populates="shipment")
    freight_bills: Mapped[list["FreightBill"]] = relationship(back_populates="shipment")


class BillOfLading(Base):
    __tablename__ = "bills_of_lading"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_weight_kg: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    shipment: Mapped["Shipment"] = relationship(back_populates="bols")


# ── Transactional Tables ─────────────────────────────────────────────────────

class FreightBill(Base):
    __tablename__ = "freight_bills"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    carrier_id: Mapped[str] = mapped_column(ForeignKey("carriers.id"), nullable=True)
    carrier_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bill_number: Mapped[str] = mapped_column(String(100), nullable=False)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    shipment_reference: Mapped[str] = mapped_column(ForeignKey("shipments.id"), nullable=True)
    lane: Mapped[str] = mapped_column(String(50), nullable=False)
    billed_weight_kg: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_per_kg: Mapped[float] = mapped_column(Float, nullable=True)
    billing_unit: Mapped[str] = mapped_column(String(20), nullable=True)
    base_charge: Mapped[float] = mapped_column(Float, nullable=False)
    fuel_surcharge: Mapped[float] = mapped_column(Float, nullable=False)
    gst_amount: Mapped[float] = mapped_column(Float, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    processing_status: Mapped[FreightBillProcessingStatus] = mapped_column(
        SAEnum(FreightBillProcessingStatus),
        default=FreightBillProcessingStatus.ingested
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    shipment: Mapped["Shipment"] = relationship(back_populates="freight_bills")
    decision: Mapped["AgentDecisionRecord"] = relationship(back_populates="freight_bill", uselist=False)
    review_queue_entry: Mapped["ReviewQueue"] = relationship(back_populates="freight_bill", uselist=False)


class AgentDecisionRecord(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    freight_bill_id: Mapped[str] = mapped_column(ForeignKey("freight_bills.id"), nullable=False, unique=True)
    matched_contract_id: Mapped[str] = mapped_column(ForeignKey("carrier_contracts.id"), nullable=True)
    matched_shipment_id: Mapped[str] = mapped_column(ForeignKey("shipments.id"), nullable=True)
    matched_bol_ids: Mapped[list] = mapped_column(JSON, nullable=True)   # list of BOL ids
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    decision: Mapped[AgentDecision] = mapped_column(SAEnum(AgentDecision), nullable=False)
    validation_results: Mapped[dict] = mapped_column(JSON, nullable=False)   # full check results
    evidence: Mapped[str] = mapped_column(Text, nullable=True)               # LLM-generated explanation
    flag_reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    freight_bill: Mapped["FreightBill"] = relationship(back_populates="decision")


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    freight_bill_id: Mapped[str] = mapped_column(ForeignKey("freight_bills.id"), nullable=False, unique=True)
    agent_state: Mapped[dict] = mapped_column(JSON, nullable=False)   # full LangGraph state checkpoint
    flag_reason: Mapped[str] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)
    evidence: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[ReviewQueueStatus] = mapped_column(
        SAEnum(ReviewQueueStatus),
        default=ReviewQueueStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    freight_bill: Mapped["FreightBill"] = relationship(back_populates="review_queue_entry")
    human_review: Mapped["HumanReview"] = relationship(back_populates="queue_entry", uselist=False)


class HumanReview(Base):
    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_queue_id: Mapped[int] = mapped_column(ForeignKey("review_queue.id"), nullable=False)
    freight_bill_id: Mapped[str] = mapped_column(ForeignKey("freight_bills.id"), nullable=False)
    reviewer_decision: Mapped[ReviewDecision] = mapped_column(SAEnum(ReviewDecision), nullable=False)
    reviewer_notes: Mapped[str] = mapped_column(Text, nullable=True)
    corrected_amount: Mapped[float] = mapped_column(Float, nullable=True)   # used if decision = "modify"
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    queue_entry: Mapped["ReviewQueue"] = relationship(back_populates="human_review")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    freight_bill_id: Mapped[str] = mapped_column(String(50), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_detail: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
