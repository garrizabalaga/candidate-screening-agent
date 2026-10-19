"""Recruiters y sus plantillas semanales, leídos de data/recruiters.json.

En producción vendrían del calendario real (Google/Outlook) o del ATS; el resto
del código solo depende de estas dos funciones.
"""

import json
from functools import lru_cache

from app.core.config import DATA_DIR
from app.models.enums import Country
from app.models.interview import Recruiter


@lru_cache
def _all() -> tuple[Recruiter, ...]:
    raw = json.loads((DATA_DIR / "recruiters.json").read_text(encoding="utf-8"))
    return tuple(Recruiter.model_validate(r) for r in raw)


def list_recruiters(country: Country | None = None) -> list[Recruiter]:
    return [r for r in _all() if country is None or r.country == country]


def get_recruiter(recruiter_id: str) -> Recruiter | None:
    return next((r for r in _all() if r.id == recruiter_id), None)
