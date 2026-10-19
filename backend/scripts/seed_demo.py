"""Datos de demostración: simula las últimas semanas de candidaturas con el código real.

Uso (desde backend/):
    .venv\\Scripts\\python -m scripts.seed_demo                 # data/demo.db, 60 días, 8 resúmenes
    .venv\\Scripts\\python -m scripts.seed_demo --summaries 0   # sin llamar a Bedrock
Luego, en el .env: DATABASE_PATH=data/demo.db

No inventa estados: recorre el tiempo con un reloj simulado y, en cada momento,
ejecuta lo mismo que en producción.
- Candidaturas (CandidateService) y primer contacto (OutreachService).
- Turnos del candidato por ChatService. Lo único que cambia es el agente: en vez
  del LLM, un guion da los argumentos de record_answer que daría el modelo y el
  mensaje es el texto fijo de esa acción (prompts/fallbacks.py). La tool, la
  máquina de estados y la agenda son las reales. Cero tokens.
- Recordatorios: el job real (ReengagementService) cada hora; es quien cierra
  los abandonos.
- Revisión manual: un recruiter resuelve cada caso entre 4 y 40 h después
  (ReviewService: aprueba el 60 %, que luego reserva entrevista). Los de las
  últimas --backlog-hours siguen pendientes, para que el panel tenga trabajo.
- Resúmenes: los genera el LLM de verdad, solo para una muestra de conversaciones
  terminadas (--summaries). El resto se generan con el botón del dashboard.

Semilla fija: mismos candidatos y conversaciones en cada ejecución (las fechas
son relativas al momento en que se ejecuta).
"""

import argparse
import heapq
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from itertools import count
from pathlib import Path

from app.clients.aws_client import BedrockClient
from app.core.config import BEDROCK_AGENT_MODEL_ID, COUNTRY_TIMEZONE, ROOT_DIR, list_service_cities
from app.models.candidate import Candidate
from app.models.enums import Channel, Country, Outcome, Stage
from app.prompts import fallbacks
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.services.agent_service import AgentTurn
from app.services.candidate_service import CandidateService
from app.services.chat_service import ChatService
from app.services.outreach_service import OutreachService
from app.services.reengagement_service import ReengagementService
from app.services.review_service import ReviewService
from app.services.scheduling_service import SchedulingService
from app.services.summary_service import SummaryService
from app.tools import record_answer

# --- Población -------------------------------------------------------------------------

FIRST_NAMES = {
    Country.ES: ["Lucía", "Hugo", "Martina", "Pablo", "Sofía", "Daniel", "Paula", "Álvaro", "Carla", "Adrián",
                 "Irene", "Javier", "Elena", "Sergio", "Nerea", "Iván", "Marta", "Rubén", "Noelia", "Óscar"],
    Country.MX: ["Valeria", "Santiago", "Ximena", "Diego", "Fernanda", "Emiliano", "Regina", "Mateo", "Camila",
                 "Leonardo", "Renata", "Iker", "Daniela", "Rodrigo", "Andrea", "Luis", "Mariana", "Jorge"],
}
LAST_NAMES = {
    Country.ES: ["García", "Fernández", "López", "Martínez", "Sánchez", "Pérez", "Gómez", "Ruiz", "Díaz", "Moreno",
                 "Álvarez", "Romero", "Navarro", "Torres", "Domínguez", "Gil", "Vázquez", "Serrano"],
    Country.MX: ["Hernández", "González", "Rodríguez", "Ramírez", "Flores", "Cruz", "Morales", "Reyes", "Jiménez",
                 "Mendoza", "Castillo", "Ortiz", "Vargas", "Guzmán", "Salazar", "Rojas"],
}
OUT_OF_ZONE = {Country.ES: ["Toledo", "Cáceres", "Lugo", "Teruel"], Country.MX: ["Oaxaca", "Mérida", "Tijuana"]}
PLATFORMS = ["Glovo", "Uber Eats", "Just Eat", "Deliveroo", "Rappi", "DiDi Food"]

