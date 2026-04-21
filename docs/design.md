# Freight Bill Processing System — Full Design Document

## Overview

This document is the authoritative design specification for the freight bill processing system.
It is intended to be used as a complete reference for implementation. Every architectural
decision, schema, agent behaviour, API contract, and file structure is specified here.

The system ingests carrier freight bills, validates them against contracted rates and delivery
records, produces a decision (auto-approve / flag / dispute), and supports human-in-the-loop
review for ambiguous cases. It is built with FastAPI, LangGraph, PostgreSQL, and Neo4j.

---

## Technology Stack

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | LangGraph, SQLAlchemy ecosystem |
| Package management | Poetry | Lockfile, dependency groups, virtualenv management |
| API framework | FastAPI | Async, typed, auto-docs |
| Relational DB | PostgreSQL 15 | ACID, complex joins, financial audit trail |
| Graph DB | Neo4j 5 | Entity relationship traversal for contract/lane/shipment graph |
| ORM | SQLAlchemy 2.x (async) | Typed models, async sessions |
| Agent framework | LangGraph | Stateful agent with interrupt/resume support |
| LLM abstraction | Custom AIClient wrapper | Model-agnostic, swap provider via config |
| Migrations | Alembic | Schema versioning |
| Containerisation | Docker + docker-compose | Local dev parity |
| Config | Pydantic Settings | Env var parsing, validation |

---

## Project Structure

```
logistic-audit-agent/
├── pyproject.toml
├── poetry.lock
├── .env
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── DESIGN.md
├── README.md
│
├── alembic/
│   └── versions/
│
├── data/
│   └── seed_data_logistics.json
│
├── scripts/
│   └── seed_loader.py          # Standalone script — loads JSON into Postgres + Neo4j
│
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Pydantic settings — all env vars
│   ├── dependencies.py         # FastAPI dependency injection (DB sessions, etc.)
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── postgres.py         # Async SQLAlchemy engine + session factory
│   │   ├── neo4j.py            # Neo4j driver singleton
│   │   └── models.py           # All SQLAlchemy ORM models
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── freight_bill.py     # Pydantic request/response schemas
│   │   ├── review.py
│   │   └── decision.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── freight_bills.py
│   │       └── reviews.py
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── graph.py            # LangGraph graph definition — nodes + edges
│   │   ├── state.py            # FreightBillState TypedDict
│   │   ├── nodes/
│   │   │   ├── __init__.py
│   │   │   ├── normalize.py
│   │   │   ├── resolve_carrier.py
│   │   │   ├── match_contract.py
│   │   │   ├── find_shipment.py
│   │   │   ├── validate.py
│   │   │   ├── score.py
│   │   │   ├── generate_evidence.py
│   │   │   └── decide.py
│   │   └── tools/
│   │       ├── __init__.py
│   │       └── llm_tools.py    # LLM-callable tools (normalize name, generate explanation)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ai_client.py        # Model-agnostic LLM wrapper
│   │   ├── graph_service.py    # All Neo4j query functions
│   │   └── validation.py       # Pure deterministic validation functions
│   │
│   └── core/
│       ├── __init__.py
│       └── audit.py            # Audit log writer
│
└── tests/
    ├── __init__.py
    ├── test_validation.py
    └── test_agent_decisions.py
```

---

## Configuration — `app/config.py`

All configuration comes from environment variables, parsed by Pydantic Settings.
Never hardcode secrets. Provide `.env.example` with all keys and placeholder values.

```python
# app/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # PostgreSQL
    POSTGRES_URL: str  # e.g. postgresql+asyncpg://user:pass@localhost:5432/freight

    # Neo4j
    NEO4J_URI: str       # e.g. bolt://localhost:7687
    NEO4J_USER: str
    NEO4J_PASSWORD: str

    # LLM — provider-agnostic
    LLM_PROVIDER: str = "anthropic"   # "anthropic" | "openai" | "google"
    LLM_MODEL: str = "claude-3-5-haiku-20241022"
    LLM_API_KEY: str

    # Agent behaviour
    CONFIDENCE_AUTO_APPROVE_THRESHOLD: float = 80.0
    CONFIDENCE_DISPUTE_THRESHOLD: float = 50.0
    RATE_DRIFT_TOLERANCE_PERCENT: float = 2.0

    class Config:
        env_file = ".env"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## LLM Abstraction — `app/services/ai_client.py`

The LLM is used in exactly two places: carrier name normalisation and evidence generation.
The `AIClient` wrapper keeps the rest of the codebase decoupled from any specific provider.
Switching from Claude to GPT-4o or Gemini requires changing two env vars only.

```python
# app/services/ai_client.py

from abc import ABC, abstractmethod
from app.config import get_settings

