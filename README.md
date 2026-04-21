# Logistic Audit Agent

The Logistic Audit Agent is an intelligent system for automating the auditing of freight bills against negotiated contracts and operational data. It leverages FastAPI for the backend, PostgreSQL for relational storage, Neo4j for contract matching and graph relationships, and LangGraph to orchestrate a deterministic and LLM-powered auditing pipeline.

## Features

- **Automated Ingestion**: Ingests freight bills via API.
- **Contract & Lane Matching**: Uses Neo4j to find the correct rate cards for a given carrier and lane.
- **Deterministic Validation**: Calculates exact fuel surcharges, checks rate drift, unit of measure, cumulative weights, and duplicate checks.
- **AI-Powered Resolution**: Employs Google GenAI for carrier normalization and generating human-readable evidence summaries.
- **Human-in-the-Loop Workflow**: Automatically pauses execution using LangGraph `interrupt()` for ambiguous cases or unknown carriers, placing them in a review queue.

## Setup Instructions

### 1. Prerequisites
- Python 3.11+
- [Poetry](https://python-poetry.org/docs/)
- Docker Desktop
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
Start the PostgreSQL and Neo4j databases:
```bash
docker-compose up -d
```

### 4. Install Dependencies
```bash
poetry install
```

### 5. Setup Database
Run the Alembic migrations to create the schema in PostgreSQL:
```bash
poetry run alembic upgrade head
```

### 6. Load Seed Data
Populate the databases with the provided seed data (Carriers, Contracts, Rate Cards, Shipments, BOLs):
```bash
poetry run python scripts/seed_loader.py --data data/seed_data_logistics.json
```

## Running the Application

### Command Line
You can run the FastAPI server via Uvicorn:
```bash
poetry run uvicorn app.main:app --reload
```

### VS Code Debugging
A `.vscode/launch.json` is provided. Go to the **Run & Debug** panel (`Cmd+Shift+D` on Mac) in VS Code, ensure that the Poetry Python interpreter is selected, and choose **"FastAPI (Uvicorn)"** to launch the app with full breakpoint support.

## API Endpoints

Access the interactive Swagger UI at `http://127.0.0.1:8000/docs`.

- `POST /freight-bills/`: Submit a freight bill for processing.
- `GET /freight-bills/{id}`: Check the status, decision, and evidence for a processed freight bill.
- `GET /review-queue`: Fetch all bills currently awaiting human review.
- `POST /review/{id}`: Submit a human decision for a queued bill (resumes the agent).
- `DELETE /freight-bills/reset`: Wipes all transactional data (bills, decisions, reviews) to cleanly restart testing without affecting the seed data.