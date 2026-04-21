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
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.postgres import AsyncSessionLocal
from app.db import models
from app.services.graph_service import GraphService
from app.db.neo4j import neo4j_conn

def parse_date(date_str):
    if not date_str:
        return None
    return datetime.strptime(date_str, "%Y-%m-%d").date()

async def load_carriers(session: AsyncSession, graph: GraphService, carriers: list[dict]):
    print(f"Loading {len(carriers)} carriers...")
    for c in carriers:
        carrier = models.Carrier(
            id=c["id"],
            name=c["name"],
            carrier_code=c["carrier_code"],
            gstin=c.get("gstin"),
            bank_account=c.get("bank_account"),
            status=models.CarrierStatus(c["status"]),
            onboarded_on=parse_date(c.get("onboarded_on"))
        )
        session.add(carrier)
        
        # Neo4j
        query = "MERGE (c:Carrier {id: $id}) SET c.name = $name, c.carrier_code = $code"
        driver = await graph.get_driver()
        async with driver.session() as driver_session:
            await driver_session.run(query, id=carrier.id, name=carrier.name, code=carrier.carrier_code)
            
    await session.commit()

async def load_contracts(session: AsyncSession, graph: GraphService, contracts: list[dict]):
    print(f"Loading {len(contracts)} contracts...")
    for c in contracts:
        contract = models.CarrierContract(
            id=c["id"],
            carrier_id=c["carrier_id"],
            effective_date=parse_date(c["effective_date"]),
            expiry_date=parse_date(c["expiry_date"]),
            status=models.ContractStatus(c["status"]),
            notes=c.get("notes")
        )
        session.add(contract)
        
        # Neo4j
        query = """
        MATCH (c:Carrier {id: $carrier_id})
        MERGE (contract:Contract {id: $id})
        SET contract.effective_date = date($effective_date),
            contract.expiry_date = date($expiry_date),
            contract.status = $status
        MERGE (c)-[:HAS_CONTRACT]->(contract)
        """
        driver = await graph.get_driver()
        async with driver.session() as driver_session:
            await driver_session.run(
                query, 
                carrier_id=contract.carrier_id, 
                id=contract.id, 
                effective_date=c["effective_date"], 
                expiry_date=c["expiry_date"],
                status=contract.status.value
            )
            
    await session.commit()

async def load_rate_cards(session: AsyncSession, graph: GraphService, contracts: list[dict]):
    rate_card_count = 0
    for c in contracts:
        contract_id = c["id"]
        for rc in c.get("rate_card", []):
            rate_card_count += 1
            rate_card = models.ContractRateCard(
                contract_id=contract_id,
                lane=rc["lane"],
                description=rc.get("description"),
                rate_per_kg=rc.get("rate_per_kg"),
                rate_per_unit=rc.get("rate_per_unit"),
                unit=rc.get("unit"),
                unit_capacity_kg=rc.get("unit_capacity_kg"),
                alternate_rate_per_kg=rc.get("alternate_rate_per_kg"),
                min_charge=rc.get("min_charge", 0.0),
                fuel_surcharge_percent=rc.get("fuel_surcharge_percent", 0.0),
                revised_on=parse_date(rc.get("revised_on")),
                revised_fuel_surcharge_percent=rc.get("revised_fuel_surcharge_percent")
            )
            session.add(rate_card)
            
            # Neo4j Lane node and link
            query = """
            MATCH (contract:Contract {id: $contract_id})
            MERGE (lane:Lane {code: $lane_code})
            MERGE (contract)-[:COVERS_LANE]->(lane)
            """
            driver = await graph.get_driver()
            async with driver.session() as driver_session:
                await driver_session.run(query, contract_id=contract_id, lane_code=rc["lane"])
                
    print(f"Loaded {rate_card_count} rate cards.")
    await session.commit()

async def load_shipments(session: AsyncSession, graph: GraphService, shipments: list[dict]):
    print(f"Loading {len(shipments)} shipments...")
    for s in shipments:
        shipment = models.Shipment(
            id=s["id"],
            carrier_id=s["carrier_id"],
            contract_id=s["contract_id"],
            lane=s["lane"],
            shipment_date=parse_date(s["shipment_date"]),
            status=models.ShipmentStatus(s["status"]),
            total_weight_kg=s["total_weight_kg"],
            notes=s.get("notes")
        )
        session.add(shipment)
        
        # Neo4j
        query = """
        MATCH (c:Carrier {id: $carrier_id})
        MATCH (contract:Contract {id: $contract_id})
        MERGE (lane:Lane {code: $lane})
        MERGE (s:Shipment {id: $id})
        SET s.shipment_date = date($shipment_date),
            s.total_weight_kg = $total_weight_kg
        MERGE (s)-[:CARRIED_BY]->(c)
        MERGE (s)-[:UNDER_CONTRACT]->(contract)
        MERGE (s)-[:ON_LANE]->(lane)
        """
        driver = await graph.get_driver()
        async with driver.session() as driver_session:
            await driver_session.run(
                query,
                carrier_id=shipment.carrier_id,
                contract_id=shipment.contract_id,
                lane=shipment.lane,
                id=shipment.id,
                shipment_date=s["shipment_date"],
                total_weight_kg=shipment.total_weight_kg
            )
            
    await session.commit()

async def load_bols(session: AsyncSession, graph: GraphService, bols: list[dict]):
    print(f"Loading {len(bols)} BOLs...")
    for b in bols:
        bol = models.BillOfLading(
            id=b["id"],
            shipment_id=b["shipment_id"],
            delivery_date=parse_date(b["delivery_date"]),
            actual_weight_kg=b["actual_weight_kg"],
            notes=b.get("notes")
        )
        session.add(bol)
        
        # Neo4j
        query = """
        MATCH (s:Shipment {id: $shipment_id})
        MERGE (bol:BOL {id: $id})
        SET bol.delivery_date = date($delivery_date),
            bol.actual_weight_kg = $actual_weight_kg
        MERGE (bol)-[:PROVES_DELIVERY_FOR]->(s)
        """
        driver = await graph.get_driver()
        async with driver.session() as driver_session:
            await driver_session.run(
                query,
                shipment_id=bol.shipment_id,
                id=bol.id,
                delivery_date=b["delivery_date"],
                actual_weight_kg=bol.actual_weight_kg
            )
            
    await session.commit()

def print_verification_summary():
    print("Seed data successfully loaded to both PostgreSQL and Neo4j!")

async def main(data_path: str):
    data = json.loads(Path(data_path).read_text())
    
    graph = GraphService()
    
    async with AsyncSessionLocal() as session:
        try:
            await load_carriers(session, graph, data.get("carriers", []))
            await load_contracts(session, graph, data.get("carrier_contracts", []))
            await load_rate_cards(session, graph, data.get("carrier_contracts", []))
            await load_shipments(session, graph, data.get("shipments", []))
            await load_bols(session, graph, data.get("bills_of_lading", []))
        except Exception as e:
            print(f"Error occurred during data load: {e}")
            await session.rollback()
            raise
    
    await neo4j_conn.close()
    print_verification_summary()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.data))