class BaseAIClient(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt, return a string response."""
        ...

class AnthropicClient(BaseAIClient):
    def __init__(self):
        import anthropic
        settings = get_settings()
        self._client = anthropic.AsyncAnthropic(api_key=settings.LLM_API_KEY)
        self._model = settings.LLM_MODEL

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return message.content[0].text

class OpenAIClient(BaseAIClient):
    def __init__(self):
        from openai import AsyncOpenAI
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.LLM_API_KEY)
        self._model = settings.LLM_MODEL

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        return response.choices[0].message.content

class GoogleClient(BaseAIClient):
    def __init__(self):
        import google.generativeai as genai
        settings = get_settings()
        genai.configure(api_key=settings.LLM_API_KEY)
        self._model = genai.GenerativeModel(settings.LLM_MODEL)

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._model.generate_content_async(
            f"{system_prompt}\n\n{user_prompt}"
        )
        return response.text

def get_ai_client() -> BaseAIClient:
    """Factory — returns the correct client based on LLM_PROVIDER env var."""
    provider = get_settings().LLM_PROVIDER
    clients = {
        "anthropic": AnthropicClient,
        "openai": OpenAIClient,
        "google": GoogleClient,
    }
    if provider not in clients:
        raise ValueError(f"Unknown LLM provider: {provider}. Choose from {list(clients.keys())}")
    return clients[provider]()
```

---

## PostgreSQL Schema — `app/db/models.py`

All tables with their columns, types, constraints, and relationships.

```python
# app/db/models.py

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

    __table_args__ = (
        # Soft uniqueness — same bill_number + carrier_id is a duplicate candidate
        # Not a hard constraint because the duplicate check is the agent's job
    )


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
```

---

## Neo4j Graph Model — `app/services/graph_service.py`

### Node Labels and Properties

```
(:Carrier)
  id, name, carrier_code

(:Contract)
  id, carrier_id, effective_date, expiry_date, status

(:Lane)
  code                          ← e.g. "DEL-BOM"

(:Shipment)
  id, carrier_id, contract_id, lane, shipment_date, total_weight_kg

(:BOL)
  id, shipment_id, delivery_date, actual_weight_kg

(:FreightBill)
  id, bill_number, carrier_name, bill_date, lane, billed_weight_kg
```

### Relationship Types

```
(Carrier)-[:HAS_CONTRACT]->(Contract)
(Contract)-[:COVERS_LANE]->(Lane)
(Shipment)-[:ON_LANE]->(Lane)
(Shipment)-[:UNDER_CONTRACT]->(Contract)
(Shipment)-[:CARRIED_BY]->(Carrier)
(BOL)-[:PROVES_DELIVERY_FOR]->(Shipment)
(FreightBill)-[:SUBMITTED_BY]->(Carrier)         ← created when bill ingested
(FreightBill)-[:REFERENCES]->(Shipment)          ← created if shipment_reference exists
(FreightBill)-[:MATCHED_TO]->(Contract)          ← created after agent decides
```

### Key Traversal Queries

The `graph_service.py` module exposes these functions. All return plain dicts for portability.

```python
# app/services/graph_service.py

from neo4j import AsyncGraphDatabase
from app.config import get_settings

class GraphService:
    def __init__(self):
        settings = get_settings()
        self._driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )

    async def close(self):
        await self._driver.close()

    async def find_contracts_for_carrier_lane_date(
        self,
        carrier_id: str,
        lane: str,
        bill_date: str       # ISO format YYYY-MM-DD
    ) -> list[dict]:
        """
        Core contract matching query.
        Returns all active contracts for a carrier/lane combination
        where the bill_date falls within the contract's validity window.
        """
        query = """
        MATCH (c:Carrier {id: $carrier_id})
              -[:HAS_CONTRACT]->(contract:Contract)
              -[:COVERS_LANE]->(lane:Lane {code: $lane})
        WHERE contract.effective_date <= $bill_date
          AND contract.expiry_date >= $bill_date
          AND contract.status = 'active'
        RETURN contract.id AS contract_id
        """
        async with self._driver.session() as session:
            result = await session.run(query, carrier_id=carrier_id, lane=lane, bill_date=bill_date)
            return [record.data() async for record in result]

    async def find_expired_contracts_for_lane(self, carrier_id: str, lane: str) -> list[dict]:
        """Used to detect expired-contract billing scenario."""
        query = """
        MATCH (c:Carrier {id: $carrier_id})
              -[:HAS_CONTRACT]->(contract:Contract)
              -[:COVERS_LANE]->(lane:Lane {code: $lane})
        WHERE contract.status = 'expired'
        RETURN contract.id AS contract_id, contract.expiry_date AS expired_on
        """
        async with self._driver.session() as session:
            result = await session.run(query, carrier_id=carrier_id, lane=lane)
            return [record.data() async for record in result]

    async def find_prior_freight_bills_on_shipment(self, shipment_id: str) -> list[dict]:
        """
        Find all freight bills already submitted against a shipment.
        Critical for cumulative over-billing check.
        """
        query = """
        MATCH (fb:FreightBill)-[:REFERENCES]->(s:Shipment {id: $shipment_id})
        RETURN fb.id AS freight_bill_id, fb.billed_weight_kg AS billed_weight_kg
        """
        async with self._driver.session() as session:
            result = await session.run(query, shipment_id=shipment_id)
            return [record.data() async for record in result]

    async def find_shipments_by_carrier_lane_date_window(
        self,
        carrier_id: str,
        lane: str,
        bill_date: str,
        window_days: int = 30
    ) -> list[dict]:
        """
        Fuzzy shipment lookup when freight bill has no shipment_reference.
        Searches within a date window around the bill date.
        """
        query = """
        MATCH (c:Carrier {id: $carrier_id})
              -[:CARRIED_BY]-(s:Shipment)
              -[:ON_LANE]->(l:Lane {code: $lane})
        WHERE abs(duration.between(date(s.shipment_date), date($bill_date)).days) <= $window_days
        RETURN s.id AS shipment_id, s.shipment_date AS shipment_date,
               s.total_weight_kg AS total_weight_kg
        ORDER BY abs(duration.between(date(s.shipment_date), date($bill_date)).days)
        """
        async with self._driver.session() as session:
            result = await session.run(
                query, carrier_id=carrier_id, lane=lane,
                bill_date=bill_date, window_days=window_days
            )
            return [record.data() async for record in result]

    async def create_freight_bill_node(self, bill: dict):
        """Called when a freight bill is ingested via the API."""
        query = """
        MERGE (fb:FreightBill {id: $id})
        SET fb.bill_number = $bill_number,
            fb.carrier_name = $carrier_name,
            fb.bill_date = $bill_date,
            fb.lane = $lane,
            fb.billed_weight_kg = $billed_weight_kg
        WITH fb
        OPTIONAL MATCH (c:Carrier {id: $carrier_id})
        FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
            MERGE (fb)-[:SUBMITTED_BY]->(c)
        )
        """
        async with self._driver.session() as session:
            await session.run(query, **bill)

    async def link_freight_bill_to_shipment(self, bill_id: str, shipment_id: str):
        query = """
        MATCH (fb:FreightBill {id: $bill_id})
        MATCH (s:Shipment {id: $shipment_id})
        MERGE (fb)-[:REFERENCES]->(s)
        """
        async with self._driver.session() as session:
            await session.run(query, bill_id=bill_id, shipment_id=shipment_id)

    async def link_freight_bill_to_contract(self, bill_id: str, contract_id: str):
        query = """
        MATCH (fb:FreightBill {id: $bill_id})
        MATCH (contract:Contract {id: $contract_id})
        MERGE (fb)-[:MATCHED_TO]->(contract)
        """
        async with self._driver.session() as session:
            await session.run(query, bill_id=bill_id, contract_id=contract_id)
```

---

## Seed Loader — `scripts/seed_loader.py`

Standalone script. Run once at startup. Idempotent — safe to run multiple times.
Loads all entities except freight_bills. Order is important for Postgres FK constraints.

```python
# scripts/seed_loader.py
"""
Usage:
    poetry run python scripts/seed_loader.py --data data/seed_data_logistics.json

