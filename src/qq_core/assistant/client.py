"""Asistente conversacional sobre el estado real del sistema.

PRINCIPIO INNEGOCIABLE
-----------------------
El asistente responde ÚNICAMENTE con datos que el sistema le entrega. No
inventa cifras, no estima de memoria y no opina sobre lo que no puede ver.

La razón no es purismo. Un asistente conversacional se lee como un consejo: si
dice "te recomiendo comprar Nasdaq porque el momento es favorable", suena a
recomendación fundada aunque no lo sea. Una tabla con veredictos se lee con
desconfianza; una frase amable, no. La misma información mal fundada resulta
mucho más peligrosa en formato conversación.

Por eso el contexto se construye AQUÍ, en código, a partir del estado real, y
se le entrega ya cerrado. El asistente no tiene forma de consultar nada por su
cuenta ni de rellenar huecos.

QUÉ PUEDE RESPONDER
-------------------
- Qué señales hay hoy y con qué convicción
- Cómo van las operaciones en seguimiento y si han cambiado desde ayer
- Cuánto capital y riesgo asignar, según el motor de reparto
- Estado de la cuenta: equity, riesgo abierto, capacidad disponible
- Qué instrumentos y estrategias existen, y sus costes

QUÉ NO PUEDE RESPONDER
----------------------
- Predicciones de precio
- Cualquier cosa que no esté en el contexto que se le pasa
- Recomendaciones que no vengan del motor de reparto

COSTE
-----
Cada pregunta consume una llamada a la API de Anthropic, que se factura. El
panel es público, de modo que sin protección cualquiera con el enlace podría
agotar el saldo. Ver `require_password` en la interfaz.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

CLAVE_ENV = "ANTHROPIC_API_KEY"
MODELO = "claude-sonnet-4-6"
URL = "https://api.anthropic.com/v1/messages"

INSTRUCCIONES = """Eres el asistente de QQ QUANT OS, una plataforma de investigación cuantitativa.

REGLA PRINCIPAL, POR ENCIMA DE CUALQUIER OTRA:
Respondes ÚNICAMENTE con la información del bloque ESTADO DEL SISTEMA que
acompaña a cada pregunta. Si algo no está ahí, dices que no lo sabes y qué
pestaña del panel podría tenerlo. Nunca inventas cifras, precios, porcentajes
ni fechas. Nunca completas un dato ausente con una estimación tuya.

QUÉ ERES Y QUÉ NO ERES:
Eres un asistente que lee el sistema y lo explica. NO eres un asesor de
inversiones y no debes comportarte como tal. Puedes decir "el motor de reparto
asigna X a esta señal y aquí está su razonamiento". No debes decir "yo te
recomiendo comprar esto".

CONTEXTO IMPORTANTE QUE DEBES TENER PRESENTE:
El sistema evaluó 198 combinaciones de estrategia e instrumento. Buscar entre
tantas garantiza encontrar algunas buenas por azar. Cuando alguien pregunte por
las mejores señales, puedes dárselas, pero recuerda brevemente que su ventaja
histórica procede de una búsqueda amplia y no está probada como real.

El sistema NO ejecuta operaciones. Todas las decisiones y ejecuciones son
manuales en MetaTrader 5. Nunca sugieras que el sistema puede operar solo.

CÓMO RESPONDES:
En español, directo y sin rodeos. Usa las cifras exactas del contexto. Si una
cifra tiene una limitación conocida —muestra pequeña, coste estimado en lugar
de medido— la mencionas. Prefiere respuestas cortas; si hace falta una tabla,
úsala. No repitas la pregunta antes de contestar.

