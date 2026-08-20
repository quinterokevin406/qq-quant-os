"""Biblioteca de estrategias (Módulo 07).

Siete motores independientes, cada uno con su horizonte declarado y su plan de
operación completo. Todos producen exactamente el mismo formato de señal.

QUÉ FALTA Y POR QUÉ
-------------------
Del plan original de quince estrategias:

  - **Carry** y **Term Structure**: bloqueadas. Requieren la curva de futuros,
    que el bróker no proporciona (ADR-0003). No es cuestión de tiempo de
    desarrollo: falta el dato de entrada.
  - **Session Effects**: requiere datos intradía. Hoy sólo hay cierres diarios.
  - **Pairs Trading** y **Statistical Arbitrage**: requieren análisis de
    cointegración entre pares, que es un módulo de investigación en sí mismo.
  - **Macro Allocation**: parcial, sin componente de renta fija.
  - **ML Meta Model**: por definición va el último, ya que combina las señales
    de las demás.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from qq_core.domain.signal import Horizon, SignalStrength
from qq_core.features import engine as fx
from qq_core.strategies.base import BaseStrategy


class TrendFollowing(BaseStrategy):
    """Seguimiento de tendencia por cruce de medias con filtro de volatilidad.

    Compra cuando la media rápida supera a la lenta, vende en el caso
    contrario, y se mantiene fuera cuando la volatilidad es extrema, porque en
    esos regímenes las señales cambian de forma errática.

    Es la familia con mayor evidencia académica de persistencia (Moskowitz, Ooi
    y Pedersen, 2012) y la que mejor encaja con un universo de índices y
    materias primas.
    """

    name = "trend_following"
    label = "Seguimiento de tendencia"
    version = "2.0.0"
    horizon = Horizon.MONTH_3
    description = (
        "Sigue la dirección dominante del mercado. Opera pocas veces y mantiene "
        "las posiciones durante meses."
    )
    exit_rule = (
        "Se cierra cuando las medias vuelven a cruzarse en sentido contrario, "
        "o si el precio alcanza la invalidación."
    )
    target_atr_multiple = Decimal("6.0")
    stop_atr_multiple = Decimal("3.0")

    def __init__(self, fast: int = 50, slow: int = 200, vol_window: int = 20,
                 max_volatility: float = 0.60) -> None:
        if fast >= slow:
            raise ValueError("la media rápida debe ser menor que la lenta")
        self.fast, self.slow = fast, slow
        self.vol_window, self.max_volatility = vol_window, max_volatility

    @property
    def warmup_bars(self) -> int:
        return max(self.slow, self.vol_window) + 1

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["close"]
        rapida, lenta = fx.sma(close, self.fast), fx.sma(close, self.slow)
        vol = fx.realized_volatility(close, self.vol_window)
        pos = pd.Series(0.0, index=close.index)
        pos[rapida > lenta] = 1.0
        pos[rapida < lenta] = -1.0
        pos[vol > self.max_volatility] = 0.0
        pos[rapida.isna() | lenta.isna() | vol.isna()] = 0.0
        return pos

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        """Las dos medias móviles cuyo cruce genera la señal."""
        close = prices["close"]
        return {
            f"Media rápida ({self.fast})": fx.sma(close, self.fast),
            f"Media lenta ({self.slow})": fx.sma(close, self.slow),
        }

    def strength_metric(self, prices: pd.DataFrame) -> pd.Series:
        """Separación relativa entre las dos medias.

        Cuanto más separadas están, más clara es la tendencia y mayor la
        convicción de la señal.
        """
        close = prices["close"]
        rapida = fx.sma(close, self.fast)
        lenta = fx.sma(close, self.slow)
        return (rapida - lenta).abs() / lenta.replace(0, pd.NA)

    @property
    def strength_thresholds(self) -> tuple[float, float]:
        return (0.05, 0.015)

    def _describe(self, prices, pos):
        close = prices["close"]
        rapida = float(fx.sma(close, self.fast).iloc[-1])
        lenta = float(fx.sma(close, self.slow).iloc[-1])
        vol = float(fx.realized_volatility(close, self.vol_window).iloc[-1])
        sep = abs(rapida - lenta) / lenta if lenta else 0.0

        if pos == 0.0:
            if vol > self.max_volatility:
                return SignalStrength.WEAK, (
                    f"Sin posición: volatilidad anualizada del {vol:.1%}, por "
                    f"encima del límite del {self.max_volatility:.0%}. En este "
                    f"régimen la estrategia genera señales erráticas."
                )
            return SignalStrength.WEAK, "Sin tendencia definida."

        sentido = "alcista" if pos > 0 else "bajista"
        relacion = "supera a" if pos > 0 else "está por debajo de"
        return self._strength(sep, 0.05, 0.015), (
            f"Tendencia {sentido}: la media de {self.fast} sesiones "
            f"({rapida:,.2f}) {relacion} la de {self.slow} ({lenta:,.2f}) en un "
            f"{sep:.1%}. Volatilidad anualizada {vol:.1%}."
        )


class _MeanReversionOverlaysMixin:
    """Indicadores dibujables de la reversión a la media."""

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        close = prices["close"]
        media = fx.sma(close, self.window)
        desv = close.rolling(self.window, min_periods=self.window).std()
        return {
            f"Media ({self.window})": media,
            f"Banda +{self.entry_z}σ": media + desv * float(self.entry_z),
            f"Banda -{self.entry_z}σ": media - desv * float(self.entry_z),
            "panel:Z-score": fx.zscore(close, self.window),
        }


class MeanReversion(_MeanReversionOverlaysMixin, BaseStrategy):
    """Reversión a la media sobre desviaciones extremas, sin tendencia.

    Cuando el precio se aleja más de dos desviaciones típicas de su media, se
    apuesta por el retorno. Sólo se aplica si NO hay tendencia dominante: hacer
    reversión dentro de una tendencia fuerte es apostar contra el movimiento, y
    es la forma clásica de perder dinero de manera consistente.
    """

    name = "mean_reversion"
    label = "Reversión a la media"
    version = "2.0.0"
    horizon = Horizon.WEEK_1
    description = (
        "Aprovecha exageraciones de corto plazo. Entra cuando el precio se "
        "aleja mucho de su media y espera el retorno."
    )
    exit_rule = (
        "Se cierra cuando el precio vuelve cerca de su media, alcanza el "
        "objetivo, o toca la invalidación."
    )
    target_atr_multiple = Decimal("2.5")
    stop_atr_multiple = Decimal("2.0")

    def __init__(self, window: int = 20, entry_z: float = 2.0,
                 exit_z: float = 0.5, trend_window: int = 200) -> None:
        self.window, self.entry_z = window, entry_z
        self.exit_z, self.trend_window = exit_z, trend_window

    @property
    def warmup_bars(self) -> int:
        return max(self.window, self.trend_window) + 1

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["close"]
        z = fx.zscore(close, self.window)
        pendiente = fx.sma(close, self.trend_window).pct_change(20)
        sin_tendencia = pendiente.abs() < 0.02

        pos = pd.Series(0.0, index=close.index)
        estado = 0.0
        # Bucle explícito: la estrategia mantiene estado hasta cruzar el umbral
        # de salida. Vectorizarlo sería más rápido y mucho menos auditable.
        for i, (zi, ok) in enumerate(zip(z.tolist(), sin_tendencia.tolist())):
            if pd.isna(zi) or not ok:
                estado = 0.0
            elif estado == 0.0:
                if zi <= -self.entry_z:
                    estado = 1.0
                elif zi >= self.entry_z:
                    estado = -1.0
            elif estado > 0 and zi >= -self.exit_z:
                estado = 0.0
            elif estado < 0 and zi <= self.exit_z:
                estado = 0.0
            pos.iloc[i] = estado
        return pos

    def strength_metric(self, prices: pd.DataFrame) -> pd.Series:
        """Desviación respecto a la media, en valor absoluto de sigmas."""
        return fx.zscore(prices["close"], self.window).abs()

    @property
    def strength_thresholds(self) -> tuple[float, float]:
        return (3.0, 2.5)

    def _describe(self, prices, pos):
        close = prices["close"]
        z = fx.zscore(close, self.window).iloc[-1]
        z = float(z) if not pd.isna(z) else 0.0
        media = float(fx.sma(close, self.window).iloc[-1])

        if pos == 0.0:
            return SignalStrength.WEAK, (
                f"Sin posición: desviación de {z:+.2f} sigmas, insuficiente "
                f"para entrar (umbral {self.entry_z}), o hay tendencia "
                f"dominante que desaconseja operar en contra."
            )
        lado = "por debajo de" if pos > 0 else "por encima de"
        espera = "retorno al alza" if pos > 0 else "corrección a la baja"
        return self._strength(abs(z), 3.0, 2.5), (
            f"Precio {abs(z):.2f} desviaciones típicas {lado} su media de "
            f"{self.window} sesiones ({media:,.2f}). Sin tendencia dominante, "
            f"se espera {espera}."
        )


class _BreakoutOverlaysMixin:
    """Indicadores dibujables de la ruptura de volatilidad."""

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        alto, bajo = prices["high"], prices["low"]
        return {
            f"Techo del rango ({self.lookback})":
                alto.rolling(self.lookback, min_periods=self.lookback).max().shift(1),
            f"Suelo del rango ({self.lookback})":
                bajo.rolling(self.lookback, min_periods=self.lookback).min().shift(1),
        }


class VolatilityBreakout(_BreakoutOverlaysMixin, BaseStrategy):
    """Ruptura de rango con confirmación de volumen de movimiento.

    Entra cuando el precio supera el máximo (o perfora el mínimo) de las
    últimas N sesiones. La lógica es que una ruptura de rango tras un periodo
    de calma suele iniciar un movimiento sostenido.

    Es la estrategia con horizonte más corto de las disponibles: las rupturas
    se resuelven en días, no en meses.
    """

    name = "volatility_breakout"
    label = "Ruptura de volatilidad"
    version = "1.0.0"
    horizon = Horizon.WEEK_1
    description = (
        "Detecta rupturas de rango tras periodos de calma. Operaciones cortas "
        "y con objetivo definido."
    )
    exit_rule = (
        "Se cierra al alcanzar el objetivo, al tocar la invalidación, o si el "
        "precio vuelve dentro del rango previo."
    )
    target_atr_multiple = Decimal("3.0")
    stop_atr_multiple = Decimal("1.5")

    def __init__(self, lookback: int = 20, squeeze_window: int = 50) -> None:
        self.lookback, self.squeeze_window = lookback, squeeze_window

    @property
    def warmup_bars(self) -> int:
        return max(self.lookback, self.squeeze_window) + 15

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        close, alto, bajo = prices["close"], prices["high"], prices["low"]
        # `shift(1)` es esencial: el máximo del rango debe excluir la barra
        # actual, o la comparación sería trivialmente cierta.
        techo = alto.rolling(self.lookback, min_periods=self.lookback).max().shift(1)
        suelo = bajo.rolling(self.lookback, min_periods=self.lookback).min().shift(1)

        # Compresión previa: sólo interesan rupturas tras calma relativa.
        atr = fx.atr(alto, bajo, close, 14)
        atr_medio = atr.rolling(self.squeeze_window, min_periods=self.squeeze_window).mean()
        comprimido = (atr < atr_medio).shift(1).fillna(False)

        pos = pd.Series(0.0, index=close.index)
        pos[(close > techo) & comprimido] = 1.0
        pos[(close < suelo) & comprimido] = -1.0
        return pos.fillna(0.0)

    def strength_metric(self, prices: pd.DataFrame) -> pd.Series:
        """Cuánto supera el precio al extremo del rango previo."""
        close, alto, bajo = prices["close"], prices["high"], prices["low"]
        techo = alto.rolling(self.lookback, min_periods=self.lookback).max().shift(1)
        suelo = bajo.rolling(self.lookback, min_periods=self.lookback).min().shift(1)
        arriba = (close / techo - 1).clip(lower=0)
        abajo = (suelo / close - 1).clip(lower=0)
        return arriba.fillna(0) + abajo.fillna(0)

    @property
    def strength_thresholds(self) -> tuple[float, float]:
        return (0.02, 0.005)

    def _describe(self, prices, pos):
        close, alto, bajo = prices["close"], prices["high"], prices["low"]
        techo = float(alto.rolling(self.lookback).max().shift(1).iloc[-1])
        suelo = float(bajo.rolling(self.lookback).min().shift(1).iloc[-1])
        precio = float(close.iloc[-1])

        if pos == 0.0:
            return SignalStrength.WEAK, (
                f"Sin ruptura: el precio ({precio:,.2f}) se mantiene dentro del "
                f"rango de las últimas {self.lookback} sesiones "
                f"({suelo:,.2f} – {techo:,.2f})."
            )
        if pos > 0:
            exceso = (precio / techo - 1) if techo else 0.0
            return self._strength(exceso, 0.02, 0.005), (
                f"Ruptura alcista: el precio ({precio:,.2f}) supera el máximo "
                f"de las últimas {self.lookback} sesiones ({techo:,.2f}) tras "
                f"un periodo de baja volatilidad."
            )
        exceso = (suelo / precio - 1) if precio else 0.0
        return self._strength(exceso, 0.02, 0.005), (
            f"Ruptura bajista: el precio ({precio:,.2f}) perfora el mínimo de "
            f"las últimas {self.lookback} sesiones ({suelo:,.2f}) tras un "
            f"periodo de baja volatilidad."
        )


class Seasonality(BaseStrategy):
    """Efectos de calendario: día de la semana y mes del año.

    Opera patrones estacionales documentados: el efecto fin de mes, en que los
    índices tienden a comportarse mejor en los últimos días, y la debilidad
    histórica del periodo de verano en renta variable.

    ADVERTENCIA METODOLÓGICA: la estacionalidad es la familia más expuesta al
    sesgo de búsqueda. Probando suficientes combinaciones de día y mes siempre
    aparecen patrones. Esta implementación usa sólo dos efectos ampliamente
    documentados en la literatura, en lugar de buscar los que mejor funcionan
    en la muestra.
    """

    name = "seasonality"
    label = "Estacionalidad"
    version = "1.0.0"
    horizon = Horizon.DAY_1
    description = (
        "Aprovecha patrones de calendario documentados, como el efecto de fin "
        "de mes. Posiciones de pocos días."
    )
    exit_rule = "Se cierra al terminar la ventana estacional."
    target_atr_multiple = Decimal("2.0")
    stop_atr_multiple = Decimal("1.5")

    @property
    def warmup_bars(self) -> int:
        return 40

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        idx = prices.index
        pos = pd.Series(0.0, index=idx)
        dia_mes = pd.Series(idx.day, index=idx)
        mes = pd.Series(idx.month, index=idx)
        ultimo_dia = pd.Series(idx.days_in_month, index=idx)

        # Efecto fin de mes: últimos 3 días naturales y primeros 2.
        fin_de_mes = (dia_mes >= ultimo_dia - 2) | (dia_mes <= 2)
        # Debilidad estacional de verano en renta variable.
        verano = mes.isin([8, 9])

        pos[fin_de_mes] = 1.0
        pos[verano & ~fin_de_mes] = -1.0
        return pos

    def _describe(self, prices, pos):
        fecha = prices.index[-1]
        if pos > 0:
            return SignalStrength.MODERATE, (
                f"Ventana de fin de mes ({fecha.strftime('%d/%m')}). "
                f"Históricamente los índices muestran sesgo positivo en los "
                f"últimos días del mes y los primeros del siguiente."
            )
        if pos < 0:
            return SignalStrength.WEAK, (
                f"Periodo estacionalmente débil ({fecha.strftime('%B')}). "
                f"Agosto y septiembre presentan históricamente el peor "
                f"comportamiento del año en renta variable."
            )
        return SignalStrength.WEAK, "Fuera de las ventanas estacionales operables."


class RiskOffAllocation(BaseStrategy):
    """Detección de aversión al riesgo por deterioro de mercado.

    Se pone corta en activos de riesgo cuando se acumulan señales de tensión:
    el precio pierde su media larga, la volatilidad se dispara respecto a su
    nivel habitual, y la caída desde máximos supera un umbral.

    Esta estrategia se beneficia especialmente de la estructura de costes del
    bróker: las posiciones vendidas generan ingreso por financiación en lugar
    de coste (ADR-0003).
    """

    name = "risk_off"
    label = "Aversión al riesgo"
    version = "1.0.0"
    horizon = Horizon.MONTH_1
    description = (
        "Detecta periodos de tensión de mercado y se posiciona a la baja. "
        "Actúa como cobertura del resto de la cartera."
    )
    exit_rule = (
        "Se cierra cuando el mercado recupera su media larga y la volatilidad "
        "vuelve a niveles normales."
    )
    target_atr_multiple = Decimal("5.0")
    stop_atr_multiple = Decimal("2.5")

    def __init__(self, trend_window: int = 100, vol_window: int = 20,
                 vol_ratio: float = 1.5, drawdown_threshold: float = 0.07) -> None:
        self.trend_window, self.vol_window = trend_window, vol_window
        self.vol_ratio, self.drawdown_threshold = vol_ratio, drawdown_threshold

    @property
    def warmup_bars(self) -> int:
        return self.trend_window + 60

    def _senales(self, prices: pd.DataFrame):
        close = prices["close"]
        bajo_media = close < fx.sma(close, self.trend_window)
        vol = fx.realized_volatility(close, self.vol_window)
        vol_alta = vol > vol.rolling(100, min_periods=100).mean() * self.vol_ratio
        caida = fx.drawdown(close) < -self.drawdown_threshold
        return bajo_media, vol_alta, caida

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        bajo_media, vol_alta, caida = self._senales(prices)
        # Se exigen las tres condiciones simultáneas. Con una o dos, las
        # señales serían demasiado frecuentes y la cobertura perdería sentido.
        activa = bajo_media & vol_alta & caida
        pos = pd.Series(0.0, index=prices.index)
        pos[activa.fillna(False)] = -1.0
        return pos

    def _describe(self, prices, pos):
        close = prices["close"]
        bajo_media, vol_alta, caida = self._senales(prices)
        dd = float(fx.drawdown(close).iloc[-1]) * 100

        if pos == 0.0:
            cumplidas = sum(
                bool(x.iloc[-1]) for x in (bajo_media, vol_alta, caida)
                if not pd.isna(x.iloc[-1])
            )
            return SignalStrength.WEAK, (
                f"Sin señal de tensión: {cumplidas} de 3 condiciones activas. "
                f"Caída actual desde máximos: {dd:.1f}%."
            )
        return SignalStrength.STRONG, (
            f"Régimen de aversión al riesgo: el precio está por debajo de su "
            f"media de {self.trend_window} sesiones, la volatilidad supera en "
            f"{self.vol_ratio:.1f} veces su nivel habitual, y la caída desde "
            f"máximos alcanza el {abs(dd):.1f}%."
        )


class _VolTargetOverlaysMixin:
    """Indicadores dibujables del control de volatilidad."""

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        vol = fx.realized_volatility(prices["close"], self.vol_window) * 100
        return {
            "panel:Volatilidad anualizada (%)": vol,
            f"panel:Referencia ({self.vol_reference})":
                vol.rolling(self.vol_reference, min_periods=self.vol_reference).mean(),
        }


class VolatilityTargeting(_VolTargetOverlaysMixin, BaseStrategy):
    """Exposición inversa a la volatilidad, con sesgo direccional.

    Mantiene posición comprada mientras la tendencia es positiva, pero sale
    cuando la volatilidad supera su nivel habitual. La premisa, bien
    documentada, es que la volatilidad se agrupa: periodos tranquilos tienden a
    seguir tranquilos, y los turbulentos a seguir turbulentos.

    Se diferencia del seguimiento de tendencia en que aquí la volatilidad es el
    criterio principal, no un filtro secundario.
    """

    name = "volatility_targeting"
    label = "Control de volatilidad"
    version = "1.0.0"
    horizon = Horizon.MONTH_1
    description = (
        "Invierte cuando el mercado está tranquilo y se retira cuando se "
        "agita. Busca rentabilidad con menor variabilidad."
    )
    exit_rule = (
        "Se cierra cuando la volatilidad supera su nivel habitual o la "
        "tendencia se vuelve negativa."
    )
    target_atr_multiple = Decimal("4.0")
    stop_atr_multiple = Decimal("2.5")

    def __init__(self, vol_window: int = 20, vol_reference: int = 100,
                 trend_window: int = 100) -> None:
        self.vol_window, self.vol_reference = vol_window, vol_reference
        self.trend_window = trend_window

    @property
    def warmup_bars(self) -> int:
        return max(self.vol_reference, self.trend_window) + 20

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["close"]
        vol = fx.realized_volatility(close, self.vol_window)
        vol_ref = vol.rolling(self.vol_reference, min_periods=self.vol_reference).mean()
        tranquilo = vol < vol_ref
        alcista = close > fx.sma(close, self.trend_window)
        pos = pd.Series(0.0, index=close.index)
        pos[(tranquilo & alcista).fillna(False)] = 1.0
        return pos

    def _describe(self, prices, pos):
        close = prices["close"]
        vol = fx.realized_volatility(close, self.vol_window)
        vol_ref = vol.rolling(self.vol_reference).mean()
        v = float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else 0.0
        vr = float(vol_ref.iloc[-1]) if not pd.isna(vol_ref.iloc[-1]) else 0.0

        if pos == 0.0:
            if v >= vr > 0:
                return SignalStrength.WEAK, (
                    f"Fuera del mercado: volatilidad actual {v:.1%} por encima "
                    f"de su nivel habitual {vr:.1%}. La estrategia sólo opera "
                    f"en entornos tranquilos."
                )
            return SignalStrength.WEAK, "Fuera: la tendencia no es favorable."
        ratio = v / vr if vr else 1.0
        return self._strength(1 - ratio, 0.3, 0.1), (
            f"Entorno favorable: volatilidad actual {v:.1%}, por debajo de su "
            f"nivel habitual {vr:.1%}, con tendencia alcista sobre la media de "
            f"{self.trend_window} sesiones."
        )


class CrossSectionalMomentum(BaseStrategy):
    """Momento transversal: comprar los fuertes, vender los débiles.

    A diferencia de las demás, esta estrategia NO evalúa un instrumento de
    forma aislada: compara todos los del universo entre sí y opera los extremos
    del ranking. Compra el tercio con mejor comportamiento reciente y vende el
    tercio peor.

    IMPLEMENTACIÓN
    --------------
    Como el contrato de estrategia opera sobre un instrumento cada vez, el
    ranking se calcula fuera y se inyecta con `set_universe_ranking`. Si no se
    ha inyectado, la estrategia devuelve posición neutra en lugar de inventar
    un ranking con un solo elemento. Es deliberado: preferimos no operar a
    operar con información incompleta.
    """

    name = "cross_sectional_momentum"
    label = "Momento transversal"
    version = "1.0.0"
    horizon = Horizon.MONTH_1
    description = (
        "Compara todos los mercados entre sí y compra los más fuertes mientras "
        "vende los más débiles."
    )
    exit_rule = (
        "Se revisa mensualmente: se cierra cuando el instrumento sale de los "
        "extremos del ranking."
    )
    target_atr_multiple = Decimal("5.0")
    stop_atr_multiple = Decimal("3.0")

    def __init__(self, lookback: int = 120, top_pct: float = 0.33) -> None:
        self.lookback, self.top_pct = lookback, top_pct
        self._ranking: pd.DataFrame | None = None

    def set_universe_ranking(self, ranking: pd.DataFrame) -> None:
        """Inyecta el percentil de cada instrumento en cada fecha.

        Args:
            ranking: DataFrame con fechas en el índice, símbolos en columnas y
                percentiles entre 0 y 1 como valores.
        """
        self._ranking = ranking

    @property
    def warmup_bars(self) -> int:
        return self.lookback + 20

    def target_position(self, prices: pd.DataFrame, symbol: str | None = None) -> pd.Series:
        pos = pd.Series(0.0, index=prices.index)
        if self._ranking is None or symbol is None or symbol not in self._ranking:
            return pos
        percentil = self._ranking[symbol].reindex(prices.index)
        pos[percentil >= 1 - self.top_pct] = 1.0
        pos[percentil <= self.top_pct] = -1.0
        return pos.fillna(0.0)

    def _describe(self, prices, pos):
        rendimiento = float(prices["close"].pct_change(self.lookback).iloc[-1] * 100)
        if pos == 0.0:
            return SignalStrength.WEAK, (
                f"En la zona media del ranking. Rentabilidad de "
                f"{self.lookback} sesiones: {rendimiento:+.1f}%."
            )
        if pos > 0:
            return SignalStrength.MODERATE, (
                f"Entre los más fuertes del universo, con una rentabilidad de "
                f"{rendimiento:+.1f}% en {self.lookback} sesiones."
            )
        return SignalStrength.MODERATE, (
            f"Entre los más débiles del universo, con una rentabilidad de "
            f"{rendimiento:+.1f}% en {self.lookback} sesiones."
        )


class _MomentumOverlaysMixin:
    """Indicadores dibujables del momento absoluto."""

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        close = prices["close"]
        ventana = getattr(self, "lookback", getattr(self, "window", 120))
        return {
            f"Precio de hace {ventana} sesiones": close.shift(ventana),
            f"panel:Rentabilidad {ventana} sesiones (%)":
                close.pct_change(ventana) * 100,
        }


class TimeSeriesMomentum(_MomentumOverlaysMixin, BaseStrategy):
    """Momento absoluto: comparar cada activo consigo mismo.

    Compra si el instrumento ha subido en los últimos doce meses, vende si ha
    bajado. Se diferencia del momento transversal en que no compara unos
    activos con otros: cada uno se juzga contra su propio pasado.

    Es el resultado central de Moskowitz, Ooi y Pedersen (2012), documentado
    sobre 58 instrumentos y más de un siglo de datos. Se incluye porque su
    comportamiento es distinto al del cruce de medias pese a parecer similar:
    reacciona antes a los cambios de régimen.
    """

    name = "time_series_momentum"
    label = "Momento absoluto"
    version = "1.0.0"
    horizon = Horizon.MONTH_3
    description = (
        "Compra lo que ha subido durante el último año y vende lo que ha "
        "bajado. Revisión mensual."
    )
    exit_rule = (
        "Se revisa cada mes: se cierra cuando la rentabilidad del último año "
        "cambia de signo o se alcanza la invalidación."
    )
    target_atr_multiple = Decimal("6.0")
    stop_atr_multiple = Decimal("3.0")

    def __init__(self, lookback: int = 252, confirm: int = 20) -> None:
        self.lookback, self.confirm = lookback, confirm

    @property
    def warmup_bars(self) -> int:
        return self.lookback + self.confirm + 5

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["close"]
        anual = close.pct_change(self.lookback)
        # Confirmación de corto plazo: evita entrar justo cuando el momento
        # anual sigue siendo positivo pero el precio ya se ha girado.
        corto = close.pct_change(self.confirm)
        pos = pd.Series(0.0, index=close.index)
        pos[(anual > 0) & (corto > 0)] = 1.0
        pos[(anual < 0) & (corto < 0)] = -1.0
        return pos.fillna(0.0)

    def strength_metric(self, prices: pd.DataFrame) -> pd.Series:
        """Magnitud de la rentabilidad de la ventana larga."""
        return prices["close"].pct_change(self.lookback).abs()

    @property
    def strength_thresholds(self) -> tuple[float, float]:
        return (0.25, 0.10)

    def _describe(self, prices, pos):
        close = prices["close"]
        anual = float(close.pct_change(self.lookback).iloc[-1] * 100)
        corto = float(close.pct_change(self.confirm).iloc[-1] * 100)
        if pos == 0.0:
            return SignalStrength.WEAK, (
                f"Sin señal: rentabilidad de {self.lookback} sesiones "
                f"{anual:+.1f}% y de {self.confirm} sesiones {corto:+.1f}%; "
                f"las dos ventanas no coinciden en dirección."
            )
        sentido = "positivo" if pos > 0 else "negativo"
        return self._strength(abs(anual) / 100, 0.25, 0.10), (
            f"Momento {sentido} confirmado: el instrumento acumula "
            f"{anual:+.1f}% en {self.lookback} sesiones y {corto:+.1f}% en las "
            f"últimas {self.confirm}. Ambas ventanas apuntan en el mismo sentido."
        )


class _PullbackOverlaysMixin:
    """Indicadores dibujables del retroceso en tendencia."""

    def chart_overlays(self, prices: pd.DataFrame) -> dict[str, pd.Series]:
        close = prices["close"]
        return {
            f"Media de tendencia ({self.trend_window})":
                fx.sma(close, self.trend_window),
            "panel:Z-score del retroceso":
                fx.zscore(close, self.pullback_window),
        }


class PullbackTrend(_PullbackOverlaysMixin, BaseStrategy):
    """Retroceso dentro de tendencia: comprar caídas en mercados alcistas.

    Combina lo mejor de las dos familias principales. Exige tendencia de fondo
    —como el seguimiento de tendencia— pero entra en los retrocesos —como la
    reversión a la media— en lugar de perseguir el precio.

    La ventaja práctica es el punto de entrada: al comprar tras una caída, la
    distancia a la invalidación es menor, lo que permite un tamaño de posición
    mayor para el mismo riesgo.
    """

    name = "pullback_trend"
    label = "Retroceso en tendencia"
    version = "1.0.0"
    horizon = Horizon.WEEK_2
    description = (
        "Espera a que el precio retroceda dentro de una tendencia sana para "
        "entrar a mejor precio."
    )
    exit_rule = (
        "Se cierra al alcanzar el objetivo, al perder la tendencia de fondo, o "
        "al tocar la invalidación."
    )
    target_atr_multiple = Decimal("3.5")
    stop_atr_multiple = Decimal("1.8")

    def __init__(self, trend_window: int = 100, pullback_window: int = 10,
                 pullback_z: float = -1.0) -> None:
        self.trend_window = trend_window
        self.pullback_window = pullback_window
        self.pullback_z = pullback_z

    @property
    def warmup_bars(self) -> int:
        return self.trend_window + self.pullback_window + 5

    def target_position(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["close"]
        media = fx.sma(close, self.trend_window)
        alcista = close > media
        bajista = close < media
        z = fx.zscore(close, self.pullback_window)

        pos = pd.Series(0.0, index=close.index)
        # Compra: tendencia alcista + retroceso de corto plazo.
        pos[(alcista & (z <= self.pullback_z)).fillna(False)] = 1.0
        # Venta: tendencia bajista + rebote de corto plazo.
        pos[(bajista & (z >= -self.pullback_z)).fillna(False)] = -1.0
        return pos

    def strength_metric(self, prices: pd.DataFrame) -> pd.Series:
        """Profundidad del retroceso, en sigmas."""
        return fx.zscore(prices["close"], self.pullback_window).abs()

    @property
    def strength_thresholds(self) -> tuple[float, float]:
        return (2.0, 1.3)

    def _describe(self, prices, pos):
        close = prices["close"]
        media = float(fx.sma(close, self.trend_window).iloc[-1])
        precio = float(close.iloc[-1])
        z = fx.zscore(close, self.pullback_window).iloc[-1]
        z = float(z) if not pd.isna(z) else 0.0

        if pos == 0.0:
            tendencia = "alcista" if precio > media else "bajista"
            return SignalStrength.WEAK, (
                f"Tendencia {tendencia} pero sin retroceso aprovechable: "
                f"desviación de corto plazo {z:+.2f} sigmas."
            )
        if pos > 0:
            return self._strength(abs(z), 2.0, 1.3), (
                f"Retroceso en tendencia alcista: el precio ({precio:,.2f}) se "
                f"mantiene sobre su media de {self.trend_window} sesiones "
                f"({media:,.2f}) pero ha caído {abs(z):.2f} sigmas en el corto "
                f"plazo. Entrada a mejor precio dentro de la tendencia."
            )
        return self._strength(abs(z), 2.0, 1.3), (
            f"Rebote en tendencia bajista: el precio ({precio:,.2f}) sigue bajo "
            f"su media de {self.trend_window} sesiones ({media:,.2f}) tras "
            f"subir {abs(z):.2f} sigmas. Entrada a mejor precio en la caída."
        )


def build_registry() -> dict[str, BaseStrategy]:
    """Construye el registro de estrategias disponibles.

    Se devuelve una instancia nueva en cada llamada porque algunas estrategias
    mantienen estado inyectado (el ranking del universo), y compartir instancias
    entre ejecuciones produciría resultados dependientes del orden.
    """
    return {
        s.name: s
        for s in (
            TrendFollowing(),
            MeanReversion(),
            VolatilityBreakout(),
            Seasonality(),
            RiskOffAllocation(),
            VolatilityTargeting(),
            CrossSectionalMomentum(),
            TimeSeriesMomentum(),
            PullbackTrend(),
        )
    }


REGISTRY: dict[str, BaseStrategy] = build_registry()
"""Estrategias disponibles. Añadir una nueva es añadirla a `build_registry`."""


BLOCKED_STRATEGIES: dict[str, str] = {
    "Carry": "Requiere la curva de futuros, no disponible en el bróker (ADR-0003)",
    "Term Structure": "Requiere la curva de futuros, no disponible (ADR-0003)",
    "Session Effects": "Requiere datos intradía; hoy sólo hay cierres diarios",
    "Pairs Trading": "Requiere análisis de cointegración (Módulo 05)",
    "Statistical Arbitrage": "Requiere análisis de cointegración (Módulo 05)",
    "Factor Investing": "Requiere datos fundamentales de las compañías",
    "Macro Allocation": "Parcial: falta el componente de renta fija",
    "ML Meta Model": "Combina las demás estrategias; debe construirse el último",
}
"""Estrategias del plan original aún no disponibles, con su motivo.

Se documentan explícitamente para que la ausencia sea una decisión trazable y
no un olvido.
"""
