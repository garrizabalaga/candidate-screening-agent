"""El agente: conversa y llama a tools; las tools ejecutan el código.

record_answer va forzada en todos los turnos, así la máquina de estados siempre
decide (aunque el mensaje sea "jaja"). Si algo falla ANTES de decidir, LLMError y
el turno no cuenta; si falla DESPUÉS, la decisión se comunica con el texto fijo.
El agente no guarda nada: el servicio de chat lo persiste todo junto.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date

from app.clients.aws_client import BedrockClient, LLMError, text_of, tool_uses, was_truncated
from app.models.candidate import Candidate
from app.models.enums import Channel
from app.models.message import Message
from app.prompts import agent, fallbacks
from app.services import reply_guard
from app.services.knowledge_service import KnowledgeService
from app.services.scheduling_service import SchedulingService
from app.services.state_machine import Decision
from app.tools import SPECS, record_answer, search_faq

logger = logging.getLogger(__name__)

MAX_STEPS = 3  # en modo auto, tras record_answer: p. ej. search_faq + respuesta = 2
MAX_REWRITES = 1


@dataclass
class AgentTurn:
    decision: Decision
    reply: str


class AgentService:
    def __init__(self, llm: BedrockClient, scheduling: SchedulingService, knowledge: KnowledgeService):
        self.llm = llm
        self.scheduling = scheduling
        self.knowledge = knowledge

    def respond(
        self, candidate: Candidate, history: list[Message], text: str, today: date, channel: Channel = Channel.TEXT
    ) -> AgentTurn:
        """`history` es la conversación hasta el último mensaje del candidato, incluido.
        Lanza LLMError solo si el código no ha podido decidir. El canal solo cambia
        cómo se redacta el mensaje: la decisión es la misma por chat que por voz."""
        turn = self._respond(candidate, history, text, today, channel)
        if channel == Channel.VOICE:
            turn.reply = reply_guard.for_voice(turn.reply)
        return turn

    def _respond(
        self, candidate: Candidate, history: list[Message], text: str, today: date, channel: Channel
    ) -> AgentTurn:
        system = agent.system_prompt(candidate, today, channel)
        messages = agent.conversation(history)

        # 1. record_answer forzada. Temperatura 0: la extracción tiene que ser repetible.
        content = self.llm.converse(system, messages, tools=SPECS, force_tool=record_answer.NAME, temperature=0.0)
        call = next((u for u in tool_uses(content) if u["name"] == record_answer.NAME), None)
        if call is None:
            raise LLMError("Agent did not call record_answer")

        # 2. El código decide (y, si toca, ofrece huecos o reserva).
        decision, extracted = record_answer.run(candidate, call["input"], text, today, self.scheduling)
        result = record_answer.result(decision, extracted.candidate_question)
        # El modelo puede llamar a varias tools a la vez (p. ej. también search_faq):
        # la API exige un resultado para cada llamada.
        results = [
            (c, result if c is call else self._run_tool(c, decision.candidate)) for c in tool_uses(content)
        ]
        messages += _exchange(content, results)

        # 3-4. El agente escribe el mensaje y el código lo valida.
        try:
            reply = self._write(system, messages, decision)
        except LLMError:
            logger.exception("Agent failed after the decision for candidate %s", candidate.id)
            reply = None
        if reply is None:
            return AgentTurn(decision=decision, reply=fallbacks.fallback(decision))
        return AgentTurn(decision=decision, reply=reply)

    def _write(self, system: str, messages: list[dict], decision: Decision) -> str | None:
        """El mensaje validado, o None si el agente no consigue uno válido."""
        rewrites = 0
        before = len(messages)
        for _ in range(MAX_STEPS + MAX_REWRITES):
            content = self.llm.converse(system, messages, tools=SPECS, temperature=0.5, max_tokens=300)
            calls = tool_uses(content)
            if calls:
                messages += _exchange(content, [(c, self._run_tool(c, decision.candidate)) for c in calls])
                continue

            reply = reply_guard.clean(text_of(content))
            issues = reply_guard.problems(reply, decision, context=_context(system, messages, before))
            if was_truncated(content):
                issues.append("El mensaje se ha cortado a medias: escríbelo más corto.")
            if not issues:
                return reply
            logger.warning("Agent reply rejected (%s): %r", "; ".join(issues), reply)
            if rewrites == MAX_REWRITES:
                return None
            rewrites += 1
            messages += [
                {"role": "assistant", "content": [{"text": reply or "(vacío)"}]},
                {"role": "user", "content": [{"text": agent.rewrite_request(issues)}]},
            ]
        return None

    def _run_tool(self, call: dict, candidate: Candidate) -> dict:
        match call["name"]:
            case search_faq.NAME:
                return search_faq.run(candidate, call["input"].get("question", ""), self.knowledge)
            case record_answer.NAME:
                # Solo una vez por turno: si lo intenta otra vez, no se vuelve a ejecutar advance().
                return {"error": "La respuesta de este mensaje ya está registrada. Escribe tu mensaje."}
            case _:
                return {"error": f"Tool desconocida: {call['name']}"}


def _context(system: str, messages: list[dict], before: int) -> str:
    """Lo que el agente tenía delante al escribir: el prompt, la conversación y los
    resultados de tools. Excluye lo que él mismo ha escrito en este turno (un
    mensaje rechazado no puede "validar" sus propias cifras inventadas)."""
    tool_results = [
        m for m in messages[before:]
        if m["role"] == "user" and any("toolResult" in block for block in m["content"])
    ]
    return system + json.dumps(messages[:before] + tool_results, ensure_ascii=False)


def _exchange(content: list[dict], results: list[tuple[dict, dict]]) -> list[dict]:
    """El turno del agente con sus llamadas y el turno con los resultados de cada una."""
    return [
        {"role": "assistant", "content": content},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": c["toolUseId"], "content": [{"json": r}]}} for c, r in results
        ]},
    ]
