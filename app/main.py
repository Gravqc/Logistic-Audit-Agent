from fastapi import FastAPI
from app.api.routes import freight_bills, reviews

app = FastAPI(
    title="Freight Bill Processing System",
    description="System to ingest and process freight bills with a LangGraph agent",
    version="0.1.0"
)

app.include_router(freight_bills.router, prefix="/freight-bills", tags=["Freight Bills"])
app.include_router(reviews.router, tags=["Reviews"])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
