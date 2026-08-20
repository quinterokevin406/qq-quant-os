"""Base común de las estrategias.

Toda estrategia hereda de `BaseStrategy`, que resuelve lo repetitivo —construir
la señal con sus tres precios, calcular niveles, formatear la justificación— y
deja a cada subclase únicamente su lógica propia.

Esto no es sólo comodidad. Centralizar la construcción de la señal garantiza
que las once estrategias produzcan exactamente el mismo formato, con los mismos
campos rellenos y las mismas invariantes verificadas. Si cada una lo hiciera
por su cuenta, aparecerían diferencias silenciosas: una olvidaría el objetivo,
otra pondría el stop del lado equivocado.

REGLA COMPARTIDA POR TODAS
---------------------------
`target_position(precios)` devuelve, para la barra `t`, la posición que debe
mantenerse a partir de la apertura de `t+1`. Ninguna estrategia aplica ese
desplazamiento: lo aplica el motor de backtesting, igual para todas. Así se
elimina la posibilidad de que una lo olvide y produzca resultados
artificialmente buenos.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable

import pandas as pd

from qq_core.domain.signal import (
    Direction,
    Horizon,
    Signal,
    SignalStrength,
    build_signal_id,
)
from qq_core.features import engine as fx


@runtime_checkable
class Strategy(Protocol):
    """Contrato común a todas las estrategias del sistema."""

    name: str
    label: str
    version: str
    horizon: Horizon
    description: str

    @property
    def warmup_bars(self) -> int: ...

    def target_position(self, prices: pd.DataFrame) -> pd.Series: ...

    def explain(self, prices: pd.DataFrame, symbol: str, name: str = "") -> Signal | None: ...


class BaseStrategy:
    """Implementación compartida de construcción de señales.

    Attributes:
        name: Identificador interno, estable.
        label: Nombre legible para el operador.
        version: Versión de la lógica.
        horizon: Duración esperada de las operaciones.
        description: Explicación de la estrategia en una frase.
        exit_rule: Regla de salida en lenguaje del operador.
        target_atr_multiple: Objetivo de beneficio en múltiplos de volatilidad.
        stop_atr_multiple: Nivel de invalidación en múltiplos de volatilidad.
    """

    name: str = "base"
    label: str = "Base"
    version: str = "1.0.0"
    horizon: Horizon = Horizon.WEEK_1
    description: str = ""
    exit_rule: str = ""
    target_atr_multiple: Decimal = Decimal("4.0")
    stop_atr_multiple: Decimal = Decimal("2.0")

    @property
    def warmup_bars(self) -> int:
        raise NotImplementedError

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def _describe(self, prices: pd.DataFrame, pos: float) -> tuple[SignalStrength, str]:
        """Convicción y justificación. Cada estrategia lo implementa."""
        raise NotImplementedError

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        """Series de los indicadores que esta estrategia usa para decidir.

        Se dibujan sobre el precio para que el operador vea EXACTAMENTE en qué
        se basó la señal, en lugar de una aproximación plausible.

        La implementación por defecto no devuelve nada. Cada estrategia expone
        sus propios indicadores; si alguna no lo hace, el gráfico muestra sólo
        el precio y lo declara, en lugar de dibujar indicadores que la
        estrategia no usa.

        Args:
            prices: Precios con columnas open/high/low/close.

        Returns:
            Diccionario nombre visible -> serie alineada con `prices`. Las
            series que van en un panel aparte —osciladores como el z-score— se
            nombran con el prefijo "panel:".
        """
        return {}

    def explain(
        self, prices: pd.DataFrame, symbol: str, name: str = ""
    ) -> Signal | None:
        """Construye la señal del último dato disponible.

        Args:
            prices: Precios con columnas open/high/low/close.
            symbol: Símbolo canónico.
            name: Nombre legible del instrumento.

        Returns:
            La señal, o `None` si no hay datos suficientes.

        Note:
            `observed_price` es el cierre de la última sesión: el dato sobre el
            que se calculó. `entry_price` es el precio al que debe entrarse.
            Se separan porque son conceptualmente distintos y confundirlos
            lleva al operador a creer que puede entrar a un precio ya pasado.
        """
        if len(prices) < self.warmup_bars:
            return None

        close = prices["close"]
        # Algunas estrategias necesitan saber sobre qué instrumento operan
        # (el momento transversal compara unos con otros). Se detecta por
        # firma en lugar de exigir el parámetro a todas.
        try:
            posiciones = self.target_position(prices, symbol)  # type: ignore[call-arg]
        except TypeError:
            posiciones = self.target_position(prices)
        pos = float(posiciones.iloc[-1])

        fecha = close.index[-1]
        precio_señal = Decimal(str(round(float(close.iloc[-1]), 6)))

        # El precio de entrada es el último precio conocido. En la práctica el
        # operador entrará a la apertura siguiente, que aún no existe; se usa
        # el cierre como mejor estimación disponible y se declara como tal.
        precio_entrada = precio_señal

        rango = fx.atr(prices["high"], prices["low"], close, 14)
        valor_atr = (
            float(rango.iloc[-1]) if not pd.isna(rango.iloc[-1]) else None
        )

        conviccion, motivo = self._describe(prices, pos)

        if pos == 0.0:
            direccion = Direction.FLAT
            objetivo = stop = None
        else:
            direccion = Direction.LONG if pos > 0 else Direction.SHORT
            objetivo, stop = self._levels(precio_entrada, direccion, valor_atr)

        return Signal(
            signal_id=build_signal_id(self.name, symbol, fecha.to_pydatetime().replace(tzinfo=timezone.utc)),
            strategy=self.name,
            strategy_label=self.label,
            strategy_version=self.version,
            symbol=symbol,
            instrument_name=name or symbol,
            as_of=fecha.to_pydatetime().replace(tzinfo=timezone.utc),
            generated_at=datetime.now(timezone.utc),
            direction=direccion,
            strength=conviccion,
            horizon=self.horizon,
            observed_price=precio_señal,
            entry_price=precio_entrada,
            target_price=objetivo,
            stop_price=stop,
            rationale=motivo,
            exit_rule=self.exit_rule,
            features=self._features(prices),
        )

    def _levels(
        self, entrada: Decimal, direccion: Direction, atr: float | None
    ) -> tuple[Decimal | None, Decimal | None]:
        """Objetivo e invalidación a partir de la volatilidad reciente.

        Se usan múltiplos de ATR y no porcentajes fijos porque la distancia
        adecuada depende del comportamiento del instrumento: un 2% es enorme
        en un par de divisas y minúsculo en gas natural.
        """
        if not atr or atr <= 0:
            return None, None
        objetivo_d = Decimal(str(round(atr, 6))) * self.target_atr_multiple
        stop_d = Decimal(str(round(atr, 6))) * self.stop_atr_multiple
        if direccion is Direction.LONG:
            return entrada + objetivo_d, entrada - stop_d
        return entrada - objetivo_d, entrada + stop_d

    def _features(self, prices: pd.DataFrame) -> dict[str, float]:
        """Indicadores registrados con la señal, para poder auditarla."""
        close = prices["close"]
        vol = fx.realized_volatility(close, 20)
        rango = fx.atr(prices["high"], prices["low"], close, 14)
        return {
            "volatilidad_anual_pct": (
                round(float(vol.iloc[-1]) * 100, 2)
                if not pd.isna(vol.iloc[-1]) else 0.0
            ),
            "atr_14": (
                round(float(rango.iloc[-1]), 4)
                if not pd.isna(rango.iloc[-1]) else 0.0
            ),
        }

    @staticmethod
    def _strength(magnitud: float, umbral_fuerte: float, umbral_medio: float) -> SignalStrength:
        """Traduce una magnitud continua a los tres niveles de convicción."""
        if magnitud >= umbral_fuerte:
            return SignalStrength.STRONG
        if magnitud >= umbral_medio:
            return SignalStrength.MODERATE
        return SignalStrength.WEAK

    # ------------------------------------------------------------------ #
    # Convicción a lo largo del histórico
    # ------------------------------------------------------------------ #

    def strength_metric(self, prices: pd.DataFrame) -> pd.Series | None:
        """Magnitud continua de convicción para cada barra del histórico.

        `_describe` calcula la convicción de la última barra a partir de una
        magnitud —la separación entre medias, el número de sigmas, el exceso
        sobre el rango—. Este método devuelve esa misma magnitud para toda la
        serie, lo que permite reconstruir cómo de fuerte era la señal en cada
        momento del pasado.

        Sin esto no se podría simular «qué habría pasado operando sólo las
        señales fuertes», porque no sabríamos cuáles lo eran.

        Returns:
            Serie de magnitudes, o `None` si la estrategia no define una
            noción continua de convicción. En ese caso todas sus señales se
            consideran de convicción moderada.
        """
        return None

    def strength_series(self, prices: pd.DataFrame) -> pd.Series:
        """Convicción categórica para cada barra del histórico.

        Args:
            prices: Precios con columnas open, high, low, close.

        Returns:
            Serie con los valores `strong`, `moderate` o `weak`.
        """
        magnitud = self.strength_metric(prices)
        if magnitud is None:
            return pd.Series(
                SignalStrength.MODERATE.value, index=prices.index, dtype=object
            )

        fuerte, medio = self.strength_thresholds
        resultado = pd.Series(
            SignalStrength.WEAK.value, index=prices.index, dtype=object
        )
        resultado[magnitud >= medio] = SignalStrength.MODERATE.value
        resultado[magnitud >= fuerte] = SignalStrength.STRONG.value
        return resultado

    @property
    def strength_thresholds(self) -> tuple[float, float]:
        """Umbrales (fuerte, moderada) aplicados a `strength_metric`."""
        return (1.0, 0.5)
