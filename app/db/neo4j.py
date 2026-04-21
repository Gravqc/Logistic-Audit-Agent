from neo4j import AsyncGraphDatabase
from app.config import get_settings

class Neo4jConnection:
    def __init__(self):
        self._driver = None

    def get_driver(self):
        if self._driver is None:
            settings = get_settings()
            self._driver = AsyncGraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
            )
        return self._driver

    async def close(self):
        if self._driver is not None:
            await self._driver.close()

neo4j_conn = Neo4jConnection()

async def get_neo4j_driver():
    return neo4j_conn.get_driver()