Loads carriers, contracts, rate_cards, shipments, BOLs into:
  - PostgreSQL (via SQLAlchemy)
  - Neo4j     (via graph_service)

Does NOT load freight_bills — those arrive via the API.
"""

import asyncio
import json
import argparse
from pathlib import Path

# Load carriers first, then contracts (FK: carrier_id),
# then rate_cards (FK: contract_id), then shipments (FK: carrier_id, contract_id),
# then BOLs (FK: shipment_id).

# For Neo4j:
# - MERGE on all node creates (idempotent)
# - Create relationships after both endpoint nodes exist

# Key implementation notes:
# 1. Parse dates from ISO strings to Python date objects before inserting
# 2. Lane nodes in Neo4j: use MERGE not CREATE to avoid duplicates
#    (multiple contracts may cover the same lane)
# 3. Log counts before and after for verification
# 4. Wrap each entity type in a try/except so one bad record doesn't abort the whole load
# 5. Print a summary table at the end showing counts in both DBs

async def main(data_path: str):
    data = json.loads(Path(data_path).read_text())

    await load_carriers(data["carriers"])
    await load_contracts(data["carrier_contracts"])
    await load_rate_cards(data["carrier_contracts"])   # rate_cards are nested in contracts in JSON
    await load_shipments(data["shipments"])
    await load_bols(data["bills_of_lading"])

    print_verification_summary()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.data))
```

---

## Agent State — `app/agent/state.py`

```python
# app/agent/state.py

from typing import TypedDict, Optional, Any

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
    candidate_contracts: list[dict]
    matched_contract: Optional[dict]
    matched_rate_card: Optional[dict]
    contract_ambiguous: bool

    # Shipment + BOL
    matched_shipment: Optional[dict]
    matched_bols: list[dict]
    prior_bills_on_shipment: list[dict]
    shipment_found_via: Optional[str]   # "reference" | "fuzzy" | "none"

    # Validation
    validation_results: list[ValidationResult]

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
```

---

## Agent Graph — `app/agent/graph.py`

```python
# app/agent/graph.py

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver   # swap for Postgres checkpointer in prod
from app.agent.state import FreightBillState
from app.agent.nodes import (
    normalize,
    resolve_carrier,
    match_contract,
    find_shipment,
    validate,
    score,
    generate_evidence,
    decide,
)

def build_graph() -> StateGraph:
    graph = StateGraph(FreightBillState)

    # Register nodes
    graph.add_node("normalize", normalize.run)
    graph.add_node("resolve_carrier", resolve_carrier.run)
    graph.add_node("match_contract", match_contract.run)
    graph.add_node("find_shipment", find_shipment.run)
    graph.add_node("validate", validate.run)
    graph.add_node("score", score.run)
    graph.add_node("generate_evidence", generate_evidence.run)
    graph.add_node("decide", decide.run)

    # Entry point
    graph.set_entry_point("normalize")

    # Linear edges
    graph.add_edge("normalize", "resolve_carrier")

    # Conditional: if carrier not found, skip to decide (will escalate)
    graph.add_conditional_edges(
        "resolve_carrier",
        lambda state: "decide" if state["should_escalate"] else "match_contract"
    )

    graph.add_edge("match_contract", "find_shipment")
    graph.add_edge("find_shipment", "validate")
    graph.add_edge("validate", "score")
    graph.add_edge("score", "generate_evidence")
    graph.add_edge("generate_evidence", "decide")
    graph.add_edge("decide", END)

    # Use LangGraph's MemorySaver for checkpointing during interrupt/resume.
    # In production, replace with AsyncPostgresSaver for durability across restarts.
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["decide"])
    # interrupt_before="decide" means: after evidence is generated, pause if needed.
    # The decide node checks whether to auto-proceed or formally interrupt.
```

---

## Agent Nodes

### Node 1 — Normalize (`app/agent/nodes/normalize.py`)

**Purpose:** Clean and standardise the raw freight bill before any lookups.
**Uses LLM:** Yes — carrier name normalisation only.

```python
"""
Steps:
1. Uppercase and strip the lane code (e.g. "del-bom" → "DEL-BOM")
2. Parse bill_date to ISO string if not already
3. Ensure numeric fields are float/int, not strings
4. Call AI client to normalise carrier_name:
   - System prompt: "You are a carrier name normaliser for an Indian logistics system.
     Given a raw carrier name and a list of known carrier names, return ONLY the best
     matching known carrier name. If no match exists, return 'UNKNOWN'. No explanation."
   - User prompt: f"Raw name: {raw_name}\nKnown carriers: {known_names}"
   - Store result in state["normalized_carrier_name"]
