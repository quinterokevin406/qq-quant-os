"""Asistente conversacional sobre el estado real del sistema.

Funciona en dos niveles:

**Gratuito** (`intents` + `responses`). Reconoce unas treinta clases de
pregunta por palabras clave y responde rellenando plantillas con datos reales.
Coste cero, respuesta instantánea, y estructuralmente incapaz de inventar
cifras porque no genera texto libre.

**De pago** (`client`). Si la pregunta no encaja en ninguna clase conocida y
hay clave de API configurada, se consulta al modelo pasándole el estado del
sistema ya cerrado. Sin clave, se muestra la lista de lo que sí sabe hacer.

El orden importa: se intenta siempre primero el gratuito.
"""

from qq_core.assistant.client import Assistant, AssistantError, SystemContext
from qq_core.assistant.intents import Intent, Match, reconocer
from qq_core.assistant.responses import AYUDA, GLOSARIO, Snapshot, responder

__all__ = [
    "AYUDA",
    "Assistant",
    "AssistantError",
    "GLOSARIO",
    "Intent",
    "Match",
    "Snapshot",
    "SystemContext",
    "reconocer",
    "responder",
]