# Guiones y su peso. Los de los últimos días quedan "en curso" solos: sus turnos caen en el futuro.
PERSONAS = {
    "qualified": 33,        # contesta todo y reserva entrevista
    "no_reply": 21,         # nunca contesta: recordatorios y abandono
    "abandon": 14,          # deja de contestar a mitad
    "no_license": 9,        # sin carné: confirma y se descarta
    "out_of_zone": 8,       # ciudad sin servicio: confirma y se descarta
    "review_license": 7,    # carné dudoso (caducado, extranjero): revisión manual
    "review_slot": 5,       # ningún hueco le va: revisión manual
    "wrong_contact": 3,     # no es quien aplicó
}


@dataclass
class Persona:
    kind: str
    country: Country
    city: str
    availability: str
    schedule: str
    years: float
    platforms: list[str]
    start_in_days: int | None  # None = puede empezar ya
    slot_choice: int
    stop_stage: Stage | None = None  # abandono: deja de contestar al llegar aquí
    replies: Counter = field(default_factory=Counter)


def make_persona(rng: random.Random, country: Country) -> Persona:
    kind = rng.choices(list(PERSONAS), weights=list(PERSONAS.values()))[0]
    cities = list_service_cities(country)
    years = rng.choice([0, 0, 0.5, 1, 2, 3, 5])
    persona = Persona(
        kind=kind,
        country=country,
        city=rng.choice(OUT_OF_ZONE[country]) if kind == "out_of_zone" else rng.choice(cities),
        availability=rng.choice(["full_time", "full_time", "part_time", "weekends"]),
        schedule=rng.choice(["morning", "afternoon", "evening", "flexible"]),
        years=years,
        platforms=rng.sample(PLATFORMS, k=rng.randint(1, 2)) if years else [],
        start_in_days=rng.choice([None, None, 3, 7, 14]),
        slot_choice=rng.randint(1, 3),
    )
    if kind == "abandon":
        persona.stop_stage = rng.choice(
            [Stage.LICENSE, Stage.ZONE, Stage.AVAILABILITY, Stage.SCHEDULE, Stage.EXPERIENCE, Stage.START_DATE, Stage.SCHEDULING]
        )
    return persona


# --- El guion: lo que diría el candidato y lo que extraería el LLM ---------------------------

AVAILABILITY_TEXT = {"full_time": "Jornada completa", "part_time": "Media jornada", "weekends": "Solo fines de semana"}
SCHEDULE_TEXT = {"morning": "Por las mañanas", "afternoon": "Mejor por la tarde", "evening": "Por la noche", "flexible": "Me da igual, soy flexible"}


def next_answer(p: Persona, c: Candidate, today: date) -> tuple[str, dict] | None:
    """El siguiente mensaje del candidato y los argumentos de record_answer, o None si no contesta."""
    if c.outcome is not None or p.kind == "no_reply" or c.stage == p.stop_stage:
        return None
    if c.pending_confirmation == Stage.LICENSE:
        return "No, ninguno. Solo tengo bici.", {"license": "no"}
    if c.pending_confirmation == Stage.ZONE:
        return f"No, solo podría en {p.city}.", {"city": p.city}
    match c.stage:
        case Stage.IDENTITY:
            if p.kind == "wrong_contact":
                return "No, creo que se han equivocado de número.", {"identity_confirmed": "no"}
            return "Sí, soy yo", {"identity_confirmed": "yes"}
        case Stage.LICENSE:
            if p.kind == "no_license":
                return "No tengo carné", {"license": "no"}
            if p.kind == "review_license":
                texts = ["Lo tengo caducado desde hace unos meses", "Es que lo estoy renovando", "Tengo uno de mi país, no sé si vale"]
                return texts[min(p.replies[c.stage], 2)], {"license": "unclear"}
            return "Sí, de moto", {"license": "yes"}
        case Stage.ZONE:
            return f"En {p.city}", {"city": p.city}
        case Stage.AVAILABILITY:
            return AVAILABILITY_TEXT[p.availability], {"availability": p.availability}
        case Stage.SCHEDULE:
            return SCHEDULE_TEXT[p.schedule], {"schedule": p.schedule}
        case Stage.EXPERIENCE:
            if not p.years:
                return "No, nunca he trabajado de repartidor", {"experience": {"years": 0, "platforms": []}}
            apps = " y ".join(p.platforms)
            return f"Sí, unos {p.years:g} años con {apps}", {"experience": {"years": p.years, "platforms": p.platforms}}
        case Stage.START_DATE:
            if p.start_in_days is None:
                return "Podría empezar ya mismo", {"start_date": "immediately"}
            start = today + timedelta(days=p.start_in_days)
            return f"A partir del {start.day}", {"start_date": start.isoformat()}
        case Stage.SCHEDULING:
            if p.kind == "review_slot":
                return "Ninguno me cuadra, trabajo a esas horas", {"chosen_slot": "none"}
            return f"El {p.slot_choice}, por favor", {"chosen_slot": p.slot_choice}
    return None