Si te preguntan algo fuera del sistema (política, tiempo, cualquier otra cosa),
dices amablemente que sólo puedes hablar del sistema."""


@dataclass
class SystemContext:
    """Estado del sistema que se entrega al asistente.

    Cada campo es una sección de texto ya formateada. Se construye en el panel
    a partir de los datos reales y se pasa cerrado: el asistente no puede
    consultar nada por su cuenta.
    """

    fecha: str = ""
    cuenta: str = ""
    senales_hoy: str = ""
    seguimiento: str = ""
    reparto: str = ""
    instrumentos: str = ""
    estrategias: str = ""
    avisos: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Compone el bloque de contexto."""
        partes = ["=== ESTADO DEL SISTEMA ==="]
        if self.fecha:
            partes.append(f"\n[FECHA]\n{self.fecha}")
        if self.cuenta:
            partes.append(f"\n[CUENTA]\n{self.cuenta}")
        if self.senales_hoy:
            partes.append(f"\n[SEÑALES DE HOY]\n{self.senales_hoy}")
        if self.seguimiento:
            partes.append(f"\n[EN SEGUIMIENTO]\n{self.seguimiento}")
        if self.reparto:
            partes.append(f"\n[REPARTO DE CAPITAL]\n{self.reparto}")
        if self.instrumentos:
            partes.append(f"\n[INSTRUMENTOS]\n{self.instrumentos}")
        if self.estrategias:
            partes.append(f"\n[ESTRATEGIAS]\n{self.estrategias}")
        if self.avisos:
            partes.append("\n[LIMITACIONES QUE DEBES MENCIONAR SI SON RELEVANTES]")
            partes.extend(f"- {a}" for a in self.avisos)
        partes.append("\n=== FIN DEL ESTADO ===")
        return "\n".join(partes)


class AssistantError(RuntimeError):
    """Fallo al consultar al asistente."""


class Assistant:
    """Cliente del asistente.

    Attributes:
        api_key: Clave de la API. Si es `None`, se busca en el entorno.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = MODELO,
        timeout: float = 60.0,
        fetcher=None,
    ) -> None:
        """Inicializa el asistente.

        Args:
            api_key: Clave. `None` significa buscarla en el entorno; una cadena
                vacía significa "sin clave" de forma explícita y NO cae al
                entorno, para que las pruebas sean deterministas.
            model: Modelo a usar.
            timeout: Espera máxima en segundos.
            fetcher: Función de petición inyectable, para pruebas.
        """
        if api_key is None:
            self._api_key = os.environ.get(CLAVE_ENV, "")
        else:
            self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._fetcher = fetcher or self._http_post

    @property
    def is_configured(self) -> bool:
        """Cierto si hay clave disponible."""
        return bool(self._api_key)

    def _http_post(self, payload: dict) -> str:
        peticion = urllib.request.Request(
            URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self._timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            cuerpo = exc.read().decode("utf-8", errors="replace")[:300]
            raise AssistantError(f"error {exc.code} de la API: {cuerpo}") from exc
        except urllib.error.URLError as exc:
            raise AssistantError(f"no se pudo contactar con la API: {exc}") from exc

    def ask(
        self,
        question: str,
        context: SystemContext,
        history: list[dict[str, str]] | None = None,
        max_tokens: int = 1200,
    ) -> str:
        """Responde una pregunta sobre el estado del sistema.

        Args:
            question: Pregunta del operador.
            context: Estado real del sistema.
            history: Turnos anteriores, para dar continuidad a la conversación.
            max_tokens: Longitud máxima de la respuesta.

        Returns:
            Respuesta en texto.

        Raises:
            AssistantError: Si no hay clave o la API falla.
        """
        if not self.is_configured:
            raise AssistantError(
                "Falta la clave de la API. Configúrala en la variable de "
                "entorno ANTHROPIC_API_KEY, o en los secretos de Streamlit."
            )

        mensajes: list[dict[str, Any]] = []
        for turno in (history or [])[-8:]:
            mensajes.append({"role": turno["role"], "content": turno["content"]})

        mensajes.append({
            "role": "user",
            "content": f"{context.render()}\n\nPREGUNTA: {question}",
        })

        crudo = self._fetcher({
            "model": self._model,
            "max_tokens": max_tokens,
            "system": INSTRUCCIONES,
            "messages": mensajes,
        })

        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError as exc:
            raise AssistantError(f"respuesta no es JSON válido: {exc}") from exc

        if "content" not in datos:
            raise AssistantError(f"respuesta inesperada: {str(datos)[:200]}")

        partes = [
            b.get("text", "") for b in datos["content"] if b.get("type") == "text"
        ]
        texto = "\n".join(p for p in partes if p).strip()

        if not texto:
            raise AssistantError("la respuesta llegó vacía")
        return texto
