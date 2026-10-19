"""Tests del agente con un LLM guionizado (sin red): cada llamada a converse()
devuelve la siguiente respuesta de la lista y se guarda cómo se llamó."""

from datetime import date, datetime, timezone

import pytest

from app.clients.aws_client import LLMError
from app.models.candidate import Candidate
from app.models.enums import Channel, Country, Identity, License, Stage
from app.models.interview import Slot
from app.models.knowledge import KnowledgeMatch
from app.models.message import Message
from app.services.agent_service import MAX_STEPS, AgentService
from app.services.state_machine import Action
from app.tools import record_answer, search_faq

TODAY = date(2026, 10, 15)  # jueves


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def converse(self, system, messages, tools=None, force_tool=None, **kwargs):
        self.calls.append({"system": system, "messages": list(messages), "force_tool": force_tool, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def record(**fields):
    return [{"toolUse": {"toolUseId": "t1", "name": record_answer.NAME, "input": {"language": "es", **fields}}}]


def faq(question, tool_use_id="t2"):
    return [{"toolUse": {"toolUseId": tool_use_id, "name": search_faq.NAME, "input": {"question": question}}}]


def say(text):
    return [{"text": text}]


def candidate(**kwargs) -> Candidate:
    data = {
        "id": "c1",
        "full_name": "Ana López",
        "phone": "+34600000000",
        "country": Country.ES,
        "applied_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
    }
    return Candidate(**(data | kwargs))


def history(*texts):
    now = datetime.now(timezone.utc)
    roles = ["agent", "candidate"] * len(texts)
    return [Message(candidate_id="c1", role=r, text=t, created_at=now) for r, t in zip(roles, texts)]


def tool_result(call) -> dict:
    """El toolResult que el servicio envió al agente en esa llamada."""
    return call["messages"][-1]["content"][0]["toolResult"]["content"][0]["json"]


def respond(llm, c=None, *texts):
    texts = texts or ("¿Hablo con Ana López?", "x")
    return AgentService(llm, FakeScheduling(), FakeKnowledge()).respond(c or candidate(), history(*texts), texts[-1], TODAY)


class FakeKnowledge:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.questions = []

    def search(self, question):
        self.questions.append(question)
        if self.fail:
            raise LLMError("Titan caído")
        return [KnowledgeMatch(question="¿Necesito vehículo propio?", answer="Sí, coche o moto.", score=0.6)]


class FakeScheduling:
    """Agenda falsa: siempre ofrece los mismos huecos y reserva sin comprobar nada."""

    SLOTS = [
        Slot(recruiter_id="r01", recruiter_name="Laura Martín", starts_at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)),
        Slot(recruiter_id="r02", recruiter_name="Javier Ruiz", starts_at=datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)),
    ]

    def __init__(self):
        self.booked = []

    def available_slots(self, candidate):
        return self.SLOTS

    def book(self, candidate, slot):
        self.booked.append(slot)


# --- Protocolo del turno -----------------------------------------------------------

def test_turn_forces_record_answer_then_writes_the_reply():
    llm = ScriptedLLM(record(identity_confirmed="yes"), say("¡Genial, Ana! ¿Tienes carné?"))
    turn = respond(llm, None, "¿Hablo con Ana López?", "sí soy yo")

    assert turn.reply == "¡Genial, Ana! ¿Tienes carné?"
    assert (turn.decision.action, turn.decision.stage) == (Action.ASK, Stage.LICENSE)
    assert turn.decision.candidate.fields.identity_confirmed == Identity.YES

    first, second = llm.calls
    assert first["force_tool"] == record_answer.NAME and first["temperature"] == 0.0
    assert second["force_tool"] is None  # modo auto: el agente decide si usa search_faq
    assert "carné de conducir" in tool_result(second)["objective"]  # el código le dice qué toca


@pytest.mark.parametrize("titan_down", [False, True])
def test_question_is_answered_with_search_faq(titan_down):
    knowledge = FakeKnowledge(fail=titan_down)
    llm = ScriptedLLM(
        record(candidate_question="si hace falta moto propia"),
        faq("¿Necesito vehículo propio para trabajar de repartidor?"),
        say("Sí, necesitas coche o moto propios. ¿En qué ciudad te gustaría repartir?"),
    )
    turn = AgentService(llm, FakeScheduling(), knowledge).respond(
        candidate(stage=Stage.ZONE), history("¿En qué ciudad?", "¿hace falta moto?"), "¿hace falta moto?", TODAY
    )

    assert turn.decision.action == Action.ASK and turn.decision.candidate.reask_counts == {}
    assert "candidate_asked" in tool_result(llm.calls[1])
    assert knowledge.questions == ["¿Necesito vehículo propio para trabajar de repartidor?"]
    result = tool_result(llm.calls[2])
    assert "Ciudades con servicio en España" in result["information"]
    if titan_down:
        assert result["faq"] == [] and "faq_note" in result
    else:
        assert result["faq"] == [{"question": "¿Necesito vehículo propio?", "answer": "Sí, coche o moto."}]