class ScriptedAgent:
    """Sustituye al LLM en ChatService: los argumentos de record_answer salen del guion
    y el mensaje es el texto fijo de la acción. La tool y la máquina de estados son las reales."""

    def __init__(self, scheduling: SchedulingService, args: dict):
        self.scheduling = scheduling
        self.args = args

    def respond(self, candidate, history, text, today, channel=Channel.TEXT) -> AgentTurn:
        decision, _ = record_answer.run(candidate, self.args | {"language": "es"}, text, today, self.scheduling)
        return AgentTurn(decision=decision, reply=fallbacks.fallback(decision))


# --- Simulación ------------------------------------------------------------------------

def arrival_times(rng: random.Random, start: datetime, end: datetime, per_day: int) -> list[tuple[datetime, Country]]:
    """Candidaturas: más entre semana, entre las 8 y las 23 h locales; 3 de cada 4 de España."""
    arrivals = []
    day = start.date()
    while day <= end.date():
        n = per_day if day.weekday() < 5 else per_day // 2
        for _ in range(max(0, round(rng.gauss(n, n * 0.25)))):
            country = Country.ES if rng.random() < 0.75 else Country.MX
            local = datetime.combine(day, time(8), COUNTRY_TIMEZONE[country]) + timedelta(minutes=rng.randint(0, 15 * 60))
            when = local.astimezone(timezone.utc)
            if start <= when <= end:
                arrivals.append((when, country))
        day += timedelta(days=1)
    return sorted(arrivals)


def reply_delay(rng: random.Random, first: bool) -> timedelta:
    """Cuánto tarda en contestar. El primer mensaje: casi siempre minutos, a veces horas.
    Entre turnos: un par de minutos y, de vez en cuando, una pausa larga (salta un recordatorio)."""
    if first:
        return timedelta(minutes=min(rng.lognormvariate(2.2, 1.3), 30 * 60))
    if rng.random() < 0.08:
        return timedelta(hours=rng.uniform(3, 26))
    return timedelta(minutes=rng.uniform(0.5, 4))


def simulate(conn, rng: random.Random, start: datetime, end: datetime, per_day: int, backlog: timedelta) -> None:
    events: list = []  # (momento, desempate, tipo, datos)
    tie = count()
    for when, country in arrival_times(rng, start, end, per_day):
        heapq.heappush(events, (when, next(tie), "apply", make_persona(rng, country)))
    hour = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while hour <= end:
        heapq.heappush(events, (hour, next(tie), "reminders", None))
        hour += timedelta(hours=1)

    candidates = CandidateRepository(conn)
    personas: dict[str, Persona] = {}
    while events:
        now, _, kind, data = heapq.heappop(events)
        clock = lambda now=now: now  # noqa: E731 - reloj simulado para los servicios
        if kind == "apply":
            rng_name = f"{rng.choice(FIRST_NAMES[data.country])} {rng.choice(LAST_NAMES[data.country])}"
            phone = f"+34 6{rng.randint(10, 99)} {rng.randint(100, 999)} {rng.randint(100, 999)}" if data.country == Country.ES \
                else f"+52 55 {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
            candidate = CandidateService(conn, now=clock).create(rng_name, phone, data.country)
            personas[candidate.id] = data
            heapq.heappush(events, (now + timedelta(minutes=rng.randint(2, 10)), next(tie), "contact", candidate.id))
        elif kind == "contact":
            OutreachService(conn, now=clock).run_first_contact()
            push_turn(events, tie, now + reply_delay(rng, first=True), data, end)
        elif kind == "turn":
            candidate, persona = candidates.get(data), personas[data]
            answer = next_answer(persona, candidate, now.date())
            if answer is None:
                continue
            text, args = answer
            persona.replies[candidate.stage] += 1
            agent = ScriptedAgent(SchedulingService(conn, now=clock), args)
            ChatService(conn, agent, now=clock).handle_message(data, text)
            if candidates.get(data).outcome == Outcome.MANUAL_REVIEW:
                # Un recruiter lo revisa horas después, salvo los de las últimas `backlog`
                # horas: el equipo va con retraso y siguen pendientes.
                review_at = now + timedelta(hours=rng.uniform(4, 40))
                if now <= end - backlog and review_at <= end:
                    heapq.heappush(events, (review_at, next(tie), "review", data))
            else:
                push_turn(events, tie, now + reply_delay(rng, first=False), data, end)
        elif kind == "review":
            approve = rng.random() < 0.6
            ReviewService(conn, SchedulingService(conn, now=clock), now=clock).resolve(data, approve)
            if approve:  # le ofrecen huecos: ahora elige uno y reserva
                personas[data].kind = "qualified"
                push_turn(events, tie, now + reply_delay(rng, first=True), data, end)
        elif kind == "reminders":
            ReengagementService(conn, now=clock).run_reminders()


