"""Estados del pipeline que comparten el controlador y sus mixins."""
from __future__ import annotations

#: Estados desde los que un pipeline puede continuar donde lo dejo. ``failed``
#: tambien es retomable cuando la etapa relanzada fallo antes de avanzar el
#: pipeline; ``_is_resumable`` exige que conserve posicion y log validos.
RESUMABLE_STATUSES = frozenset({"paused", "interrupted", "failed"})
# Estados en los que el pipeline sigue avanzando aunque no haya proceso vivo: se
# esta descartando etapas sin pendientes entre una y la siguiente.
ACTIVE_STATUSES = frozenset({"running", "stopping", "pausing"})
# Lo que detener o pausar esperan por el bloqueo antes de limitarse a dejar la
# peticion puesta. El bucle del pipeline la atiende entre etapa y etapa.
CONTROL_LOCK_TIMEOUT = 3
