# Freight Bill Processing System - Backend Assignment

## Overview
This repository contains the solution for the Freight Bill Audit Agent assignment. The system intelligently automates the auditing of freight bills against negotiated contracts and operational data (shipments, BOLs), while maintaining strict financial accuracy and handling operational ambiguities.

The solution is designed to directly address the core challenges of the problem statement:
1. **Deterministic Validations**: All financial calculations (rate drift, fuel surcharges, UOM mapping) and business rules (duplicate checks, cumulative weight reconciliation) are handled in pure Python to guarantee 100% accuracy.
2. **Ambiguity Handling via AI**: Google GenAI is selectively employed for unstructured tasks where traditional logic fails, such as normalizing misspelled carrier names against the database and generating concise, human-readable summaries of the audit evidence.
3. **Human-in-the-Loop (HITL)**: When the system encounters unsolvable ambiguities (e.g., completely unknown spot carriers, or overlapping contracts with missing shipment references), it does not fail or hallucinate. Instead, it pauses execution and drops the bill into a review queue for human intervention.
4. **Complete Auditability**: Every step, from automated approvals to human overrides, is strictly recorded in a transactional PostgreSQL audit log.

## Architecture & Technical Decisions

The system architecture was heavily influenced by the need to balance strict financial rules with flexible graph relationships:

- **Dual Database Strategy (PostgreSQL + Neo4j)**: 
  PostgreSQL acts as the source of truth for transactional data, strictly enforcing data integrity and foreign key constraints for the API. Neo4j is utilized to handle the highly relational aspect of logistics—traversing nodes to find the correct rate card for a specific carrier, lane, and time-window without writing massively complex, brittle SQL `JOIN` statements.
- **Agent Orchestration via LangGraph**: 
  Instead of a monolithic LLM call or a rigid linear script, the auditing process is modeled as a Directed Acyclic Graph (DAG) using LangGraph. This modular design isolates individual checks and utilizes LangGraph's native checkpointer and `interrupt()` functionality to seamlessly pause and resume the agent for human reviews.
- **Selective LLM Integration**: 
  LLMs are notoriously unreliable at arithmetic. By restricting the LLM to text-normalization and summarization, we eliminate the risk of hallucinated rates or math errors while still benefiting from AI adaptability.

For a deeper dive into the specific nodes and execution order, please refer to:
- [Architecture Decisions Documentation](docs/architecture_decisions.md)
- [Agent End-to-End Flow](docs/agent_flow.md)

---

## Setup Instructions

### 1. Prerequisites
- Python 3.11+
- [Poetry](https://python-poetry.org/docs/)
- Docker Desktop (for Postgres and Neo4j)
- A Google Gemini API Key (or other LLM provider configured in `.env`)

### 2. Environment Variables
Create a `.env` file in the root directory:
```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/logistics
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
LLM_PROVIDER=google
LLM_MODEL=gemini-2.5-flash
GEMINI_API_KEY=your_gemini_key_here
```

### 3. Start Infrastructure
Spin up the PostgreSQL and Neo4j databases:
```bash
docker-compose up -d
```
Open Postgres CLI (psql)
```
psql -U freight -d freight
```

### 4. Install Dependencies
```bash
poetry install
```

### 5. Setup Database Schema
Run the Alembic migrations to create the schema in PostgreSQL:
```bash
poetry run alembic upgrade head
```

### 6. Load Seed Data
Populate both databases with the provided assignment seed data (Carriers, Contracts, Rate Cards, Shipments, BOLs):
```bash
poetry run python scripts/seed_loader.py --data data/seed_data_logistics.json
```

---

## Running the Application

### Command Line
Start the FastAPI server via Uvicorn:
```bash
poetry run uvicorn app.main:app --reload
```

### VS Code Debugging
A `.vscode/launch.json` is provided. Go to the **Run & Debug** panel (`Cmd+Shift+D` on Mac) in VS Code, ensure that the Poetry Python interpreter is selected, and choose **"FastAPI (Uvicorn)"** to launch the app with full breakpoint support.

---

## Testing the API

Once the server is running, you can access the interactive Swagger UI at `http://127.0.0.1:8000/docs`.

### Available Endpoints:
- `POST /freight-bills/`: Submit a freight bill for processing. This immediately triggers the LangGraph agent in the background.
- `GET /freight-bills/{id}`: Retrieve the current processing status, the final decision, and the AI-generated evidence summary for a processed freight bill.
- `GET /review-queue`: Fetch all bills that the agent has paused and flagged for human review.
- `POST /review/{id}`: Submit a human decision for a queued bill. This instantly resumes the suspended LangGraph agent to conclude the workflow.
- `DELETE /freight-bills/reset`: Wipes all transactional data (bills, decisions, reviews, audit logs) to cleanly restart testing from scratch without affecting the foundational seed data.