"""Beginner lessons in Colombian Spanish (es-CO).

Each lesson answers five questions: what the concept is, its current value
(when the analysis computed it), how it is usually read, what it means for
the analyzed asset, and the limits of that reading. Values come from the
caller (application/) - this module never fetches or computes market data,
it only compares already-computed numbers with the documented thresholds
and words the result. A concept the analysis did not compute is explained
in general and marked as not available, never filled with a guess.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Callable, Mapping

ContextValue = Decimal | str


@dataclass(frozen=True)
class LessonContext:
    """Already-computed values from one analysis, keyed by the names below.

    Keys: price, sma20, sma50, sma200, rsi14, atr_pips, support, resistance,
    trend (UP/DOWN/SIDEWAYS), direction (LONG/SHORT/NO_TRADE), spread_pips,
    entry, stop_loss, take_profit, risk_pips, reward_pips,
    gross_reward_risk, net_reward_risk, units, notional, margin, leverage,
    risk_amount, reward_amount, capital, max_drawdown (fraction),
    annualized_volatility (fraction), committee_confidence, timeframe.
    """

    asset_label: str
    source: str  # e.g. "la señal de trading simulado del 2026-10-13 17:00 UTC"
    values: Mapping[str, ContextValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def number(self, key: str) -> Decimal | None:
        value = self.values.get(key)
        return value if isinstance(value, Decimal) else None

    def text(self, key: str) -> str | None:
        value = self.values.get(key)
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class Lesson:
    concept_key: str
    title: str
    what_it_is: str
    how_to_read: str
    example: str
    limitations: str
    current_value: str | None = None  # None: not computed in this analysis
    for_this_asset: str | None = None
    context_source: str | None = None
    asset_label: str | None = None


@dataclass(frozen=True)
class _Concept:
    key: str
    title: str
    aliases: tuple[str, ...]
    what_it_is: str
    how_to_read: str
    example: str
    limitations: str
    reading: Callable[[LessonContext], tuple[str, str] | None]


# --- Formatting (decimal comma, as used in Colombia) ---


def _num(value: Decimal, places: int = 2) -> str:
    return f"{value:,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _price(value: Decimal) -> str:
    return _num(value, 5)


def _money(value: Decimal) -> str:
    return f"{_num(value, 2)} USD"


def _percent(fraction: Decimal) -> str:
    return f"{_num(fraction * 100, 2)} %"


_DIRECTION = {"LONG": "COMPRA", "SHORT": "VENTA", "NO_TRADE": "NO OPERAR"}
_TREND = {"UP": "alcista", "DOWN": "bajista", "SIDEWAYS": "lateral (sin dirección clara)"}


# --- Readings: (current value, meaning for this asset), or None if the
# analysis did not compute what the concept needs. ---


def _read_trend(ctx: LessonContext) -> tuple[str, str] | None:
    trend = ctx.text("trend")
    price, sma20, sma50 = ctx.number("price"), ctx.number("sma20"), ctx.number("sma50")
    if trend is None or price is None or sma20 is None or sma50 is None:
        return None
    value = f"tendencia {_TREND.get(trend, trend)} (precio {_price(price)}, SMA20 {_price(sma20)}, SMA50 {_price(sma50)})"
    if trend == "UP":
        meaning = (
            f"En {ctx.asset_label} el precio está por encima de la SMA50 y la SMA20 también, así que los "
            "compradores han dominado en este periodo."
        )
    elif trend == "DOWN":
        meaning = (
            f"En {ctx.asset_label} el precio está por debajo de la SMA50 y la SMA20 también, así que los "
            "vendedores han dominado en este periodo."
        )
    else:
        meaning = (
            f"En {ctx.asset_label} el precio y las medias no apuntan en la misma dirección: no hay una "
            "tendencia clara, y la estrategia prefiere no operar así."
        )
    return value, meaning


def _read_moving_averages(ctx: LessonContext) -> tuple[str, str] | None:
    price = ctx.number("price")
    averages = [(name, ctx.number(key)) for name, key in (("SMA20", "sma20"), ("SMA50", "sma50"), ("SMA200", "sma200"))]
    available = [(name, value) for name, value in averages if value is not None]
    if price is None or not available:
        return None
    value = f"precio {_price(price)}; " + ", ".join(f"{name} {_price(v)}" for name, v in available)
    missing = [name for name, v in averages if v is None]
    above = [name for name, v in available if price > v]
    below = [name for name, v in available if price <= v]
    parts = []
    if above:
        parts.append(f"el precio de {ctx.asset_label} está por encima de {', '.join(above)}")
    if below:
        parts.append(f"por debajo o igual a {', '.join(below)}")
    meaning = "Ahora " + " y ".join(parts) + "."
    if missing:
        meaning += f" {', '.join(missing)} no se calculó en este análisis (no disponible)."
    return value, meaning


def _read_rsi(ctx: LessonContext) -> tuple[str, str] | None:
    rsi = ctx.number("rsi14")
    if rsi is None:
        return None
    if rsi >= 70:
        zone = "zona de sobrecompra (70 o más): subió mucho y rápido, puede frenarse o corregir"
    elif rsi <= 30:
        zone = "zona de sobreventa (30 o menos): cayó mucho y rápido, puede rebotar"
    elif rsi > 50:
        zone = "por encima de 50: el impulso reciente es comprador, sin llegar a extremos"
    elif rsi < 50:
        zone = "por debajo de 50: el impulso reciente es vendedor, sin llegar a extremos"
    else:
        zone = "justo en 50: impulso neutral"
    return f"RSI14 = {_num(rsi, 1)}", f"Para {ctx.asset_label}, un RSI de {_num(rsi, 1)} está {zone}."


def _read_support_resistance(ctx: LessonContext) -> tuple[str, str] | None:
    price, support, resistance = ctx.number("price"), ctx.number("support"), ctx.number("resistance")
    if price is None or support is None or resistance is None:
        return None
    pip = ctx.number("pip_size")
    distance = ""
    if pip is not None:
        distance = (
            f" El precio está a {_num((price - support) / pip, 1)} pips del soporte y a "
            f"{_num((resistance - price) / pip, 1)} pips de la resistencia."
        )
    return (
        f"soporte {_price(support)}, resistencia {_price(resistance)} (mínimo y máximo de las últimas 24 velas)",
        f"En {ctx.asset_label} esa es la franja reciente donde el precio ha rebotado.{distance}",
    )


def _read_volatility(ctx: LessonContext) -> tuple[str, str] | None:
    atr_pips = ctx.number("atr_pips")
    if atr_pips is not None:
        return (
            f"ATR14 = {_num(atr_pips, 1)} pips por vela",
            f"En {ctx.asset_label} cada vela de {ctx.text('timeframe') or 'esta temporalidad'} se ha movido en "
            f"promedio unos {_num(atr_pips, 1)} pips. La estrategia exige entre 3 y 40 pips: menos es un mercado "
            "demasiado quieto para cubrir costos, más es demasiado agitado.",
        )
    volatility = ctx.number("annualized_volatility")
    if volatility is not None:
        return (
            f"volatilidad anualizada {_percent(volatility)} (el ATR no se calculó en este análisis)",
            f"{ctx.asset_label} ha variado en torno a {_percent(volatility)} al año en promedio.",
        )
    return None


def _read_spread(ctx: LessonContext) -> tuple[str, str] | None:
    spread = ctx.number("spread_pips")
    if spread is None:
        return None
    units = ctx.number("units")
    cost = ""
    if units is not None and ctx.number("pip_size") is not None:
        cost = f" Con {_num(units, 0)} unidades, ese spread cuesta {_money(units * spread * ctx.number('pip_size'))}."  # type: ignore[operator]
    return (
        f"spread supuesto de {_num(spread, 1)} pips",
        f"La simulación de {ctx.asset_label} supone que comprar cuesta {_num(spread / 2, 2)} pips más que el "
        f"precio medio y vender {_num(spread / 2, 2)} pips menos.{cost} Es un supuesto configurable: los datos "
        "gratuitos no traen el spread real.",
    )


def _read_levels(ctx: LessonContext) -> tuple[str, str] | None:
    direction = ctx.text("direction")
    entry, stop, target = ctx.number("entry"), ctx.number("stop_loss"), ctx.number("take_profit")
    if direction is None:
        return None
    if entry is None or stop is None or target is None:
        return (
            f"dirección {_DIRECTION.get(direction, direction)}: no hay entrada, stop-loss ni take-profit",
            f"Para {ctx.asset_label} la estrategia no encontró una oportunidad válida, así que no propone niveles.",
        )
    return (
        f"{_DIRECTION.get(direction, direction)}: entrada {_price(entry)}, stop-loss {_price(stop)}, take-profit {_price(target)}",
        f"Si el precio de {ctx.asset_label} llega a {_price(stop)} se acepta la pérdida planeada; si llega a "
        f"{_price(target)} se toma la ganancia. La entrada ya incluye el spread y el deslizamiento supuestos.",
    )


def _read_reward_risk(ctx: LessonContext) -> tuple[str, str] | None:
    gross, net = ctx.number("gross_reward_risk"), ctx.number("net_reward_risk")
    risk, reward = ctx.number("risk_amount"), ctx.number("reward_amount")
    if gross is None or net is None:
        return None
    detail = f" Arriesgas {_money(risk)} para intentar ganar {_money(reward)}." if risk is not None and reward is not None else ""
    return (
        f"1 : {_num(gross, 2)} por niveles; 1 : {_num(net, 2)} después de costos",
        f"En esta operación simulada de {ctx.asset_label}, por cada 1 USD arriesgado se busca ganar {_num(net, 2)}."
        f"{detail} Con una relación así, bastaría acertar alrededor de {_num(Decimal(100) / (1 + net), 0)} % "
        "de las veces para no perder dinero (sin contar rachas ni cambios del mercado).",
    )


def _read_position(ctx: LessonContext) -> tuple[str, str] | None:
    units, margin, leverage = ctx.number("units"), ctx.number("margin"), ctx.number("leverage")
    notional, capital, risk = ctx.number("notional"), ctx.number("capital"), ctx.number("risk_amount")
    if units is None or margin is None or leverage is None or notional is None:
        return None
    capital_text = f" de una cuenta hipotética de {_money(capital)}" if capital is not None else ""
    risk_text = f", calculado para que en el stop-loss se pierdan como máximo {_money(risk)}{capital_text}" if risk is not None else ""
    return (
        f"{_num(units, 0)} unidades (valor {_money(notional)}), margen {_money(margin)} con apalancamiento {_num(leverage, 0)}:1",
        f"El tamaño sale del riesgo, no de cuánto quieres ganar{risk_text}. El apalancamiento hace que con "
        f"{_money(margin)} de garantía controles {_money(notional)}: las ganancias y las pérdidas se calculan "
        "sobre el valor total, no sobre el margen.",
    )


def _read_drawdown(ctx: LessonContext) -> tuple[str, str] | None:
    drawdown = ctx.number("max_drawdown")
    if drawdown is not None:
        return (
            f"caída máxima {_percent(drawdown)}",
            f"En el periodo analizado, {ctx.asset_label} llegó a estar {_percent(-drawdown)} por debajo de su "
            "máximo anterior.",
        )
    amount = ctx.number("backtest_max_drawdown")
    if amount is not None:
        return (
            f"caída máxima del backtest {_money(amount)}",
            "Es la peor racha de la cuenta simulada, desde un máximo hasta el punto más bajo siguiente.",
        )
    return None


def _read_confidence(ctx: LessonContext) -> tuple[str, str] | None:
    confidence = ctx.number("committee_confidence")
    if confidence is None:
        return None
    return (
        f"confianza interna {_num(confidence, 2)} ({_num(confidence * 100, 0)} %)",
        f"Mide qué tan de acuerdo estuvieron las reglas del comité sobre {ctx.asset_label}. No es la "
        "probabilidad de ganar.",
    )


def _no_reading(ctx: LessonContext) -> tuple[str, str] | None:
    return None


_CONCEPTS: tuple[_Concept, ...] = (
    _Concept(
        key="tendencia",
        title="Tendencia: alcista, bajista o lateral",
        aliases=("tendencia", "tendencias", "trend", "alcista", "bajista", "lateral"),
        what_it_is=(
            "La tendencia es la dirección general del precio. Alcista: hace máximos y mínimos cada vez más altos. "
            "Bajista: cada vez más bajos. Lateral: se mueve de lado, sin dirección clara."
        ),
        how_to_read=(
            "Una forma sencilla es comparar el precio con sus medias móviles: si el precio y la media corta están "
            "por encima de la media larga, la tendencia suele leerse como alcista; si ambos están por debajo, bajista."
        ),
        example="Si EUR/USD pasa de 1,1000 a 1,1100 en una semana con pequeños retrocesos, la tendencia es alcista.",
        limitations=(
            "La tendencia se ve mejor en retrospectiva. Puede cambiar en cualquier momento, y lo que es alcista en "
            "velas de 1 hora puede ser bajista en velas diarias."
        ),
        reading=_read_trend,
    ),
    _Concept(
        key="medias_moviles",
        title="Medias móviles: SMA20, SMA50 y SMA200",
        aliases=("sma", "sma20", "sma50", "sma200", "media", "medias", "media movil", "medias moviles", "promedio movil"),
        what_it_is=(
            "Una media móvil simple (SMA) es el promedio de los últimos N precios de cierre. La SMA20 usa 20 velas, "
            "la SMA50 usa 50 y la SMA200 usa 200. Suaviza el ruido para ver la dirección."
        ),
        how_to_read=(
            "Precio por encima de la media: los compradores dominan en ese plazo. Media corta (SMA20) por encima de "
            "la larga (SMA50 o SMA200): la tendencia reciente es más fuerte que la de fondo."
        ),
        example="Si la SMA20 de EUR/USD es 1,1050 y la SMA50 es 1,1000, el promedio reciente está 50 pips por encima del de fondo.",
        limitations=(
            "Las medias van detrás del precio (son lentas): avisan tarde de los giros y dan señales falsas cuando el "
            "mercado está lateral."
        ),
        reading=_read_moving_averages,
    ),
    _Concept(
        key="rsi",
        title="RSI, sobrecompra y sobreventa",
        aliases=("rsi", "rsi14", "sobrecompra", "sobreventa", "momentum", "impulso", "fuerza relativa"),
        what_it_is=(
            "El RSI (Índice de Fuerza Relativa) compara cuánto subió el precio con cuánto bajó en las últimas 14 "
            "velas. Va de 0 a 100."
        ),
        how_to_read=(
            "Por encima de 70 se habla de sobrecompra; por debajo de 30, de sobreventa. Entre 50 y 70 el impulso es "
            "comprador; entre 30 y 50, vendedor. Esta estrategia compra con RSI entre 50 y 70 y vende entre 30 y 50."
        ),
        example="Un RSI de 75 en EUR/USD indica que las subidas recientes fueron mucho más fuertes que las caídas.",
        limitations=(
            "Sobrecompra no significa que vaya a caer: en tendencias fuertes el RSI puede quedarse en extremos mucho "
            "tiempo. Es una medida del pasado reciente, no una predicción."
        ),
        reading=_read_rsi,
    ),
    _Concept(
        key="soportes_resistencias",
        title="Soportes y resistencias",
        aliases=("soporte", "soportes", "resistencia", "resistencias", "soportes y resistencias"),
        what_it_is=(
            "Un soporte es una zona de precio donde antes aparecieron compradores y el precio dejó de caer. Una "
            "resistencia es una zona donde aparecieron vendedores y dejó de subir."
        ),
        how_to_read=(
            "Muchos operadores esperan rebotes en esas zonas, o un movimiento fuerte si el precio las rompe. Aquí se "
            "calculan de forma mecánica: el mínimo y el máximo de las últimas 24 velas."
        ),
        example="Si EUR/USD rebotó tres veces cerca de 1,0950, esa zona actúa como soporte.",
        limitations=(
            "Son zonas aproximadas, no líneas exactas, y se rompen con frecuencia. La versión mecánica de este "
            "sistema es una simplificación y no se usa para decidir la señal."
        ),
        reading=_read_support_resistance,
    ),
    _Concept(
        key="volatilidad_atr",
        title="Volatilidad y ATR",
        aliases=("volatilidad", "atr", "atr14", "rango verdadero"),
        what_it_is=(
            "La volatilidad mide cuánto se mueve el precio. El ATR (Rango Verdadero Promedio) es el tamaño promedio "
            "de las velas recientes, contando también los saltos entre una vela y la siguiente."
        ),
        how_to_read=(
            "Un ATR alto significa movimientos grandes (más oportunidad y más riesgo); uno bajo, movimientos "
            "pequeños. La estrategia usa el ATR para poner el stop-loss a 1,5 ATR y el take-profit a 3 ATR."
        ),
        example="Un ATR de 10 pips en velas de 1 hora significa que EUR/USD se ha movido unos 10 pips por hora.",
        limitations=(
            "El ATR no dice hacia dónde se moverá el precio, solo cuánto se ha movido. Puede cambiar de golpe con "
            "noticias económicas."
        ),
        reading=_read_volatility,
    ),
    _Concept(
        key="spread_pips",
        title="Spread y pips",
        aliases=("spread", "pip", "pips", "diferencial", "bid", "ask", "compra venta"),
        what_it_is=(
            "Un pip es la unidad mínima habitual de movimiento en Forex: en EUR/USD es 0,0001. El spread es la "
            "diferencia entre el precio al que puedes comprar (ask) y el precio al que puedes vender (bid)."
        ),
        how_to_read=(
            "El spread es un costo que pagas siempre, ganes o pierdas. Mientras más pequeña la operación esperada, "
            "más pesa el spread. Por eso la estrategia descarta operaciones donde los costos superan el 25 % del riesgo."
        ),
        example="Si EUR/USD cotiza 1,10000 / 1,10010, el spread es de 1 pip.",
        limitations=(
            "Los datos gratuitos de este sistema no traen bid ni ask reales: el spread es un supuesto configurable. "
            "En la realidad cambia según el bróker, la hora y las noticias."
        ),
        reading=_read_spread,
    ),
    _Concept(
        key="entrada_stop_take",
        title="Entrada, stop-loss y take-profit",
        aliases=("entrada", "stop", "stop loss", "stoploss", "sl", "take profit", "takeprofit", "tp", "niveles", "objetivo"),
        what_it_is=(
            "La entrada es el precio al que se abriría la operación. El stop-loss es el precio donde se cierra con "
            "una pérdida aceptada de antemano. El take-profit es el precio donde se cierra con ganancia."
        ),
        how_to_read=(
            "En una COMPRA el stop-loss va por debajo de la entrada y el take-profit por encima; en una VENTA, al "
            "revés. Definirlos antes de entrar evita decidir con emociones."
        ),
        example="Compra en 1,1000 con stop-loss en 1,0985 y take-profit en 1,1030: arriesgas 15 pips para ganar 30.",
        limitations=(
            "Un stop-loss no garantiza el precio: si el mercado salta (por ejemplo, al abrir el lunes), la salida "
            "puede ser peor. La simulación aplica esa regla conservadora."
        ),
        reading=_read_levels,
    ),
    _Concept(
        key="riesgo_beneficio",
        title="Relación riesgo/beneficio",
        aliases=("riesgo beneficio", "riesgo/beneficio", "beneficio riesgo", "ratio", "rr", "r:r", "relacion riesgo beneficio"),
        what_it_is="Compara cuánto puedes perder (hasta el stop-loss) con cuánto puedes ganar (hasta el take-profit).",
        how_to_read=(
            "Una relación 1:2 significa arriesgar 1 para ganar 2. Con 1:2, en teoría basta acertar un poco más de "
            "1 de cada 3 operaciones para cubrir las pérdidas. La estrategia exige al menos 1:1,5 después de costos."
        ),
        example="Arriesgar 100 USD para ganar 200 USD es una relación 1:2.",
        limitations=(
            "Una buena relación no sirve si casi nunca se alcanza el objetivo. Lo que importa es la combinación de "
            "relación y porcentaje de aciertos, y los costos reducen la relación real."
        ),
        reading=_read_reward_risk,
    ),
    _Concept(
        key="tamano_posicion",
        title="Tamaño de posición, margen y apalancamiento",
        aliases=("tamano", "tamano de posicion", "posicion", "lote", "lotes", "margen", "apalancamiento", "leverage", "unidades"),
        what_it_is=(
            "El tamaño de posición es cuántas unidades se operan. El apalancamiento permite controlar una posición "
            "más grande que tu dinero; el margen es la parte de tu dinero que queda como garantía."
        ),
        how_to_read=(
            "Una regla prudente es decidir primero cuánto arriesgar (aquí, 1 % de una cuenta hipotética de 10.000 USD) "
            "y calcular las unidades para que el stop-loss cueste exactamente eso."
        ),
        example="Con apalancamiento 30:1, una posición de 30.000 USD requiere 1.000 USD de margen.",
        limitations=(
            "El apalancamiento multiplica pérdidas igual que ganancias, y puedes perder más que el margen. En "
            "Colombia y en otros países los límites de apalancamiento dependen del regulador y del producto."
        ),
        reading=_read_position,
    ),
    _Concept(
        key="drawdown",
        title="Drawdown (caída máxima)",
        aliases=("drawdown", "caida", "caida maxima", "dd", "racha"),
        what_it_is=(
            "El drawdown es cuánto cae el precio de un activo, o el saldo de una cuenta, desde un máximo hasta el "
            "punto más bajo siguiente."
        ),
        how_to_read=(
            "Muestra el peor momento que habrías tenido que aguantar. Un drawdown de -20 % exige ganar 25 % para "
            "volver al máximo."
        ),
        example="Si una cuenta sube a 11.000 USD y luego baja a 9.900 USD, el drawdown es de -10 %.",
        limitations="Es un dato del pasado: el próximo drawdown puede ser más profundo que cualquiera de los vistos.",
        reading=_read_drawdown,
    ),
    _Concept(
        key="confianza",
        title="Confianza interna del comité",
        aliases=("confianza", "confidence", "puntuacion", "certeza"),
        what_it_is=(
            "Es una puntuación de 0 a 1 que resume qué tan de acuerdo estuvieron las reglas y los analistas del "
            "comité de inversión."
        ),
        how_to_read=(
            "Más alta significa que más reglas apuntaron en la misma dirección. Se usa para comparar decisiones del "
            "mismo sistema entre sí."
        ),
        example="Una confianza de 0,90 indica que casi todas las reglas evaluadas coincidieron.",
        limitations=(
            "No es una probabilidad: un 90 % de confianza no significa un 90 % de probabilidad de ganar. Todavía no "
            "se ha medido qué tan acertada es históricamente."
        ),
        reading=_read_confidence,
    ),
    _Concept(
        key="inversion_trading_cfd",
        title="Inversión, trading y CFD",
        aliases=("inversion", "trading", "cfd", "cfds", "diferencia", "invertir", "especular"),
        what_it_is=(
            "Invertir es comprar un activo para mantenerlo meses o años (por ejemplo, acciones o ETF). Hacer "
            "trading es abrir y cerrar operaciones en horas o días buscando movimientos cortos. Un CFD es un "
            "contrato con un bróker para ganar o perder la diferencia de precio, sin ser dueño del activo."
        ),
        how_to_read=(
            "En este sistema, /analyze es la opinión de inversión del comité (velas diarias) y /trade es una señal "
            "de trading simulado (velas de 1 hora). Son cosas distintas y pueden no coincidir."
        ),
        example="Comprar acciones de un ETF para tu retiro es inversión; comprar EUR/USD por unas horas es trading.",
        limitations=(
            "Los CFD y el trading apalancado son productos de alto riesgo: la mayoría de las cuentas minoristas "
            "pierden dinero con ellos. Este sistema solo simula; no opera con dinero real ni con ningún bróker."
        ),
        reading=_no_reading,
    ),
)

_BY_KEY = {concept.key: concept for concept in _CONCEPTS}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.replace("-", " ").replace("_", " ").split())


_ALIASES = {_normalize(alias): concept.key for concept in _CONCEPTS for alias in (concept.key, *concept.aliases)}


def concept_keys() -> tuple[str, ...]:
    return tuple(concept.key for concept in _CONCEPTS)


def concept_titles() -> tuple[tuple[str, str], ...]:
    """(key, title) for every concept, in teaching order."""
    return tuple((concept.key, concept.title) for concept in _CONCEPTS)


def find_concept(query: str) -> str | None:
    """Concept key for a user's free-text query ("RSI", "stop-loss",
    "riesgo/beneficio"), or None if it names no known concept."""
    normalized = _normalize(query)
    if normalized in _ALIASES:
        return _ALIASES[normalized]
    compact = normalized.replace(" ", "")
    for alias, key in _ALIASES.items():
        if alias.replace(" ", "") == compact:
            return key
    return None


def build_lesson(concept_key: str, context: LessonContext | None = None) -> Lesson:
    concept = _BY_KEY[concept_key]
    reading = concept.reading(context) if context is not None else None
    return Lesson(
        concept_key=concept.key,
        title=concept.title,
        what_it_is=concept.what_it_is,
        how_to_read=concept.how_to_read,
        example=concept.example,
        limitations=concept.limitations,
        current_value=reading[0] if reading else None,
        for_this_asset=reading[1] if reading else None,
        context_source=context.source if context is not None else None,
        asset_label=context.asset_label if context is not None else None,
    )


TRADE_LESSON_KEYS = (
    "tendencia",
    "medias_moviles",
    "rsi",
    "volatilidad_atr",
    "soportes_resistencias",
    "spread_pips",
    "entrada_stop_take",
    "riesgo_beneficio",
    "tamano_posicion",
)
