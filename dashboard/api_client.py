"""Todas las llamadas del dashboard a la API. Las páginas no saben de URLs ni de HTTP.

La URL de la API se lee de API_URL (dashboard/.env o variable de entorno).
"""

import os
from datetime import date, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
# 127.0.0.1 y no localhost: en Windows, "localhost" prueba antes IPv6 (::1), uvicorn
# solo escucha en IPv4 y cada petición pierde segundos hasta reintentar.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

# El chat espera al agente (2-3 llamadas al LLM): más margen que el resto.
TIMEOUT = 15
CHAT_TIMEOUT = 60

# Un solo cliente para todo el dashboard: reutiliza la conexión. httpx.request() crea
# uno nuevo en cada llamada (conexión y configuración SSL nuevas): ~0,4 s por petición.
# El módulo se importa una vez por proceso, así que el cliente sobrevive a los reruns.
_client = httpx.Client(base_url=API_URL)


class APIError(Exception):
    """La API no responde o devuelve un error. Las páginas lo muestran sin romper."""


def _request(method: str, path: str, timeout: float = TIMEOUT, **kwargs):
    try:
        response = _client.request(method, path, timeout=timeout, **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise APIError(f"{e.response.status_code}: {e.response.text}") from e
    except httpx.HTTPError as e:
        raise APIError(f"No se puede conectar con la API en {API_URL} ({e.__class__.__name__})") from e
    return response.json() if response.content else None


# --- Candidatos ----------------------------------------------------------------------

def list_candidates() -> list[dict]:
    return _request("GET", "/candidates")


def create_candidate(full_name: str, phone: str, country: str) -> dict:
    return _request("POST", "/candidates", json={"full_name": full_name, "phone": phone, "country": country})


def get_messages(candidate_id: str) -> list[dict]:
    return _request("GET", f"/candidates/{candidate_id}/messages")


def review(candidate_id: str, decision: str) -> str:
    """decision: "approve" | "reject". Devuelve el mensaje que le ha llegado al candidato."""
    return _request("POST", f"/candidates/{candidate_id}/review", json={"decision": decision})["message_sent"]


def get_summary(candidate_id: str) -> dict | None:
    """El resumen post-conversación, o None si todavía no se ha generado."""
    return _request("GET", f"/candidates/{candidate_id}/summary")


def generate_summary(candidate_id: str) -> dict:
    """Genera (o regenera) el resumen y espera al resultado (usa el LLM)."""
    return _request("POST", f"/candidates/{candidate_id}/summary", timeout=CHAT_TIMEOUT)


def send_message(candidate_id: str, text: str) -> str:
    return _request("POST", "/chat", timeout=CHAT_TIMEOUT, json={"candidate_id": candidate_id, "text": text})["reply"]


# --- Jobs (en producción los lanza un scheduler) ---------------------------------------

def run_first_contact() -> list[str]:
    return _request("POST", "/jobs/first-contact")["contacted"]


def run_reminders(now: datetime) -> list[dict]:
    return _request("POST", "/jobs/reminders", json={"now": now.isoformat()})["sent"]


# --- Recruiters ----------------------------------------------------------------------

def list_recruiters(country: str | None = None) -> list[dict]:
    return _request("GET", "/recruiters", params={"country": country} if country else None)


def recruiter_calendar(recruiter_id: str, start: date, end: date) -> list[dict]:
    params = {"from": start.isoformat(), "to": end.isoformat()}
    return _request("GET", f"/recruiters/{recruiter_id}/interviews", params=params)


# --- Base de conocimiento (FAQ) -------------------------------------------------------

def upload_faq(filename: str, content: bytes) -> dict:
    """Sube el FAQ y regenera la base (un embedding por pregunta: unos segundos)."""
    files = {"file": (filename, content, "text/markdown")}
    return _request("POST", "/knowledge", timeout=CHAT_TIMEOUT, files=files)


def list_faq() -> list[dict]:
    return _request("GET", "/knowledge")
