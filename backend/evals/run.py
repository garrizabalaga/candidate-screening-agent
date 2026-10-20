"""Evals contra el LLM real (Bedrock): lo que los tests no ven, porque usan un LLM falso.

Uso (desde backend/):
    .venv\\Scripts\\python -m evals.run                     # todo (~45 llamadas a Bedrock)
    .venv\\Scripts\\python -m evals.run --only extraction   # solo extracción (~19 llamadas)

- Extracción: el paso 1 de un turno real (mismo prompt, record_answer forzada, misma
  validación) y se comparan los campos extraídos con los esperados.
- Escenarios: conversaciones por ChatService y AgentService reales sobre una SQLite en
  memoria. En cada turno se comprueba la acción decidida y que la respuesta la haya
  escrito el agente (si hizo falta el texto fijo, cuenta como fallo de redacción).

Informe por consola y en evals/results/latest.json.
"""

import argparse
import json
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

from app.clients.aws_client import BedrockClient, EmbeddingClient, tool_uses
from app.core.config import BEDROCK_AGENT_MODEL_ID, BEDROCK_EMBEDDING_MODEL_ID
from app.models.candidate import Candidate
from app.models.enums import Country, Stage
from app.models.interview import Slot
from app.models.message import Message
from app.prompts import agent
from app.prompts.slots import numbered
from app.prompts.stages import rephrase
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository
from app.services.agent_service import AgentService
from app.services.chat_service import ChatService
from app.services.knowledge_service import KnowledgeService
from app.services.scheduling_service import SchedulingService
from app.tools import SPECS, record_answer
from evals.cases import EXTRACTION, SCENARIOS

TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
PAUSE_SECONDS = 1  # cuota baja de la cuenta: sin pausa hay throttling
RESULTS = Path(__file__).parent / "results" / "latest.json"

ANSWERED = {
    Stage.IDENTITY: {"identity_confirmed": "yes"},
    Stage.LICENSE: {"license": "yes"},
    Stage.ZONE: {"city": "Madrid"},
    Stage.AVAILABILITY: {"availability": "full_time"},
    Stage.SCHEDULE: {"schedule": "morning"},
    Stage.EXPERIENCE: {"experience": {"years": 1, "platforms": ["Glovo"]}},
    Stage.START_DATE: {"start_date": "immediately"},
}
SLOTS = [
    Slot(recruiter_id="r01", recruiter_name="Laura Martín", starts_at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)),
    Slot(recruiter_id="r02", recruiter_name="Javier Ruiz", starts_at=datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)),
    Slot(recruiter_id="r03", recruiter_name="Marta Gómez", starts_at=datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)),
]


def candidate_at(stage: Stage) -> Candidate:
    """Un candidato que ya ha contestado bien todo lo anterior a `stage`."""
    order = list(Stage)
    fields = {k: v for s, answers in ANSWERED.items() if order.index(s) < order.index(stage) for k, v in answers.items()}
    return Candidate(
        id="c1", full_name="Ana López", phone="+34600000000", country=Country.ES, applied_at=NOW,
        contacted_at=NOW, stage=stage, fields=fields, offered_slots=SLOTS if stage == Stage.SCHEDULING else [],
    )


def last_question(c: Candidate) -> str:
    if c.stage == Stage.SCHEDULING:
        return f"¿Cuál de estos huecos te va mejor para la entrevista? {numbered(c)}"
    return rephrase(c.stage, c)


# --- Extracción --------------------------------------------------------------------------

def run_extraction(llm: BedrockClient) -> list[dict]:
    results = []
    for case in EXTRACTION:
        c = candidate_at(case["stage"])
        history = [
            Message(candidate_id=c.id, role="agent", text=last_question(c), created_at=NOW),
            Message(candidate_id=c.id, role="candidate", text=case["message"], created_at=NOW),
        ]
        content = llm.converse(agent.system_prompt(c, TODAY), agent.conversation(history), tools=SPECS,
                               force_tool=record_answer.NAME, temperature=0.0)
        call = next(u for u in tool_uses(content) if u["name"] == record_answer.NAME)
        got = record_answer.parse(call["input"]).model_dump(mode="json")
        wrong = {k: {"expected": v, "got": got.get(k)} for k, v in case["expected"].items() if not _same(v, got.get(k))}
        results.append({"stage": str(case["stage"]), "message": case["message"], "passed": not wrong, "wrong": wrong})
        _print(not wrong, f"[{case['stage']}] {case['message']}", wrong)
        time.sleep(PAUSE_SECONDS)
    return results


