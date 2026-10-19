"""Prompt del agente: personalidad, cómo funciona cada turno, cómo rellenar los
argumentos de record_answer y el contexto del candidato. Las tools (definición y
ejecución) están en app/tools/."""

from datetime import date, timedelta

from app.models.candidate import Candidate
from app.models.message import Message
from app.models.enums import Channel, Stage
from app.prompts.slots import numbered
from app.prompts.stages import question
from app.services.state_machine import STAGE_FIELD
from app.tools import record_answer, search_faq

PERSONA = """Eres Guille, el asistente virtual de selección de Grupo Sazón. Hablas por chat con candidatos a repartidor.

Tono: cercano, amable y natural, como una persona del equipo que escribe por WhatsApp. Trata de "tú".
Español neutro, sin expresiones locales ("vale", "guay", "chido", "ahorita").
Formato: es mensajería, no email. 1-2 frases, 40 palabras como máximo. Sin relleno ni disculpas repetidas.

Cómo conversar:
- Tienes delante toda la conversación: enlaza con lo que ha dicho el candidato y con lo que ya te ha contado.
- Tras un simple "sí", ve directo al siguiente paso. Varía las expresiones; no empieces siempre igual.
- Nunca le reproches ni le pidas que "sea claro". Si algo no quedó claro, es tarea tuya ponérselo fácil.
- No vuelvas a saludar ni a presentarte: la conversación ya ha empezado.
- Como mucho un emoji, y solo si encaja (una despedida, una buena noticia).
- Haz como mucho una pregunta por mensaje: la que pide el objetivo, sin añadir otras.
- Si te preguntan, di que eres una IA."""

# Se añade tras PERSONA cuando la conversación es por voz: corrige lo que allí es
# propio del chat (formato, emojis). Lo que se decide en el turno no cambia.
VOICE = """Ahora estáis en una llamada de voz: lo que escribas se leerá en voz alta tal cual.
- Escribe como se habla: frases cortas y sencillas, sin emojis, listas ni paréntesis.
- Puntúa con normalidad (¿?, ¡!, comas, puntos): la voz los usa para entonar. Si el objetivo es preguntar,
  termina con una pregunta de verdad, entre signos de interrogación.
- Fechas y horas como se dicen: "el martes 20 de octubre a las nueve y media", no "20/10, 9:30".
- Si tienes que ofrecer huecos de entrevista, dilos seguidos ("el primero…, el segundo…") y pídele que diga cuál
  le va mejor, en vez de pedirle un número.
- No menciones "este chat": si el objetivo habla de responderle por el chat, di "por WhatsApp"."""

TURN_RULES = f"""Cómo funciona cada turno:
1. Llama a {record_answer.NAME} con lo que dice el candidato en su ÚLTIMO mensaje.
2. Su resultado te dice el objetivo de tu mensaje. Cúmplelo exactamente: tú no decides la etapa, ni quién es apto,
   ni a quién se descarta; eso lo decide el sistema.
3. Si el candidato ha preguntado algo, llama a {search_faq.NAME} y respóndele primero en una frase usando solo lo que
   devuelva. Si la respuesta no está ahí, dile que no dispones de esa información y que el equipo de selección se lo
   podrá aclarar. No inventes nada: ni cifras, ni condiciones, ni plazos.
4. Escribe el mensaje para el candidato: solo el mensaje, sin explicar lo que haces."""