def push_turn(events, tie, when: datetime, candidate_id: str, end: datetime) -> None:
    if when <= end:  # lo que caería en el futuro no ha pasado todavía: queda en curso
        heapq.heappush(events, (when, next(tie), "turn", candidate_id))


def generate_summaries(conn, n: int) -> None:
    """Resúmenes reales (LLM) para una muestra de conversaciones terminadas: primero
    revisión manual, luego aptos y descartados."""
    if n <= 0:
        return
    finished = [c for c in CandidateRepository(conn).list_all() if c.outcome in
                (Outcome.MANUAL_REVIEW, Outcome.QUALIFIED, Outcome.DISQUALIFIED)]
    order = {Outcome.MANUAL_REVIEW: 0, Outcome.QUALIFIED: 1, Outcome.DISQUALIFIED: 2}
    by_outcome = {o: [c for c in finished if c.outcome == o] for o in order}
    quota = {Outcome.MANUAL_REVIEW: (n + 2) // 3, Outcome.QUALIFIED: (n + 2) // 3}
    sample = by_outcome[Outcome.MANUAL_REVIEW][: quota[Outcome.MANUAL_REVIEW]] + by_outcome[Outcome.QUALIFIED][: quota[Outcome.QUALIFIED]]
    sample += by_outcome[Outcome.DISQUALIFIED][: n - len(sample)]
    service = SummaryService(conn, BedrockClient(BEDROCK_AGENT_MODEL_ID))
    for c in sample:
        try:
            service.generate(c.id)
            print(f"  resumen: {c.full_name} ({c.outcome})")
        except Exception as e:  # sin Bedrock no se para la demo: se generan luego con el botón
            print(f"  resumen fallido para {c.full_name}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/demo.db", help="BD a generar (se borra si existe)")
    parser.add_argument("--days", type=int, default=60, help="días de historia (el inicio compara periodos de hasta 30 días con los 30 anteriores)")
    parser.add_argument("--per-day", type=int, default=16, help="candidaturas por día laborable (fines de semana, la mitad)")
    parser.add_argument("--summaries", type=int, default=8, help="resúmenes con el LLM (0 = ninguno)")
    parser.add_argument("--backlog-hours", type=int, default=120, help="revisiones manuales de estas últimas horas, sin resolver")
    args = parser.parse_args()

    path = ROOT_DIR / args.db
    if path.resolve() == (ROOT_DIR / "data" / "screening.db").resolve():
        raise SystemExit("No se genera sobre screening.db: usa una BD aparte (p. ej. data/demo.db).")
    Path(path).unlink(missing_ok=True)
    conn = get_connection(path)
    init_db(conn)

    end = datetime.now(timezone.utc)
    simulate(conn, random.Random(42), end - timedelta(days=args.days), end, args.per_day, timedelta(hours=args.backlog_hours))
    generate_summaries(conn, args.summaries)

    candidates = CandidateRepository(conn).list_all()
    outcomes = Counter(str(c.outcome or "en curso") for c in candidates)
    print(f"\n{len(candidates)} candidatos en {path}")
    for outcome, n in outcomes.most_common():
        print(f"  {outcome}: {n}")
    conn.close()


if __name__ == "__main__":
    main()
