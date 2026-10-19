"""Configuración: variables de entorno, parámetros del flujo y zonas de servicio.

- Lo que cambia entre entornos (rutas, credenciales, región...) se lee del .env
  con load_dotenv(), que lo carga en las variables de entorno del proceso.
  boto3 lee de ahí AWS_BEARER_TOKEN_BEDROCK y AWS_REGION, así que la API key no
  pasa por nuestro código.
- Las reglas del flujo (MAX_REASKS) son constantes del código.
- Las zonas viven en data/service_areas.json, fuera del código y del prompt. La
  búsqueda va acotada al país del candidato (viene de la oferta): así
  "Guadalajara" es la de México para un candidato mexicano y la de España para
  uno español, sin preguntar.
"""

import json
import os
import unicodedata
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.models.enums import Country

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"

load_dotenv(ROOT_DIR / ".env")

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "screening.db"))
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
# Perfil "eu.*": las peticiones no salen de la UE (RGPD).
BEDROCK_AGENT_MODEL_ID = os.getenv("BEDROCK_AGENT_MODEL_ID", "eu.anthropic.claude-sonnet-4-6")
# Titan V2: está en la región (los datos no salen) y no depende de una suscripción de Marketplace.
BEDROCK_EMBEDDING_MODEL_ID = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
# URL pública del backend (túnel en la demo): con ella se construye el enlace de voz
# que recibe el candidato en el mensaje de apertura.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
# Agente de voz de ElevenLabs: /voice/{candidate_id} redirige a su página (talk-to).
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
# Clave que ElevenLabs envía (Authorization: Bearer) al llamar a /voice/llm. El
# endpoint queda expuesto a internet (ngrok): sin ella, cualquiera podría escribir
# en la conversación de un candidato. Vacía = sin comprobar (solo en local).
VOICE_LLM_API_KEY = os.getenv("VOICE_LLM_API_KEY", "")
# Margen que espera cada turno de voz antes de procesarse, por si ElevenLabs lo
# sustituye por una versión ampliada (el candidato hizo una pausa y siguió hablando).
VOICE_DEBOUNCE_SECONDS = float(os.getenv("VOICE_DEBOUNCE_SECONDS", "1.5"))

# Repreguntas por campo antes de aplicar la regla de "sigue sin estar claro".
MAX_REASKS = 2

# Recordatorios: tiempo desde que el candidato dejó de contestar. Los dos primeros
# son recordatorios; al tercero se cierra la conversación como abandonada.
REMINDER_SCHEDULE = [timedelta(hours=2), timedelta(hours=24), timedelta(hours=72)]

# Solo se escribe entre estas horas, en la hora local del país del candidato.
SENDING_HOURS = (9, 21)
COUNTRY_TIMEZONE = {Country.ES: ZoneInfo("Europe/Madrid"), Country.MX: ZoneInfo("America/Mexico_City")}

# Agenda de entrevistas (videollamada). Las plantillas semanales de cada recruiter
# están en data/recruiters.json; solo hay franjas de mañana y de tarde.
INTERVIEW_MINUTES = 30
SLOT_HORIZON_DAYS = 14      # se ofrecen huecos de mañana a 14 días vista
SLOTS_OFFERED = 3
AFTERNOON_FROM_HOUR = 14    # un hueco que empieza a partir de las 14:00 (hora local) es de tarde


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y sin espacios sobrantes: "  Málaga " -> "malaga"."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.casefold().split())


@lru_cache
def _service_city_index() -> dict[tuple[Country, str], str]:
    """(país, nombre normalizado de ciudad o alias) -> nombre canónico."""
    areas = json.loads((DATA_DIR / "service_areas.json").read_text(encoding="utf-8"))
    index: dict[tuple[Country, str], str] = {}
    for area in areas:
        country = Country(area["country"])
        for name in [area["city"], *area["aliases"]]:
            index[(country, normalize(name))] = area["city"]
    return index


def list_service_cities(country: Country) -> list[str]:
    """Ciudades con servicio en un país (para ofrecerlas como opciones cerradas)."""
    return sorted({city for (c, _), city in _service_city_index().items() if c == country})


def match_service_city(city: str, country: Country) -> str | None:
    """Ciudad canónica si tiene servicio en ese país, o None si no."""
    return _service_city_index().get((country, normalize(city)))
