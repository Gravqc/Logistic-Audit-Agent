# Architecture Decisions

This document outlines the major technical decisions made during the design and implementation of the Logistic Audit Agent.

## 1. Dual Database Strategy (PostgreSQL + Neo4j)
**Decision**: Use PostgreSQL as the primary transactional store and Neo4j for contract matching and relationship mapping.
**Why**: 
Freight bill auditing requires both robust transactional integrity (e.g., maintaining state, audit logs, and rigid financial schemas) and complex relationship traversal (e.g., matching a carrier and lane to multiple overlapping rate cards across time). 
- PostgreSQL provides strict schema validation and ACID compliance for the API.
- Neo4j significantly simplifies the graph traversal required to find applicable rate cards and link shipments across complex operational parameters, eliminating the need for massively complex SQL `JOIN` statements.

## 2. Agentic Workflow via LangGraph
**Decision**: Orchestrate the auditing pipeline using LangGraph instead of a traditional linear script or a monolithic LLM call.
**Why**: 
LangGraph treats the audit process as a directed acyclic graph (DAG) where state is explicitly passed between nodes. 
- **Modularity**: Individual nodes (`normalize`, `validate`, `score`) can be isolated, tested, and upgraded independently.
- **Human-in-the-loop**: LangGraph's native `interrupt()` allows the agent to pause execution for ambiguous cases, wait for a human decision, and then seamlessly resume execution exactly where it left off, which is critical for the Review Queue feature.

## 3. Selective Use of Large Language Models (LLMs)
**Decision**: Restrict the use of the LLM to specific, non-deterministic tasks (carrier name normalization and evidence generation) while handling all mathematical validations using deterministic Python code.
**Why**: 
LLMs are notoriously unreliable at arithmetic and strict rules-engine logic. By confining the LLM to tasks it excels at (understanding messy, unstructured text like a misspelled carrier name, or writing a human-readable summary of validation results) and using pure Python for rate calculations and weight checks, the system guarantees 100% mathematical accuracy while still benefiting from AI flexibility.

## 4. "Inferred" vs "Exact" Shipment Matching
**Decision**: Use an inferred lookup mechanism for shipments when explicit IDs are missing.
**Why**:
Often, a freight bill will not contain an exact `shipment_reference` ID. The system falls back to querying Neo4j for a shipment matching the same carrier, lane, and a close date window. While this results in an "exact match" against those parameters, the relationship is heuristically *inferred* rather than explicitly stated. We denote these matches as `inferred` to alert human reviewers that the system made an educated assumption.
