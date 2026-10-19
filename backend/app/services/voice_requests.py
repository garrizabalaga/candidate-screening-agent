"""Cuál es la última petición de voz de cada candidato.

ElevenLabs lanza el turno en cuanto cree que el candidato ha terminado. Si sigue
hablando tras una pausa, descarta esa petición y envía otra con la frase completa.
Para ElevenLabs no pasa nada (supone un LLM sin estado), pero nuestro turno guarda
y avanza la máquina de estados. Por eso cada petición espera un margen corto antes
de procesarse y, si mientras tanto ha llegado otra del mismo candidato, se retira:
solo se procesa la última.

Tradeoffs (aceptados para la demo):
- Suma el margen a todos los turnos de voz.
- Una pausa más larga que el margen se sigue procesando como dos turnos.
- En memoria: un solo proceso. La solución de producción sería confirmar cada turno
  cuando aparece en el transcript de ElevenLabs (ver docs/architecture.md).
"""

import threading


class VoiceRequests:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest: dict[str, int] = {}  # candidato -> número de su última petición

    def register(self, candidate_id: str) -> int:
        """Apunta una petición nueva del candidato y devuelve su número."""
        with self._lock:
            self._latest[candidate_id] = self._latest.get(candidate_id, 0) + 1
            return self._latest[candidate_id]

    def is_latest(self, candidate_id: str, number: int) -> bool:
        with self._lock:
            return self._latest.get(candidate_id) == number