5. If LLM returns "UNKNOWN", set normalized_carrier_name = "UNKNOWN" (handled in next node)
"""
```

### Node 2 — Resolve Carrier (`app/agent/nodes/resolve_carrier.py`)

**Purpose:** Match normalized carrier name to a carrier record in Postgres.
**Uses LLM:** No.

```python
"""
Steps:
1. Query Postgres: SELECT * FROM carriers WHERE name = normalized_carrier_name
   Also try: WHERE carrier_code = extracted_code_if_any
2. If found: set state["resolved_carrier"] = carrier dict
3. If not found:
   - Set state["should_escalate"] = True
   - Set state["flag_reason"] = "Unknown carrier — no record or contract exists. Manual review required."
   - Do NOT raise exception — let the graph route to decide node which will escalate.
"""
```

### Node 3 — Match Contract (`app/agent/nodes/match_contract.py`)

**Purpose:** Find the correct contract governing this freight bill.
**Uses LLM:** No — pure graph traversal + deterministic priority rules.

```python
"""
Steps:
1. Call graph_service.find_contracts_for_carrier_lane_date(
       carrier_id, normalized_lane, bill_date
   )
   Returns list of contract_ids whose date range covers the bill_date.

2. Fetch full contract + rate_card rows from Postgres for each candidate.

3. Resolution logic:
   CASE A — Zero candidates:
     - Call graph_service.find_expired_contracts_for_lane()
     - If expired contracts exist: set flag_reason = "Bill date falls after contract expiry.
       Nearest expired contract: {id}, expired {date}. Newer contract {id} exists with
       different pricing. Human review required."
     - If no contracts at all: escalate — no commercial relationship on this lane.

   CASE B — One candidate:
     - Use it. High confidence contribution.
     - state["matched_contract"] = contract
     - state["matched_rate_card"] = rate card for this lane

   CASE C — Multiple candidates (overlapping contracts):
     - If freight_bill["shipment_reference"] is not null:
         Fetch shipment from Postgres. shipment.contract_id is the authoritative match.
         Use that contract. Confidence not penalised.
     - Else if billed rate_per_kg matches exactly one candidate's rate_per_kg:
         Use that candidate. Note ambiguity but resolved by rate match.
         Moderate confidence penalty (-10).
     - Else:
         state["contract_ambiguous"] = True
         state["candidate_contracts"] = all candidates
         Set flag_reason = "Multiple overlapping contracts on {lane}. Cannot resolve
         without shipment reference. Human review required."
         Confidence penalty (-25).

4. Store matched_contract and matched_rate_card in state.
"""
```

### Node 4 — Find Shipment (`app/agent/nodes/find_shipment.py`)

**Purpose:** Locate the shipment and BOL(s) that this bill is for.
**Uses LLM:** No.

```python
"""
Steps:
1. If freight_bill["shipment_reference"] is not null:
   - Direct Postgres lookup by shipment_id
   - Fetch all BOLs for this shipment
   - state["shipment_found_via"] = "reference"

2. If no reference:
   - Call graph_service.find_shipments_by_carrier_lane_date_window()
   - If exactly one result: use it, set shipment_found_via = "fuzzy", confidence penalty (-10)
   - If multiple: take closest by date, note all candidates in evidence, confidence penalty (-15)
   - If none: state["matched_shipment"] = None, shipment_found_via = "none", penalty (-20)