def test_parallel_tool_calls_in_the_forced_step_all_get_a_result():
    both = record(candidate_question="dónde hay servicio") + faq("dónde hay servicio")
    llm = ScriptedLLM(both, say("Estamos en Madrid, Sevilla... ¿Dónde te gustaría repartir?"))
    respond(llm, candidate(stage=Stage.ZONE), "¿Ciudad?", "¿dónde estáis?")

    results = llm.calls[1]["messages"][-1]["content"]
    assert [r["toolResult"]["toolUseId"] for r in results] == ["t1", "t2"]
    assert "objective" in results[0]["toolResult"]["content"][0]["json"]
    assert "information" in results[1]["toolResult"]["content"][0]["json"]


def test_record_answer_is_not_executed_twice():
    llm = ScriptedLLM(record(license="yes"), record(license="no"), say("¿En qué ciudad?"))
    turn = respond(llm, candidate(stage=Stage.LICENSE), "¿Carné?", "sí")
    assert turn.decision.candidate.fields.license == License.YES
    assert "error" in tool_result(llm.calls[2])


def test_invalid_field_is_dropped_and_reasked():
    llm = ScriptedLLM(record(license="maybe", city="Madrid"), say("¿Tienes carné de coche o moto?"))
    turn = respond(llm, candidate(stage=Stage.LICENSE), "¿Carné?", "x")
    assert turn.decision.candidate.fields.license is None
    assert turn.decision.candidate.fields.city == "Madrid"
    assert turn.decision.action == Action.REASK


def test_system_prompt_has_calendar_current_question_and_neutral_gender():
    llm = ScriptedLLM(record(), say("¿Tienes carné?"))
    respond(llm, candidate(stage=Stage.LICENSE), "¿Carné?", "x")
    system = llm.calls[0]["system"]
    assert "jueves 2026-10-15" in system and "lunes 2026-10-19" in system
    assert "le preguntaba si tiene carné de conducir" in system
    assert "formas neutras" in system


# --- Fallos antes y después de decidir ----------------------------------------------

@pytest.mark.parametrize("first", [say("no llamo a la tool"), LLMError("Bedrock caído")])
def test_failure_before_the_decision_raises(first):
    with pytest.raises(LLMError):
        respond(ScriptedLLM(first))


@pytest.mark.parametrize(
    "after_decision",
    [
        [LLMError("Bedrock caído")],              # Bedrock falla al redactar
        [faq("x")] * (MAX_STEPS + 1),             # bucle de tools sin responder
        [say(""), say("")],                       # vacío también al reescribir
    ],
)
def test_failure_after_the_decision_uses_the_fixed_text(after_decision):
    llm = ScriptedLLM(record(identity_confirmed="yes"), *after_decision)
    turn = respond(llm)
    assert turn.decision.stage == Stage.LICENSE  # la decisión se mantiene
    assert turn.reply == "¿Puedes conducir un coche o una moto? Es decir, ¿tienes carné de coche o de moto en vigor?"


# --- Validación del mensaje ----------------------------------------------------------

def test_invalid_reply_is_rewritten_once():
    llm = ScriptedLLM(record(identity_confirmed="yes"), say("Perfecto, Ana."), say("Perfecto, Ana. ¿Tienes carné?"))
    turn = respond(llm)
    assert turn.reply == "Perfecto, Ana. ¿Tienes carné?"
    rewrite = llm.calls[2]["messages"][-1]["content"][0]["text"]
    assert "Mensaje del sistema" in rewrite and "no tiene ninguna" in rewrite


def test_invented_salary_falls_back_to_the_fixed_text():
    salary = say("Se paga a 12 €/hora. ¿Qué disponibilidad tienes?")
    llm = ScriptedLLM(record(candidate_question="cuánto se paga"), salary, salary)
    c = candidate(stage=Stage.AVAILABILITY, fields={"identity_confirmed": "yes", "license": "yes", "city": "Madrid"})
    turn = respond(llm, c, "¿Disponibilidad?", "¿cuánto se paga?")
    assert turn.reply == "¿Buscas algo a jornada completa, a media jornada o solo para fines de semana?"  # texto fijo


def test_voice_only_changes_how_the_reply_is_written():
    script = (record(identity_confirmed="yes"), say("¡Genial, Ana! 🙌 ¿Tienes carné?"))
    by_text = respond(ScriptedLLM(*script))
    voice_llm = ScriptedLLM(*script)
    by_voice = AgentService(voice_llm, FakeScheduling(), FakeKnowledge()).respond(
        candidate(), history("¿Hablo con Ana López?", "x"), "x", TODAY, channel=Channel.VOICE
    )
    assert (by_voice.decision.action, by_voice.decision.stage) == (by_text.decision.action, by_text.decision.stage)
    assert by_voice.reply == "¡Genial, Ana! ¿Tienes carné?" and "🙌" in by_text.reply
    assert "llamada de voz" in voice_llm.calls[0]["system"]


def test_truncated_reply_is_rewritten():
    cut = [{"text": "Genial, Ana. ¿Tienes carné de"}, {"truncated": True}]
    llm = ScriptedLLM(record(identity_confirmed="yes"), cut, say("Genial, Ana. ¿Tienes carné?"))
    turn = respond(llm)
    assert turn.reply == "Genial, Ana. ¿Tienes carné?"
    assert "cortado" in llm.calls[2]["messages"][-1]["content"][0]["text"]