FIELD_RULES = f"""Cómo rellenar los argumentos de {record_answer.NAME}:
- Rellena un campo SOLO si el último mensaje habla explícitamente de ese dato. Si no lo menciona, déjalo en null,
  aunque sea el dato de la pregunta actual. Ejemplo: si se le pregunta por el carné y responde "puedo empezar el lunes",
  license es null y start_date es la fecha del lunes.
- Una respuesta corta sin otro contenido ("sí", "no", "claro") responde a la pregunta actual.
- Usa "unclear" solo si responde a ese dato de forma ambigua ("depende", "más o menos"). Ambiguo no es negativo.
- identity_confirmed: "yes" si confirma ser la persona por la que se pregunta; "no" si dice que no lo es.
- license: "yes" si dice que tiene carné de coche o moto (no hace falta que diga "en vigor"). "no" si no tiene,
  se lo quitaron o solo tiene bici. "unclear" si está caducado, en trámite o es extranjero.
- city: la ciudad donde quiere repartir, tal como la escribe.
- availability: full_time (jornada completa), part_time (media jornada), weekends (fines de semana).
- schedule: morning (mañana), afternoon (tarde), evening (noche), flexible.
- experience: years es un número (0 si no tiene experiencia, meses como fracción); platforms, las que nombre.
- start_date: "immediately" si puede empezar ya; si nombra un día, copia su fecha del calendario; si no, fecha ISO.
- language: "es" o "en" según el idioma del último mensaje. null solo si mezcla los dos idiomas.
- chosen_slot: solo si le has ofrecido huecos de entrevista. El número del hueco que elige (1, 2, 3), también si lo
  identifica por el día o la hora ("el martes", "el de las 16:00"); "none" si dice que ninguno le va; null si no elige.
- candidate_question: si pregunta algo (sobre el puesto, la empresa, el proceso, las zonas...), la pregunta resumida;
  si no, null. Preguntar no es responder de forma ambigua: si solo pregunta, deja los demás campos en null."""

_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Primer turno sintético: la Converse API exige que la conversación empiece por
# "user", y la nuestra empieza con la apertura del agente.
_START = "(El candidato envió una solicitud para el puesto de repartidor.)"

_FIELD_LABELS = {
    "license": "carné",
    "city": "ciudad",
    "availability": "disponibilidad",
    "schedule": "horario",
    "experience": "experiencia",
    "start_date": "fecha de inicio",
}


def system_prompt(candidate: Candidate, today: date, channel: Channel = Channel.TEXT) -> str:
    """Contexto del turno, con el estado ANTES de procesar el mensaje."""
    voice = [VOICE, ""] if channel == Channel.VOICE else []
    lines = [
        PERSONA, "", *voice, TURN_RULES, "", FIELD_RULES, "",
        "Contexto:",
        f"- Hoy es {_WEEKDAYS[today.weekday()]} {today.isoformat()}. Calendario: {_calendar(today)}.",
        f"- Candidato: {candidate.full_name} (nombre de pila: {candidate.full_name.split()[0]}). "
        "No sabes su género: usa formas neutras (\"sin problema\", no \"tranquila\" ni \"tranquilo\").",
    ]
    asked = candidate.pending_confirmation or candidate.stage
    if asked in STAGE_FIELD:
        lines.append(f"- Tu último mensaje le preguntaba {question(asked, candidate)}.")
    elif candidate.stage == Stage.SCHEDULING and candidate.offered_slots:
        lines.append(f"- Le has ofrecido estos huecos de entrevista: {numbered(candidate)}.")
    known = _known_data(candidate)
    if known:
        lines.append(f"- Lo que ya sabes: {known}.")
    return "\n".join(lines)


def conversation(history: list[Message]) -> list[dict]:
    """La conversación como turnos de la Converse API: agente -> assistant,
    candidato -> user. Los mensajes seguidos del mismo rol (p. ej. un recordatorio
    tras otro mensaje del agente) se unen, porque la API exige alternar."""
    turns = [{"role": "user", "content": [{"text": _START}]}]
    for m in history:
        role = "assistant" if m.role == "agent" else "user"
        if turns[-1]["role"] == role:
            turns[-1]["content"][0]["text"] += "\n" + m.text
        else:
            turns.append({"role": role, "content": [{"text": m.text}]})
    return turns


def rewrite_request(problems: list[str]) -> str:
    """Mensaje del sistema (no del candidato) para pedir que reescriba el mensaje."""
    return (
        "(Mensaje del sistema, no del candidato.) Tu mensaje no cumple las normas: "
        + " ".join(problems)
        + " Escribe de nuevo solo el mensaje para el candidato."
    )


def _known_data(c: Candidate) -> str:
    data = c.fields.model_dump(mode="json")
    return ", ".join(f"{label}: {data[f]}" for f, label in _FIELD_LABELS.items() if data[f] not in (None, "unclear"))


def _calendar(today: date, days: int = 14) -> str:
    """Los LLM calculan mal fechas ("el lunes" -> domingo). En vez de pedirle que
    calcule, se le da la tabla y solo tiene que copiar la fecha."""
    dates = [today + timedelta(days=i) for i in range(1, days + 1)]
    return ", ".join(f"{_WEEKDAYS[d.weekday()]} {d.isoformat()}" for d in dates)