3. If shipment found:
   - Fetch all BOLs from Postgres: SELECT * FROM bills_of_lading WHERE shipment_id = ?
   - state["matched_bols"] = list of BOL dicts

4. Always:
   - Call graph_service.find_prior_freight_bills_on_shipment(shipment_id)
   - state["prior_bills_on_shipment"] = list (used in validate node for cumulative check)
   - Also fetch their full records from Postgres to get billed_weight_kg totals
"""
```

### Node 5 — Validate (`app/agent/nodes/validate.py`)

**Purpose:** Run every deterministic check. Produce a structured list of results.
**Uses LLM:** No — pure arithmetic and comparisons.

Each check appends to `state["validation_results"]` as a `ValidationResult` dict.

```python
"""
CHECKS (in order):

1. DUPLICATE CHECK
   Query Postgres:
     SELECT id FROM freight_bills
     WHERE bill_number = ? AND carrier_id = ?
     AND id != current_bill_id
   If any row found: severity="fail", is_duplicate=True.
   This is a terminal fail — no further checks matter.

2. WEIGHT vs BOL
   If BOL exists:
     If billed_weight_kg > actual_weight_kg on BOL:
       severity = "fail" if deviation > 5% else "warning"
       detail = f"Billed {billed}kg, BOL confirms {actual}kg delivered"
     Else: pass

3. CUMULATIVE WEIGHT CHECK
   Sum billed_weight_kg across prior_bills_on_shipment + current bill.
   Compare to shipment.total_weight_kg.
   If sum > shipment total: severity="fail"
     detail = f"Total billed across all bills ({sum}kg) exceeds shipment weight ({total}kg)"

4. RATE CHECK (per-kg contracts)
   If matched_rate_card.rate_per_kg is not null:
     deviation_pct = abs(billed_rate - contracted_rate) / contracted_rate * 100
     If deviation_pct > RATE_DRIFT_TOLERANCE_PERCENT (from settings):
       severity = "fail" if deviation_pct > 10 else "warning"
       detail = f"Billed ₹{billed_rate}/kg, contracted ₹{contracted_rate}/kg ({deviation_pct:.1f}% drift)"
     Else: pass

5. FUEL SURCHARGE CHECK
   Determine correct_surcharge_pct:
     If rate_card.revised_on is not null AND bill_date >= rate_card.revised_on:
       correct_surcharge_pct = rate_card.revised_fuel_surcharge_percent
     Else:
       correct_surcharge_pct = rate_card.fuel_surcharge_percent
   expected_surcharge = base_charge * correct_surcharge_pct / 100
   If abs(billed_fuel_surcharge - expected_surcharge) > 1.0:  # ₹1 tolerance for rounding
     severity = "fail"
     detail = f"Fuel surcharge: billed ₹{billed}, expected ₹{expected} at {correct_pct}%"
   Else: pass

6. CONTRACT VALIDITY
   If matched_contract.status == "expired":
     severity = "fail"
     detail = f"Contract {contract_id} expired on {expiry_date}"

7. UNIT OF MEASURE CHECK (FTL contracts)
   If rate_card.unit == "FTL":
     FTL_amount = rate_card.rate_per_unit
     per_kg_amount = billed_weight_kg * rate_card.alternate_rate_per_kg
     If freight_bill.billing_unit == "kg":
       # Alternate billing — semantically valid but produces different total
       If abs(base_charge - per_kg_amount) < 1.0: pass (severity="warning" to note UOM difference)
       Else: severity="fail"
     Else:
       # Should be billed as FTL
       If abs(base_charge - FTL_amount) < 1.0: pass
       Else: severity="fail"

8. MINIMUM CHARGE CHECK
   If base_charge < rate_card.min_charge:
     severity = "warning"
     detail = f"Base charge ₹{base_charge} below contract minimum ₹{min_charge}"
"""
```

### Node 6 — Score (`app/agent/nodes/score.py`)

**Purpose:** Convert validation results and state flags into a single confidence score.
**Uses LLM:** No.

```python
"""
Scoring algorithm:

Start: score = 100.0

