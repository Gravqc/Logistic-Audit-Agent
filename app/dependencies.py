from typing import AsyncGenerator
from app.db.postgres import AsyncSessionLocal
from app.services.graph_service import GraphService

async def get_db_session() -> AsyncGenerator:
    async with AsyncSessionLocal() as session:
        yield session

def get_graph_service() -> GraphService:
    return GraphService()
