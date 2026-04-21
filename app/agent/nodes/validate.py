from app.agent.state import FreightBillState, ValidationResult
from app.db.postgres import AsyncSessionLocal
from app.db.models import FreightBill
from sqlalchemy import select
from datetime import datetime

async def run(state: FreightBillState) -> dict:
    freight_bill = state["freight_bill"]
    matched_contract = state.get("matched_contract")
    matched_rate_card = state.get("matched_rate_card")
    matched_shipment = state.get("matched_shipment")
    matched_bols = state.get("matched_bols", [])
    prior_bills = state.get("prior_bills_on_shipment", [])
    
    results = []
    is_duplicate = False

    async with AsyncSessionLocal() as session:
        # 1. DUPLICATE CHECK
        query = select(FreightBill.id).where(
            FreightBill.bill_number == freight_bill["bill_number"],
            FreightBill.carrier_id == freight_bill.get("carrier_id"),
            FreightBill.id != freight_bill["id"]
        )
        result = await session.execute(query)
        dup = result.scalars().first()
        if dup:
            results.append({
                "check": "duplicate_check",
                "passed": False,
                "detail": f"Duplicate bill detected. Matches existing bill {dup}",
                "severity": "fail"
            })
            is_duplicate = True
        else:
            results.append({
                "check": "duplicate_check",
                "passed": True,
                "detail": "No prior bill with this number",
                "severity": "pass"
            })

    # If duplicate, terminal fail, no further checks matter as much
    if is_duplicate:
        return {"validation_results": results, "is_duplicate": True}

    # 2. WEIGHT vs BOL
    billed_weight = freight_bill["billed_weight_kg"]
    if matched_bols:
        actual_weight = sum(b["actual_weight_kg"] for b in matched_bols)
        if billed_weight > actual_weight:
            deviation = (billed_weight - actual_weight) / actual_weight * 100
            severity = "fail" if deviation > 5 else "warning"
            results.append({
                "check": "weight_match",
                "passed": False,
                "detail": f"Billed {billed_weight}kg, BOL confirms {actual_weight}kg delivered",
                "severity": severity
            })
        else:
            results.append({
                "check": "weight_match",
                "passed": True,
                "detail": f"Billed {billed_weight}kg matches BOL {actual_weight}kg",
                "severity": "pass"
            })

    # 3. CUMULATIVE WEIGHT CHECK
    if matched_shipment:
        sum_billed = sum(pb["billed_weight_kg"] for pb in prior_bills) + billed_weight
        shipment_total = matched_shipment["total_weight_kg"]
        if sum_billed > shipment_total:
            results.append({
                "check": "cumulative_weight",
                "passed": False,
                "detail": f"Total billed across all bills ({sum_billed}kg) exceeds shipment weight ({shipment_total}kg)",
                "severity": "fail"
            })
        else:
            results.append({
                "check": "cumulative_weight",
                "passed": True,
                "detail": f"Cumulative weight {sum_billed}kg within shipment total {shipment_total}kg",
                "severity": "pass"
            })

    # RATE AND CONTRACT CHECKS
    if matched_contract and matched_rate_card:
        # 6. CONTRACT VALIDITY
        if matched_contract["status"] == "expired":
            results.append({
                "check": "contract_validity",
                "passed": False,
                "detail": f"Contract {matched_contract['id']} expired on {matched_contract['expiry_date']}",
                "severity": "fail"
            })

        # 4. RATE CHECK
        contracted_rate = matched_rate_card.get("rate_per_kg")
        billed_rate = freight_bill.get("rate_per_kg")
        if contracted_rate is not None and billed_rate is not None:
            deviation_pct = abs(billed_rate - contracted_rate) / contracted_rate * 100
            # using 2.0% as drift tolerance
            if deviation_pct > 2.0:
                severity = "fail" if deviation_pct > 10 else "warning"
                results.append({
                    "check": "rate_check",
                    "passed": False,
                    "detail": f"Billed ₹{billed_rate}/kg, contracted ₹{contracted_rate}/kg ({deviation_pct:.1f}% drift)",
                    "severity": severity
                })
            else:
                results.append({
                    "check": "rate_check",
                    "passed": True,
                    "detail": f"₹{billed_rate}/kg matches contract",
                    "severity": "pass"
                })

        # 5. FUEL SURCHARGE CHECK
        bill_date = datetime.strptime(str(freight_bill["bill_date"]), "%Y-%m-%d").date()
        revised_on_str = matched_rate_card.get("revised_on")
        revised_on = datetime.strptime(revised_on_str, "%Y-%m-%d").date() if revised_on_str else None
        
        if revised_on and bill_date >= revised_on:
            correct_surcharge_pct = matched_rate_card.get("revised_fuel_surcharge_percent", 0.0)
        else:
            correct_surcharge_pct = matched_rate_card.get("fuel_surcharge_percent", 0.0)
            
        base_charge = freight_bill["base_charge"]
        expected_surcharge = base_charge * correct_surcharge_pct / 100.0
        billed_fuel_surcharge = freight_bill["fuel_surcharge"]
        
        if abs(billed_fuel_surcharge - expected_surcharge) > 1.0:
            results.append({
                "check": "fuel_surcharge",
                "passed": False,
                "detail": f"Fuel surcharge: billed ₹{billed_fuel_surcharge}, expected ₹{expected_surcharge} at {correct_surcharge_pct}%",
                "severity": "fail"
            })
        else:
            results.append({
                "check": "fuel_surcharge",
                "passed": True,
                "detail": f"Fuel surcharge ₹{billed_fuel_surcharge} matches expected at {correct_surcharge_pct}%",
                "severity": "pass"
            })

        # 7. UNIT OF MEASURE CHECK
        if matched_rate_card.get("unit") == "FTL":
            ftl_amount = matched_rate_card.get("rate_per_unit", 0)
            alt_rate = matched_rate_card.get("alternate_rate_per_kg", 0)
            per_kg_amount = billed_weight * alt_rate
            
            if freight_bill.get("billing_unit") == "kg":
                if abs(base_charge - per_kg_amount) < 1.0:
                    results.append({
                        "check": "uom_check",
                        "passed": True,
                        "detail": f"Billed per-kg alternate rate correctly (₹{base_charge})",
                        "severity": "warning" # Semantically warning as per spec
                    })
                else:
                    results.append({
                        "check": "uom_check",
                        "passed": False,
                        "detail": f"Alternate per-kg billing incorrect. Expected ₹{per_kg_amount}, billed ₹{base_charge}",
                        "severity": "fail"
                    })
            else:
                if abs(base_charge - ftl_amount) < 1.0:
                    pass # Pass silently
                else:
                    results.append({
                        "check": "uom_check",
                        "passed": False,
                        "detail": f"FTL amount incorrect. Expected ₹{ftl_amount}, billed ₹{base_charge}",
                        "severity": "fail"
                    })
                    
        # 8. MINIMUM CHARGE CHECK
        min_charge = matched_rate_card.get("min_charge", 0)
        if base_charge < min_charge:
            results.append({
                "check": "min_charge",
                "passed": False,
                "detail": f"Base charge ₹{base_charge} below contract minimum ₹{min_charge}",
                "severity": "warning"
            })

    return {"validation_results": results, "is_duplicate": is_duplicate}
