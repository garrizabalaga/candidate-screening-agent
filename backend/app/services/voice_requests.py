"""Cuál es la última petición de voz de cada candidato.

Si el candidato hace una pausa y sigue hablando, ElevenLabs descarta la petición y
envía otra con la frase completa. Cada petición espera un margen y, si mientras
tanto llega otra del mismo candidato, se retira: solo se procesa la última.
Tradeoffs en docs/architecture.md §16.
"""

import threading


class VoiceRequests:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest: dict[str, int] = {}

    def register(self, candidate_id: str) -> int:
        """Apunta una petición nueva del candidato y devuelve su número."""
        with self._lock:
            self._latest[candidate_id] = self._latest.get(candidate_id, 0) + 1
            return self._latest[candidate_id]

    def is_latest(self, candidate_id: str, number: int) -> bool:
        with self._lock:
            return self._latest.get(candidate_id) == number
