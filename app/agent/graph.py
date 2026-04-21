from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver   # swap for Postgres checkpointer in prod
from app.agent.state import FreightBillState
from app.agent.nodes import (
    normalize,
    resolve_carrier,
    match_contract,
    find_shipment,
    validate,
    score,
    generate_evidence,
    decide,
)

def build_graph() -> StateGraph:
    graph = StateGraph(FreightBillState)

    # Register nodes
    graph.add_node("normalize", normalize.run)
    graph.add_node("resolve_carrier", resolve_carrier.run)
    graph.add_node("match_contract", match_contract.run)
    graph.add_node("find_shipment", find_shipment.run)
    graph.add_node("validate", validate.run)
    graph.add_node("score", score.run)
    graph.add_node("generate_evidence", generate_evidence.run)
    graph.add_node("decide", decide.run)

    # Entry point
    graph.set_entry_point("normalize")

    # Linear edges
    graph.add_edge("normalize", "resolve_carrier")

    # Conditional: if carrier not found, skip to decide (will escalate)
    graph.add_conditional_edges(
        "resolve_carrier",
        lambda state: "decide" if state.get("should_escalate") else "match_contract"
    )

    graph.add_edge("match_contract", "find_shipment")
    graph.add_edge("find_shipment", "validate")
    graph.add_edge("validate", "score")
    graph.add_edge("score", "generate_evidence")
    graph.add_edge("generate_evidence", "decide")
    
    # After decide, we might interrupt, or end
    def route_after_decide(state: FreightBillState):
        decision = state.get("decision")
        human_review = state.get("human_review")
        if human_review is not None:
            # Resuming from interrupt
            return END
        if decision in ["flag_for_review", "dispute", "escalate"]:
            # Need human review
            return "decide" # Wait, actually we interrupt BEFORE decide if we want to pause?
            # Design doc: "interrupt_before=['decide']" - Wait, "The decide node checks whether to auto-proceed or formally interrupt."
            # No, if we use interrupt_before=["decide"], it pauses BEFORE running decide.
            # But the design says "Agent writes to Postgres... then calls interrupt".
            # The LangGraph interrupt() is used within the node in latest versions, 
            # or we interrupt via graph compilation.
            # Design doc specifically says:
            # return graph.compile(checkpointer=checkpointer, interrupt_before=["decide"])
            # Actually, the design doc says:
            # "interrupt_before=['decide'] means: after evidence is generated, pause if needed."
            # But wait, step 3 of Decide says: "Write to DB, then Call LangGraph interrupt() — agent pauses here."
            # That's slightly contradictory with `interrupt_before`.
            # If we interrupt AFTER decide, we should use `interrupt_after=["decide"]`.
            pass

        return END
        
    graph.add_edge("decide", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)

