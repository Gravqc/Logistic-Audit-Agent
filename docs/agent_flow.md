# Agent End-to-End Flow

This document details the step-by-step execution flow of the LangGraph agent when a new freight bill is ingested.

The agent's state is tracked via the `FreightBillState` dictionary, which is updated at each node.

## 1. `normalize`
- **Input**: Raw `freight_bill` payload.
- **Action**: 
  - Capitalizes and strips the `lane` string.
  - Queries the PostgreSQL database dynamically to fetch a list of all currently onboarded carriers.
  - Passes the raw `carrier_name` and the list of known carriers to the LLM (Google GenAI) to find the best match.
- **Output**: Sets `normalized_carrier_name` and `normalized_lane`.

## 2. `resolve_carrier`
- **Input**: `normalized_carrier_name`.
- **Action**: 
  - Looks up the normalized name in the PostgreSQL `Carrier` table.
  - If the LLM returned "UNKNOWN" (meaning the carrier is completely unrecognized), the node sets `should_escalate = True`.
- **Output**: Sets `resolved_carrier` (dictionary of carrier details) or flags the state for immediate escalation.

## 3. `match_contract`
*(Skipped if `should_escalate` is True)*
- **Input**: `resolved_carrier`, `normalized_lane`, `bill_date`.
- **Action**: 
  - Queries Neo4j for any contracts belonging to the carrier on the specified lane that were active on the bill date.
  - Fetches full rate card details from PostgreSQL.
  - Resolves ambiguities (e.g., if multiple contracts overlap, it attempts to match based on the billed rate).
- **Output**: Sets `matched_contract` and `matched_rate_card`. If multiple overlapping contracts exist and cannot be resolved, sets `contract_ambiguous = True`.

## 4. `find_shipment`
*(Skipped if `should_escalate` is True)*
- **Input**: `freight_bill`, `resolved_carrier`, `normalized_lane`.
- **Action**: 
  - If `shipment_reference` is provided, fetches it directly.
  - Otherwise, queries Neo4j to infer the shipment based on the carrier, lane, and a narrow date window.
  - Fetches associated Bills of Lading (BOLs) and any prior freight bills linked to this shipment (for cumulative checks).
- **Output**: Sets `matched_shipment`, `matched_bols`, `prior_bills_on_shipment`, and `shipment_found_via` (`"reference"`, `"inferred"`, or `"inferred_multiple"`).

## 5. `validate`
*(Skipped if `should_escalate` is True)*
- **Input**: `freight_bill`, `matched_contract`, `matched_rate_card`, `matched_shipment`, `matched_bols`.
- **Action**: Runs deterministic Python functions to check:
  - **Duplicate Check**: Is there already a bill with this number?
  - **Weight Match**: Does the billed weight match the BOL actual weight?
  - **Cumulative Weight**: Do all bills on this shipment exceed the total shipment weight?
  - **Rate Check**: Does the billed rate match the contracted rate (within a 2% drift tolerance)?
  - **Fuel Surcharge**: Is the fuel surcharge mathematically correct according to the contracted percentage?
  - **UOM & Minimums**: Does it respect Full Truck Load (FTL) alternates and minimum charge floors?
- **Output**: Sets a list of `validation_results` (pass, warning, or fail).

## 6. `score`
*(Skipped if `should_escalate` is True)*
- **Input**: `validation_results`, matching ambiguity flags.
- **Action**: 
  - Starts with a base score of `100.0`.
  - Deducts points based on the severity of validation failures, missing contracts, or inferred shipments.
- **Output**: Sets `confidence_score` and determines the preliminary `decision` (`"auto_approve"`, `"flag_for_review"`, `"dispute"`).

## 7. `generate_evidence`
*(Skipped if `should_escalate` is True)*
- **Input**: `validation_results`, `decision`, `confidence_score`.
- **Action**: 
  - Passes the structured validation results to the LLM.
  - The LLM writes a concise, human-readable summary of why the bill scored the way it did.
- **Output**: Sets `evidence` (a string explanation).

## 8. `decide`
- **Input**: `decision`, `confidence_score`, `evidence`.
- **Action**: 
  - Writes the final `AgentDecisionRecord` to PostgreSQL.
  - If the decision is `"auto_approve"`, the agent completes and updates Neo4j to link the bill to the contract.
  - If the decision is anything else (e.g., `"escalate"`, `"flag_for_review"`), the agent writes a `ReviewQueue` row and calls `interrupt()`.
  - Calling `interrupt()` pauses the LangGraph execution. The agent is suspended until a human reviewer calls the `POST /review/{id}` endpoint, at which point the agent resumes and concludes.
- **Output**: Agent terminates or pauses.
