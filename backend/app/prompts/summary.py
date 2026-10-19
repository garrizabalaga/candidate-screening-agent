"""Prompt y tool para las notas del resumen post-conversación."""

from app.models.candidate import Candidate
from app.models.message import Message
from app.models.summary import SummaryNotes

TOOL = {
    "name": "record_summary_notes",
    "description": "Registra las notas del resumen para el recruiter.",
    "inputSchema": {"json": SummaryNotes.model_json_schema()},
}

SYSTEM = f"""Preparas notas para un recruiter de Grupo Sazón que va a revisar a un candidato a repartidor.
Lee la conversación entre el asistente virtual (Guille) y el candidato y llama a {TOOL["name"]} con:
- context: hechos que el candidato contó y que NO son datos del screening (los datos ya los tiene el recruiter).
- open_questions: preguntas del candidato que el asistente no supo responder.
- alerts: dudas, contradicciones o cosas que convenga confirmar en la entrevista.

Reglas:
- Solo lo que aparece en la conversación. No supongas ni completes nada.
- Frases cortas y concretas, en español. Como mucho 4 elementos por lista; listas vacías si no hay nada.
- No valores al candidato ni recomiendes si contratarle: eso lo decide el recruiter."""


def user_prompt(candidate: Candidate, messages: list[Message], key_data: str) -> str:
    transcript = "\n".join(f"{'Guille' if m.role == 'agent' else 'Candidato'}: {m.text}" for m in messages)
    return (
        f"Candidato: {candidate.full_name}.\n"
        f"Datos del screening que ya tiene el recruiter (no los repitas): {key_data}\n\n"
        f"Conversación:\n{transcript}"
    )
