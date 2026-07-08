"""
LangGraph orchestration graph where each node delegates to Hermes
(`AIAgent` from run_agent.py) as an autonomous sub-agent.

Fixes vs. the original:
  1. `task` is now part of the invoke() input, so research_node doesn't KeyError.
  2. `messages` uses an `add_messages`-style reducer so nodes can safely
     append log entries instead of silently overwriting/being ignored.
  3. Added a `review_node` that actually uses `quality_approved`, with a
     conditional edge that loops back to `programador` on rejection
     instead of leaving that field dead in the schema.
  4. Centralized AIAgent construction so quiet_mode/max_iterations are
     consistent across nodes (tweak once, applies everywhere).
  5. Final block prints plain state fields instead of calling
     `.pretty_print()` on tuples that were never real messages.
"""

from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from run_agent import AIAgent


# ---------------------------------------------------------------------
# 1. Graph state
# ---------------------------------------------------------------------

class AgentState(TypedDict):
    task: str
    research_notes: str
    code_generated: str
    quality_approved: bool
    messages: Annotated[list, add_messages]  # accumulates instead of overwriting


# ---------------------------------------------------------------------
# 2. Shared Hermes agent factory (centralized config)
# ---------------------------------------------------------------------

def make_agent(max_iterations: int = 10) -> AIAgent:
    return AIAgent(quiet_mode=True, max_iterations=max_iterations)


# ---------------------------------------------------------------------
# 3. Nodes
# ---------------------------------------------------------------------

def research_node(state: AgentState) -> dict:
    agent = make_agent(max_iterations=10)

    prompt = (
        f"Investiga a fondo sobre el siguiente requerimiento y devuélveme "
        f"un reporte técnico detallado: {state['task']}"
    )
    result = agent.run_conversation(prompt)

    return {
        "research_notes": result["final_response"],
        "messages": [("assistant", f"[investigador] {result['final_response'][:200]}...")],
    }


def coding_node(state: AgentState) -> dict:
    agent = make_agent(max_iterations=15)

    # Include prior review feedback on retries, if present.
    feedback = ""
    if state.get("messages"):
        last_review = next(
            (
                m.content for m in reversed(state["messages"])
                if getattr(m, "content", "").startswith("[revisor]")
            ),
            None,
        )
        if last_review:
            feedback = f"\n\nFeedback del revisor a corregir: {last_review}"

    prompt = (
        f"Basándote en estas notas de investigación: {state['research_notes']}. "
        f"Escribe y ejecuta el script necesario.{feedback}"
    )
    result = agent.run_conversation(prompt)

    return {
        "code_generated": result["final_response"],
        "messages": [("assistant", f"[programador] {result['final_response'][:200]}...")],
    }


def review_node(state: AgentState) -> dict:
    agent = make_agent(max_iterations=5)

    prompt = (
        f"Revisa este código y determina si cumple el requerimiento original.\n"
        f"Requerimiento: {state['task']}\n"
        f"Código: {state['code_generated']}\n"
        f"Responde EXACTAMENTE con 'APROBADO' o 'RECHAZADO: <motivo>'."
    )
    result = agent.run_conversation(prompt)
    verdict = result["final_response"].strip()
    approved = verdict.upper().startswith("APROBADO")

    return {
        "quality_approved": approved,
        "messages": [("assistant", f"[revisor] {verdict}")],
    }


def route_after_review(state: AgentState) -> str:
    return END if state["quality_approved"] else "programador"


# ---------------------------------------------------------------------
# 4. Build the graph
# ---------------------------------------------------------------------

workflow = StateGraph(AgentState)

workflow.add_node("investigador", research_node)
workflow.add_node("programador", coding_node)
workflow.add_node("revisor", review_node)

workflow.add_edge(START, "investigador")
workflow.add_edge("investigador", "programador")
workflow.add_edge("programador", "revisor")
workflow.add_conditional_edges(
    "revisor",
    route_after_review,
    {"programador": "programador", END: END},
)

graph = workflow.compile()

# ---------------------------------------------------------------------
# 5. Run it
# ---------------------------------------------------------------------

if __name__ == "__main__":
    result = graph.invoke(
        {
            "task": "Implementar búsqueda híbrida en LanceDB combinando qwen3-embedding con FTS/BM25",
            "messages": [],
        }
    )

    print("\n--- research_notes ---")
    print(result["research_notes"])

    print("\n--- code_generated ---")
    print(result["code_generated"])

    print(f"\n--- quality_approved: {result['quality_approved']} ---")

    print("\n--- message log ---")
    for m in result["messages"]:
        print(f"[{m.type}] {m.content}")