def _same(expected, got) -> bool:
    if expected == "*":
        return got is not None
    if expected is None or got is None:
        return expected is got
    if isinstance(expected, dict):
        return isinstance(got, dict) and all(_same(v, got.get(k)) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(got, list) and {_norm(x) for x in expected} == {_norm(x) for x in got}
    if isinstance(expected, str):
        return _norm(expected) == _norm(got)
    return expected == got


def _norm(value) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return text.casefold().strip()


# --- Escenarios ---------------------------------------------------------------------------

class RecordingAgent:
    """El agente real; guarda cada turno (decisión y respuesta) para comprobarlo."""

    def __init__(self, inner: AgentService):
        self.inner = inner
        self.turns = []

    def respond(self, *args, **kwargs):
        turn = self.inner.respond(*args, **kwargs)
        self.turns.append(turn)
        return turn


def run_scenarios(llm: BedrockClient) -> list[dict]:
    results = []
    for scenario in SCENARIOS:
        conn = get_connection(":memory:")
        init_db(conn)
        c = candidate_at(scenario["stage"])
        with conn:
            CandidateRepository(conn).create(c)
            MessageRepository(conn).add(Message(candidate_id=c.id, role="agent", text=last_question(c), created_at=NOW))
        knowledge = KnowledgeService(conn, EmbeddingClient(BEDROCK_EMBEDDING_MODEL_ID))  # sin FAQ cargado
        recorder = RecordingAgent(AgentService(llm, SchedulingService(conn, now=lambda: NOW), knowledge))
        chat = ChatService(conn, recorder, now=lambda: NOW)

        turns = []
        for message, expected in scenario["turns"]:
            before = len(recorder.turns)
            reply = chat.handle_message(c.id, message).reply
            problems = []
            if len(recorder.turns) == before:
                problems.append("Bedrock falló antes de decidir")
            else:
                turn = recorder.turns[-1]
                decision = turn.decision
                allowed = expected if isinstance(expected, tuple) else (expected,)
                if decision.action not in allowed:
                    problems.append(f"acción {decision.action} (esperada {expected})")
                if turn.used_fallback:
                    problems.append("hizo falta el texto fijo")
                if scenario.get("language") and decision.candidate.language != scenario["language"]:
                    problems.append(f"idioma {decision.candidate.language}")
                problems += [f"contiene «{t}»" for t in scenario.get("forbidden", []) if t.lower() in reply.lower()]
            turns.append({"message": message, "reply": reply, "problems": problems})
            time.sleep(PAUSE_SECONDS)
        passed = not any(t["problems"] for t in turns)
        results.append({"name": scenario["name"], "passed": passed, "turns": turns})
        _print(passed, scenario["name"], [p for t in turns for p in t["problems"]])
        for t in turns:
            print(f"      candidato: {t['message']}\n      guille:    {t['reply']}")
        conn.close()
    return results


# --- Informe ------------------------------------------------------------------------------

def _print(passed: bool, label: str, detail) -> None:
    print(f"  {'OK  ' if passed else 'FAIL'} {label}" + ("" if passed else f"  ->  {detail}"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evals contra el LLM real")
    parser.add_argument("--only", choices=["extraction", "scenarios"])
    args = parser.parse_args()

    llm = BedrockClient(BEDROCK_AGENT_MODEL_ID)
    report = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": BEDROCK_AGENT_MODEL_ID}
    if args.only != "scenarios":
        print("\nExtracción")
        report["extraction"] = run_extraction(llm)
    if args.only != "extraction":
        print("\nEscenarios")
        report["scenarios"] = run_scenarios(llm)

    print()
    for level in ("extraction", "scenarios"):
        if level in report:
            ok = sum(r["passed"] for r in report[level])
            report[f"{level}_score"] = f"{ok}/{len(report[level])}"
            print(f"{level}: {ok}/{len(report[level])}")
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Informe: {RESULTS}")


if __name__ == "__main__":
    main()
