"""Un mensaje del historial de la conversación."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Message(BaseModel):
    candidate_id: str
    role: Literal["candidate", "agent"]
    text: str
    created_at: datetime