Deductions:

  Carrier:
    resolved cleanly          → 0
    (unknown already escalated, won't reach here)

  Contract:
    matched uniquely          → 0
    matched via rate heuristic → -10
    contract ambiguous        → -25
    no active contract        → -40
    expired contract          → -30

  Shipment:
    found via direct reference → 0
    found via fuzzy match      → -10
    found via fuzzy, multiple  → -15
    not found                  → -20

  Validation results:
    each WARNING              → -10
    each FAIL                 → -25
    duplicate detected        → score = 0 (override, hardcoded floor)

  Final score: max(0, score)

Decision mapping:
    score >= CONFIDENCE_AUTO_APPROVE_THRESHOLD (default 80): "auto_approve"
    score >= CONFIDENCE_DISPUTE_THRESHOLD (default 50):      "flag_for_review"
    score <  CONFIDENCE_DISPUTE_THRESHOLD:                   "dispute"
    should_escalate == True:                                  "escalate" (override)
    is_duplicate == True:                                     "dispute"  (override)

Store: state["confidence_score"], state["decision"]
"""
```

### Node 7 — Generate Evidence (`app/agent/nodes/generate_evidence.py`)

**Purpose:** Generate a human-readable explanation of the agent's reasoning.
**Uses LLM:** Yes — this is the second and final LLM call.

```python
"""
Build a structured context string from state:
  - Freight bill summary (id, carrier, lane, weight, amounts)
  - Matched contract (id, rates) or why matching failed
  - Matched shipment + BOL summary or why not found
  - Prior bills on shipment if any
  - Each validation result (check name, pass/fail, detail)
  - Confidence score and decision

System prompt:
  "You are an audit assistant for a logistics operations team. Your job is to write
   clear, concise explanations of freight bill validation decisions for human reviewers.
   Write in plain English. Be specific about numbers. State what matched, what failed,
   and why the decision was made. Do not use jargon. Keep it under 200 words."

User prompt: the structured context string above.

Store LLM response in state["evidence"].

NOTE: If LLM call fails for any reason, fall back to a deterministic template:
  "Bill {id}: {decision} (confidence {score}%). Checks: {pass_count} passed,
   {warn_count} warnings, {fail_count} failed. Primary concern: {first_fail_detail}"
  This ensures the pipeline never blocks on an LLM failure.
"""
```

### Node 8 — Decide (`app/agent/nodes/decide.py`)

**Purpose:** Write the final decision to the database. Either complete the bill or pause for human review.
**Uses LLM:** No.

```python
"""
Steps:
1. Write AgentDecisionRecord to Postgres with all matched IDs, score, decision, evidence.

2. If decision in ["auto_approve"]:
   - Update freight_bill.processing_status = "completed"
   - Write audit log: event_type="auto_approved", detail={score, contract, shipment}
   - Update Neo4j: link FreightBill to matched Contract
   - Done — graph reaches END.

3. If decision in ["flag_for_review", "dispute", "escalate"]:
   - Update freight_bill.processing_status = "awaiting_review"
   - Write ReviewQueue row with:
       agent_state = entire current state serialized to JSON (the checkpoint)
       flag_reason = state["flag_reason"]
       confidence_score = state["confidence_score"]
       evidence = state["evidence"]
   - Write audit log: event_type="flagged_for_review", detail={reason, score}
   - Call LangGraph interrupt() — agent pauses here.

4. On RESUME (human submits via POST /review/{id}):
   - state["human_review"] is injected by the API handler
   - Write HumanReview record to Postgres
   - Update freight_bill.processing_status = "completed"
   - Write audit log: event_type="human_reviewed", detail={reviewer_decision, notes}
   - Update ReviewQueue status = "reviewed"
   - Graph reaches END.
"""
```

---

## API Routes

### `POST /freight-bills`

**Request body:**
```json
{
  "id": "FB-2025-101",
  "carrier_id": "CAR001",
  "carrier_name": "Safexpress Logistics",
  "bill_number": "SFX/2025/00234",
  "bill_date": "2025-02-15",
  "shipment_reference": "SHP-2025-002",
  "lane": "DEL-BLR",
  "billed_weight_kg": 850,
  "rate_per_kg": 15.00,
  "billing_unit": "kg",
  "base_charge": 12750.00,
  "fuel_surcharge": 1020.00,
  "gst_amount": 2479.00,
  "total_amount": 16249.00
}
```

**Behaviour:**
1. Validate request schema (Pydantic).
2. Write FreightBill row to Postgres with `processing_status = "ingested"`.
3. Create FreightBill node in Neo4j. If `shipment_reference` present, create REFERENCES edge.
4. Write audit log: `event_type = "bill_ingested"`.
5. Launch LangGraph agent as a background task (FastAPI BackgroundTasks or asyncio task).
6. Return immediately — do not wait for agent.

**Response (202 Accepted):**
```json
{
  "id": "FB-2025-101",
  "processing_status": "ingested",
  "message": "Freight bill accepted. Processing started."
}
```

---

### `GET /freight-bills/{id}`

**Behaviour:** Fetch freight bill + decision + evidence from Postgres.

**Response:**
```json
{
  "id": "FB-2025-101",
  "carrier_name": "Safexpress Logistics",
  "lane": "DEL-BLR",
  "total_amount": 16249.00,
  "processing_status": "completed",
  "decision": {
    "decision": "auto_approve",
    "confidence_score": 95.0,
    "matched_contract_id": "CC-2024-SFX-001",
    "matched_shipment_id": "SHP-2025-002",
    "validation_results": [
      {"check": "duplicate_check", "passed": true, "detail": "No prior bill with this number", "severity": "pass"},
      {"check": "weight_match", "passed": true, "detail": "Billed 850kg matches BOL 850kg", "severity": "pass"},
      {"check": "rate_check", "passed": true, "detail": "₹15.00/kg matches contract", "severity": "pass"}
    ],
    "evidence": "Freight bill FB-2025-101 from Safexpress Logistics for the DEL-BLR lane has been auto-approved..."
  }
}
```

---

### `GET /review-queue`

**Behaviour:** Return all ReviewQueue rows with `status = "pending"`, joined with freight bill details.

**Response:**
```json
{
  "queue": [
    {
      "review_queue_id": 3,
      "freight_bill_id": "FB-2025-104",
      "carrier_name": "Safexpress Logistics",
      "lane": "DEL-BOM",
      "total_amount": 23895.00,
      "flag_reason": "Cumulative billed weight (2300kg) exceeds shipment total (2000kg). Possible over-billing.",
      "confidence_score": 20.0,
      "evidence": "This bill claims 1500kg on the DEL-BOM lane for SHP-2025-001...",
      "flagged_at": "2025-03-15T10:23:00Z"
    }
  ],
  "total": 1
}
```

---

### `POST /review/{id}`

**Request body:**
```json
{
  "reviewer_decision": "dispute",
  "reviewer_notes": "Over-billing confirmed. BOL shows 1200kg on first truck. This bill claims 1500kg.",
  "corrected_amount": null
}
```

**Behaviour:**
1. Fetch ReviewQueue row for this freight_bill_id.
2. Validate it exists and is still `status = "pending"`.
3. Write HumanReview record.
4. Restore LangGraph agent state from `review_queue.agent_state` checkpoint.
5. Inject human_review into state.
6. Resume agent — it will write final decision and close out.

**Response:**
```json
{
  "freight_bill_id": "FB-2025-104",
  "reviewer_decision": "dispute",
  "message": "Review submitted. Agent resuming."
}
```

---

## Docker Compose — `docker-compose.yml`

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: freight
      POSTGRES_PASSWORD: freight
      POSTGRES_DB: freight
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: neo4j/password
    ports:
      - "7474:7474"   # browser
      - "7687:7687"   # bolt
    volumes:
      - neo4j_data:/data

  api:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      - postgres
      - neo4j
    command: >
      sh -c "
        poetry run alembic upgrade head &&
        poetry run python scripts/seed_loader.py --data data/seed_data_logistics.json &&
        poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
      "

volumes:
  postgres_data:
  neo4j_data:
```

---

## pyproject.toml

```toml
[tool.poetry]
name = "freight-bill-processor"
version = "0.1.0"
description = "Freight bill processing system with LangGraph agent"
authors = ["Your Name"]
python = "^3.11"

[tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.111.0"
uvicorn = {extras = ["standard"], version = "^0.29.0"}
sqlalchemy = {extras = ["asyncio"], version = "^2.0.0"}
asyncpg = "^0.29.0"
alembic = "^1.13.0"
pydantic = "^2.7.0"
pydantic-settings = "^2.2.0"
neo4j = "^5.20.0"
langgraph = "^0.1.0"
langchain-core = "^0.2.0"
anthropic = "^0.28.0"
openai = "^1.30.0"
google-generativeai = "^0.7.0"
python-dotenv = "^1.0.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.2.0"
pytest-asyncio = "^0.23.0"
httpx = "^0.27.0"   # for FastAPI test client

[build-system]
requires = ["poetry-core"]
build-backend = "poetry-core.masonry.api"
```

---

## `.env.example`

```
POSTGRES_URL=postgresql+asyncpg://freight:freight@localhost:5432/freight

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password

LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-haiku-20241022
LLM_API_KEY=your_api_key_here

CONFIDENCE_AUTO_APPROVE_THRESHOLD=80.0
CONFIDENCE_DISPUTE_THRESHOLD=50.0
RATE_DRIFT_TOLERANCE_PERCENT=2.0
```

---

## How Each Seed Freight Bill Should Be Processed

This documents the expected agent behaviour for each test case.

| Bill | Expected Decision | Key Checks |
|---|---|---|
| FB-2025-101 | auto_approve (~95) | Clean match, weight/rate/surcharge all pass |
| FB-2025-102 | flag_for_review (~55) | 3 overlapping contracts, no shipment ref, ambiguous |
| FB-2025-103 | auto_approve (~80) | Partial delivery, weight matches remaining 800kg |
| FB-2025-104 | dispute (~20) | Cumulative weight 2300kg > 2000kg shipment, over-billing |
| FB-2025-105 | flag_for_review (~65) | Rate drift ₹8.70 vs ₹8.00 contracted (8.75% over) |
| FB-2025-106 | dispute (~30) | Billing against expired contract CC-2023-TCI-001 |
| FB-2025-107 | flag_for_review (~70) | UOM mismatch — FTL contract billed per-kg, amounts differ |
| FB-2025-108 | auto_approve (~88) | Revised fuel surcharge (18%) correctly applied post Oct 2024 |
| FB-2025-109 | dispute (~0) | Duplicate bill number SFX/2025/00234 from same carrier |
| FB-2025-110 | escalate | Unknown carrier Gati KWE, no record, no contract |

---

## Confidence Score Design — Reference

```
Starting score: 100

Carrier resolution:
  Clean match                   +0   (expected baseline)
  Unknown → escalate            override to "escalate" regardless of score

Contract matching:
  Single active contract        +0
  Resolved via shipment ref     +0
  Resolved via rate heuristic   -10
  Ambiguous, unresolved         -25
  Expired contract on lane      -30
  No contract at all            -40

Shipment matching:
  Found via direct reference    +0
  Found via fuzzy match         -10
  Multiple fuzzy candidates     -15
  Not found                     -20

Per validation check:
  PASS                          +0
  WARNING                       -10  each
  FAIL                          -25  each

Hard overrides:
  is_duplicate = True           → score = 0, decision = "dispute"
  should_escalate = True        → decision = "escalate" (score irrelevant)

Decision thresholds (configurable via env):
  score >= 80  → auto_approve
  score >= 50  → flag_for_review
  score <  50  → dispute
```

---

## Human-in-the-Loop Pattern — Implementation Detail

LangGraph's `interrupt()` mechanism is the core of this. Here is exactly how it works:

1. **Agent reaches the decide node** and determines the decision is not `auto_approve`.
2. **Agent serializes its full state** — the `FreightBillState` TypedDict — and writes it to `review_queue.agent_state` as a JSON blob in Postgres.
3. **Agent calls `interrupt()`** — LangGraph pauses execution. The graph thread is suspended. The LangGraph checkpointer (MemorySaver in dev, AsyncPostgresSaver in prod) saves the graph's internal execution state.
4. **The API returns** to whatever triggered the agent. The freight bill's `processing_status` is now `awaiting_review`.
5. **Reviewer calls `POST /review/{id}`** with their decision.
6. **The API handler** fetches the checkpoint from the checkpointer using the thread_id (which should be the freight_bill_id), injects `human_review` into state, and calls `graph.invoke(None, config={"configurable": {"thread_id": bill_id}})` — this resumes from the interrupt point.
7. **Agent continues** from where it paused, writes final decision, closes out.

The `thread_id` for each agent run must be the freight_bill_id. This is what LangGraph uses to look up the right checkpoint.

```python
# In the API route handler — starting the agent:
config = {"configurable": {"thread_id": freight_bill.id}}
await graph.ainvoke({"freight_bill": bill_dict}, config=config)

# In the review route handler — resuming the agent:
config = {"configurable": {"thread_id": freight_bill_id}}
await graph.ainvoke(
    {"human_review": reviewer_decision_dict},
    config=config
)
```

---

## Audit Log Events Reference

Every meaningful state transition writes to `audit_log`. Event types:

| event_type | Triggered when |
|---|---|
| `bill_ingested` | POST /freight-bills received |
| `agent_started` | LangGraph graph begins |
| `carrier_resolved` | Carrier matched successfully |
| `carrier_unknown` | Carrier not found — escalating |
| `contract_matched` | Single contract identified |
| `contract_ambiguous` | Multiple overlapping contracts |
| `contract_expired` | Bill date after contract expiry |
| `shipment_matched` | Shipment found (direct or fuzzy) |
| `shipment_not_found` | No shipment located |
| `validation_complete` | All checks run — summary |
| `auto_approved` | Agent decided auto_approve |
| `flagged_for_review` | Agent paused, added to queue |
| `human_reviewed` | Reviewer submitted decision |
| `duplicate_detected` | Duplicate bill number found |

---

## What to Build First — Recommended Order

```
Phase 1 — Foundation (get data in)
  1. docker-compose.yml with Postgres + Neo4j
  2. SQLAlchemy models + Alembic migration
  3. Seed loader script — Postgres first, then Neo4j
  4. Verify with sanity queries (counts, graph traversals)

Phase 2 — API skeleton
  5. FastAPI app setup, config, dependencies
  6. POST /freight-bills — just write to DB, no agent yet
  7. GET /freight-bills/{id} — just read from DB

Phase 3 — Agent (node by node)
  8. State definition
  9. Normalize node — test with a single bill dict
  10. Resolve carrier node
  11. Match contract node — test with the overlapping contract cases
  12. Find shipment node
  13. Validate node — test each check individually
  14. Score node
  15. Generate evidence node
  16. Decide node + interrupt/resume

Phase 4 — Wire together
  17. Connect POST /freight-bills to launch agent
  18. GET /review-queue
  19. POST /review/{id} with resume logic

Phase 5 — Polish
  20. Error handling, logging, audit trail
  21. Tests for validation logic and agent decisions
  22. README
```

---

## Key Design Decisions and Tradeoffs

**LLM used minimally and only where deterministic logic fails.** The LLM is called exactly twice per bill: once for carrier name normalisation, once for evidence generation. All validation, scoring, and routing is deterministic Python. This means the system's decisions are auditable, reproducible, and not subject to LLM hallucination on financial figures.

**AIClient abstraction isolates provider dependency.** The rest of the codebase imports `get_ai_client()` and calls `.complete(system, user)`. Switching from Claude to GPT-4o is two env var changes. No code changes.

**Confidence score is additive penalties, not a model.** This is intentional. A learned confidence score would be uninterpretable. The ops team can read the score, see the deduction breakdown, and understand exactly why a bill scored 65 instead of 95. Interpretability matters in financial systems.

**Neo4j is justified for relationship traversal, not data storage.** Postgres is the source of truth. Neo4j holds only what's needed for graph queries — IDs and relationship edges. If Neo4j goes down, Postgres still has everything. Rehydrating the graph from Postgres is a one-command operation.

**The interrupt/resume pattern is a first-class concern, not an afterthought.** State is serialized to Postgres before every interrupt. This means the system survives restarts. A bill flagged for review on Monday can be reviewed on Wednesday regardless of what happened to the server in between.

**MemorySaver in dev, AsyncPostgresSaver in prod.** LangGraph's MemorySaver is in-process and lost on restart — fine for development. For production, swap to LangGraph's AsyncPostgresSaver which persists checkpoints to Postgres. One line change in `graph.py`.