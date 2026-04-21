from neo4j import AsyncGraphDatabase
from app.db.neo4j import get_neo4j_driver

class GraphService:
    def __init__(self):
        # We fetch the driver at invocation time to avoid issues with event loops
        pass

    async def get_driver(self):
        return await get_neo4j_driver()

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
        WHERE contract.effective_date <= date($bill_date)
          AND contract.expiry_date >= date($bill_date)
          AND contract.status = 'active'
        RETURN contract.id AS contract_id
        """
        driver = await self.get_driver()
        async with driver.session() as session:
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
        driver = await self.get_driver()
        async with driver.session() as session:
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
        driver = await self.get_driver()
        async with driver.session() as session:
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
        RETURN s.id AS shipment_id, toString(s.shipment_date) AS shipment_date,
               s.total_weight_kg AS total_weight_kg
        ORDER BY abs(duration.between(date(s.shipment_date), date($bill_date)).days)
        """
        driver = await self.get_driver()
        async with driver.session() as session:
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
            fb.bill_date = date($bill_date),
            fb.lane = $lane,
            fb.billed_weight_kg = $billed_weight_kg
        WITH fb
        OPTIONAL MATCH (c:Carrier {id: $carrier_id})
        FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
            MERGE (fb)-[:SUBMITTED_BY]->(c)
        )
        """
        driver = await self.get_driver()
        async with driver.session() as session:
            # We copy to avoid modifying the original dict
            bill_params = bill.copy()
            if isinstance(bill_params.get("bill_date"), str) == False:
                bill_params["bill_date"] = str(bill_params["bill_date"])
            await session.run(query, **bill_params)

    async def link_freight_bill_to_shipment(self, bill_id: str, shipment_id: str):
        query = """
        MATCH (fb:FreightBill {id: $bill_id})
        MATCH (s:Shipment {id: $shipment_id})
        MERGE (fb)-[:REFERENCES]->(s)
        """
        driver = await self.get_driver()
        async with driver.session() as session:
            await session.run(query, bill_id=bill_id, shipment_id=shipment_id)

    async def link_freight_bill_to_contract(self, bill_id: str, contract_id: str):
        query = """
        MATCH (fb:FreightBill {id: $bill_id})
        MATCH (contract:Contract {id: $contract_id})
        MERGE (fb)-[:MATCHED_TO]->(contract)
        """
        driver = await self.get_driver()
        async with driver.session() as session:
            await session.run(query, bill_id=bill_id, contract_id=contract_id)
