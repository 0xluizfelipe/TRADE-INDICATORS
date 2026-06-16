"""Estratégias de análise: cada uma combina critérios independentes que somam pontos.

O sinal só vale quando o score atinge o limiar — confluência de evidências, nunca
um indicador isolado. As mesmas funções alimentam a análise ao vivo, o backtest e
o laboratório, garantindo que o que foi testado é o que será usado.

Estratégias disponíveis:
  confluencia   Indicadores clássicos multi-timeframe (EMA, RSI, MACD, BB, ADX, volume)
  tendencia_pa  Tendência + price action: pullback na média com padrão de candle
  reversao      Reversão à média: extremos de RSI/Bollinger em suporte com padrão
  rompimento    Rompimento de máximas/mínimas de 20 candles com volume e força
"""

from dataclasses import dataclass
from typing import Callable

import pandas as pd

LIMIAR_FORTE = 70
LIMIAR_MODERADO = 55

Condicao = Callable[[pd.DataFrame, pd.DataFrame], pd.Series]


@dataclass
class Criterio:
    nome: str
    pontos: int
    descricao: str
    compra: Condicao
    venda: Condicao


@dataclass
class Estrategia:
    nome: str
    titulo: str
    criterios: list[Criterio]


def _alinhar_contexto(df: pd.DataFrame, df_maior: pd.DataFrame) -> pd.DataFrame:
    """Projeta os indicadores do timeframe maior sobre o menor, sem olhar o futuro.

    Usa shift(1): em cada candle do timeframe menor só se conhece o último candle
    JÁ FECHADO do timeframe maior — exatamente como em uma operação real.
    """
    contexto = df_maior[["fechamento", "ema50", "ema200", "macd_hist"]].shift(1)
    contexto = contexto.reindex(df.index.union(contexto.index)).ffill().reindex(df.index)
    return contexto


# ---------------------------------------------------------------------------
# Condições reutilizáveis (df = timeframe operado, ctx = contexto do maior)
# ---------------------------------------------------------------------------

def _recente(serie: pd.Series, janela: int = 3) -> pd.Series:
    """Verdadeiro se a condição ocorreu em algum dos últimos `janela` candles."""
    return serie.rolling(janela).max() > 0


def _cruz_macd(df, alta: bool) -> pd.Series:
    if alta:
        cruz = (df["macd"] > df["macd_sinal"]) & (df["macd"].shift(1) <= df["macd_sinal"].shift(1))
    else:
        cruz = (df["macd"] < df["macd_sinal"]) & (df["macd"].shift(1) >= df["macd_sinal"].shift(1))
    return _recente(cruz)


def _rsi_gatilho(df, alta: bool) -> pd.Series:
    rsi, rsi_ant = df["rsi"], df["rsi"].shift(1)
    if alta:
        saiu_extremo = _recente((rsi_ant < 30) & (rsi >= 30))
        pullback = rsi.between(40, 60) & (rsi > rsi_ant) & (df["fechamento"] > df["ema50"])
    else:
        saiu_extremo = _recente((rsi_ant > 70) & (rsi <= 70))
        pullback = rsi.between(40, 60) & (rsi < rsi_ant) & (df["fechamento"] < df["ema50"])
    return saiu_extremo | pullback


def _bollinger_reversao(df, alta: bool) -> pd.Series:
    if alta:
        return _recente(df["minima"] <= df["bb_inferior"]) & (df["fechamento"] > df["bb_inferior"])
    return _recente(df["maxima"] >= df["bb_superior"]) & (df["fechamento"] < df["bb_superior"])


def _padrao_candle(df, alta: bool) -> pd.Series:
    """Qualquer padrão de candle de reversão (martelo, engolfo, harami, estrela etc.)."""
    return _recente(df["pa_reversao_alta" if alta else "pa_reversao_baixa"], 2)


def _divergencia(df, alta: bool) -> pd.Series:
    """Divergência regular RSI x preço nos últimos candles (sinal de reversão)."""
    return _recente(df["pa_div_alta" if alta else "pa_div_baixa"], 5)


def _fib_zona(df, alta: bool) -> pd.Series:
    """Preço na zona de ouro de Fibonacci (50%–61,8%) do último swing."""
    return _recente(df["pa_fib_long" if alta else "pa_fib_short"], 3)


def _pullback_media(df, alta: bool) -> pd.Series:
    """Preço visitou a EMA21/EMA50 e retomou na direção da tendência."""
    if alta:
        tocou = _recente((df["minima"] <= df["ema21"]) | (df["minima"] <= df["ema50"]))
        return tocou & (df["fechamento"] > df["ema21"])
    tocou = _recente((df["maxima"] >= df["ema21"]) | (df["maxima"] >= df["ema50"]))
    return tocou & (df["fechamento"] < df["ema21"])


def _pullback_ema10(df, alta: bool) -> pd.Series:
    """Pullback até a EMA10 e fechamento retomando a tendência (estratégia 10 EMA)."""
    if alta:
        tocou = _recente(df["minima"] <= df["ema10"])
        return tocou & (df["fechamento"] > df["ema10"])
    tocou = _recente(df["maxima"] >= df["ema10"])
    return tocou & (df["fechamento"] < df["ema10"])


def _perto_nivel(df, alta: bool) -> pd.Series:
    """Preço reagindo no último suporte (compra) ou resistência (venda) confirmados."""
    margem = 0.5 * df["atr"]
    if alta:
        return (df["minima"] <= df["pa_suporte"] + margem) & (df["fechamento"] >= df["pa_suporte"])
    return (df["maxima"] >= df["pa_resistencia"] - margem) & (df["fechamento"] <= df["pa_resistencia"])


def _volume_acima(df, fator: float) -> pd.Series:
    return df["volume"] > fator * df["volume_media"]


# ---------------------------------------------------------------------------
# Definição das estratégias
# ---------------------------------------------------------------------------

ESTRATEGIAS: dict[str, Estrategia] = {}


def _registrar(estrategia: Estrategia):
    total = sum(c.pontos for c in estrategia.criterios)
    assert total == 100, f"{estrategia.nome}: pontos somam {total}, esperado 100"
    ESTRATEGIAS[estrategia.nome] = estrategia


_registrar(Estrategia(
    nome="confluencia",
    titulo="Confluência de indicadores multi-timeframe",
    criterios=[
        Criterio("ctx_tendencia", 15, "Tendência do timeframe maior (preço vs EMA200)",
                 lambda df, ctx: ctx["fechamento"] > ctx["ema200"],
                 lambda df, ctx: ctx["fechamento"] < ctx["ema200"]),
        Criterio("ctx_emas", 10, "EMA50 vs EMA200 no timeframe maior",
                 lambda df, ctx: ctx["ema50"] > ctx["ema200"],
                 lambda df, ctx: ctx["ema50"] < ctx["ema200"]),
        Criterio("ctx_macd", 5, "MACD do timeframe maior a favor",
                 lambda df, ctx: ctx["macd_hist"] > 0,
                 lambda df, ctx: ctx["macd_hist"] < 0),
        Criterio("tendencia_local", 10, "Preço vs EMA200 no timeframe operado",
                 lambda df, ctx: df["fechamento"] > df["ema200"],
                 lambda df, ctx: df["fechamento"] < df["ema200"]),
        Criterio("rsi_gatilho", 15, "RSI: saída de extremo ou pullback retomando",
                 lambda df, ctx: _rsi_gatilho(df, True),
                 lambda df, ctx: _rsi_gatilho(df, False)),
        Criterio("macd_cruzamento", 15, "Cruzamento do MACD nos últimos 3 candles",
                 lambda df, ctx: _cruz_macd(df, True),
                 lambda df, ctx: _cruz_macd(df, False)),
        Criterio("bollinger_reversao", 10, "Reversão na Banda de Bollinger",
                 lambda df, ctx: _bollinger_reversao(df, True),
                 lambda df, ctx: _bollinger_reversao(df, False)),
        Criterio("adx_forca", 10, "ADX > 20 (tendência com força)",
                 lambda df, ctx: df["adx"] > 20,
                 lambda df, ctx: df["adx"] > 20),
        Criterio("volume_confirmacao", 10, "Volume acima da média",
                 lambda df, ctx: _volume_acima(df, 1.2),
                 lambda df, ctx: _volume_acima(df, 1.2)),
    ],
))

_registrar(Estrategia(
    nome="tendencia_pa",
    titulo="Tendência + price action (pullback com padrão de candle)",
    criterios=[
        Criterio("ctx_tendencia", 15, "Tendência do timeframe maior (preço vs EMA200)",
                 lambda df, ctx: ctx["fechamento"] > ctx["ema200"],
                 lambda df, ctx: ctx["fechamento"] < ctx["ema200"]),
        Criterio("ctx_emas", 10, "EMA50 vs EMA200 no timeframe maior",
                 lambda df, ctx: ctx["ema50"] > ctx["ema200"],
                 lambda df, ctx: ctx["ema50"] < ctx["ema200"]),
        Criterio("estrutura", 15, "Estrutura de mercado (topos/fundos na direção)",
                 lambda df, ctx: df["pa_estrutura_alta"],
                 lambda df, ctx: df["pa_estrutura_baixa"]),
        Criterio("pullback_media", 15, "Pullback na EMA21/EMA50 com retomada",
                 lambda df, ctx: _pullback_media(df, True),
                 lambda df, ctx: _pullback_media(df, False)),
        Criterio("padrao_candle", 25, "Padrão de reversão (engolfo/martelo/estrela)",
                 lambda df, ctx: _padrao_candle(df, True),
                 lambda df, ctx: _padrao_candle(df, False)),
        Criterio("adx_forca", 10, "ADX > 20 (tendência com força)",
                 lambda df, ctx: df["adx"] > 20,
                 lambda df, ctx: df["adx"] > 20),
        Criterio("volume_confirmacao", 10, "Volume acima da média",
                 lambda df, ctx: _volume_acima(df, 1.2),
                 lambda df, ctx: _volume_acima(df, 1.2)),
    ],
))

_registrar(Estrategia(
    nome="reversao",
    titulo="Reversão à média (extremos com price action)",
    criterios=[
        Criterio("rsi_extremo", 25, "RSI em zona extrema nos últimos 3 candles",
                 lambda df, ctx: _recente(df["rsi"] < 30),
                 lambda df, ctx: _recente(df["rsi"] > 70)),
        Criterio("bollinger_extremo", 20, "Preço esticado além da Banda de Bollinger",
                 lambda df, ctx: _recente(df["minima"] <= df["bb_inferior"]),
                 lambda df, ctx: _recente(df["maxima"] >= df["bb_superior"])),
        Criterio("padrao_candle", 25, "Padrão de reversão (engolfo/martelo/estrela)",
                 lambda df, ctx: _padrao_candle(df, True),
                 lambda df, ctx: _padrao_candle(df, False)),
        Criterio("nivel", 20, "Reagindo em suporte/resistência confirmado",
                 lambda df, ctx: _perto_nivel(df, True),
                 lambda df, ctx: _perto_nivel(df, False)),
        Criterio("volume_pico", 10, "Pico de volume (capitulação/euforia)",
                 lambda df, ctx: _volume_acima(df, 1.5),
                 lambda df, ctx: _volume_acima(df, 1.5)),
    ],
))

_registrar(Estrategia(
    nome="rompimento",
    titulo="Rompimento de 20 candles com volume e força",
    criterios=[
        Criterio("rompeu", 30, "Fechamento além do extremo de 20 candles",
                 lambda df, ctx: df["pa_rompimento_alta"],
                 lambda df, ctx: df["pa_rompimento_baixa"]),
        Criterio("volume_forte", 20, "Volume 50% acima da média",
                 lambda df, ctx: _volume_acima(df, 1.5),
                 lambda df, ctx: _volume_acima(df, 1.5)),
        Criterio("adx_forte", 15, "ADX > 25 (força clara)",
                 lambda df, ctx: df["adx"] > 25,
                 lambda df, ctx: df["adx"] > 25),
        Criterio("ctx_tendencia", 15, "Tendência do timeframe maior a favor",
                 lambda df, ctx: ctx["fechamento"] > ctx["ema200"],
                 lambda df, ctx: ctx["fechamento"] < ctx["ema200"]),
        Criterio("estrutura", 20, "Estrutura de mercado na direção do rompimento",
                 lambda df, ctx: df["pa_estrutura_alta"],
                 lambda df, ctx: df["pa_estrutura_baixa"]),
    ],
))

_registrar(Estrategia(
    nome="divergencia",
    titulo="Divergência RSI + confirmação de price action",
    criterios=[
        Criterio("divergencia", 25, "Divergência regular RSI x preço",
                 lambda df, ctx: _divergencia(df, True),
                 lambda df, ctx: _divergencia(df, False)),
        Criterio("padrao_candle", 20, "Padrão de candle de reversão",
                 lambda df, ctx: _padrao_candle(df, True),
                 lambda df, ctx: _padrao_candle(df, False)),
        Criterio("nivel", 15, "Reagindo em suporte/resistência confirmado",
                 lambda df, ctx: _perto_nivel(df, True),
                 lambda df, ctx: _perto_nivel(df, False)),
        Criterio("rsi_zona", 15, "RSI saindo de zona extrema",
                 lambda df, ctx: _recente(df["rsi"] < 35),
                 lambda df, ctx: _recente(df["rsi"] > 65)),
        Criterio("macd_cruzamento", 15, "Cruzamento do MACD a favor",
                 lambda df, ctx: _cruz_macd(df, True),
                 lambda df, ctx: _cruz_macd(df, False)),
        Criterio("volume", 10, "Volume acima da média",
                 lambda df, ctx: _volume_acima(df, 1.2),
                 lambda df, ctx: _volume_acima(df, 1.2)),
    ],
))

_registrar(Estrategia(
    nome="fibonacci",
    titulo="Retração de Fibonacci (zona de ouro 50%–61,8%)",
    criterios=[
        Criterio("ctx_tendencia", 15, "Tendência do timeframe maior a favor",
                 lambda df, ctx: ctx["fechamento"] > ctx["ema200"],
                 lambda df, ctx: ctx["fechamento"] < ctx["ema200"]),
        Criterio("fib_zona", 30, "Preço na zona de ouro de Fibonacci do último swing",
                 lambda df, ctx: _fib_zona(df, True),
                 lambda df, ctx: _fib_zona(df, False)),
        Criterio("estrutura", 15, "Estrutura de mercado a favor",
                 lambda df, ctx: df["pa_estrutura_alta"],
                 lambda df, ctx: df["pa_estrutura_baixa"]),
        Criterio("padrao_candle", 20, "Padrão de candle de reversão na zona",
                 lambda df, ctx: _padrao_candle(df, True),
                 lambda df, ctx: _padrao_candle(df, False)),
        Criterio("adx_forca", 10, "ADX > 20 (tendência com força)",
                 lambda df, ctx: df["adx"] > 20,
                 lambda df, ctx: df["adx"] > 20),
        Criterio("volume", 10, "Volume acima da média",
                 lambda df, ctx: _volume_acima(df, 1.2),
                 lambda df, ctx: _volume_acima(df, 1.2)),
    ],
))

_registrar(Estrategia(
    nome="tendencia_ema",
    titulo="Tendência com EMA10 e volume contraído no pullback",
    criterios=[
        Criterio("ctx_tendencia", 15, "Tendência do timeframe maior a favor",
                 lambda df, ctx: ctx["fechamento"] > ctx["ema200"],
                 lambda df, ctx: ctx["fechamento"] < ctx["ema200"]),
        Criterio("tendencia_local", 15, "EMA10 a favor da EMA50",
                 lambda df, ctx: df["ema10"] > df["ema50"],
                 lambda df, ctx: df["ema10"] < df["ema50"]),
        Criterio("pullback_ema10", 20, "Pullback até a EMA10 com retomada no fechamento",
                 lambda df, ctx: _pullback_ema10(df, True),
                 lambda df, ctx: _pullback_ema10(df, False)),
        Criterio("volume_contraido", 15, "Volume do pullback abaixo da média (correção fraca)",
                 lambda df, ctx: _recente(df["pa_volume_contraido"]),
                 lambda df, ctx: _recente(df["pa_volume_contraido"])),
        Criterio("estrutura", 15, "Estrutura de mercado a favor",
                 lambda df, ctx: df["pa_estrutura_alta"],
                 lambda df, ctx: df["pa_estrutura_baixa"]),
        Criterio("padrao_candle", 10, "Candle de continuação/retomada",
                 lambda df, ctx: _padrao_candle(df, True),
                 lambda df, ctx: _padrao_candle(df, False)),
        Criterio("adx_forca", 10, "ADX > 20 (tendência com força)",
                 lambda df, ctx: df["adx"] > 20,
                 lambda df, ctx: df["adx"] > 20),
    ],
))


# ---------------------------------------------------------------------------
# Cálculo de scores e avaliação
# ---------------------------------------------------------------------------

def calcular_scores(df: pd.DataFrame, df_maior: pd.DataFrame,
                    estrategia: str = "confluencia") -> pd.DataFrame:
    """Score de compra e de venda (0 a 100) em cada candle, para uma estratégia."""
    est = ESTRATEGIAS[estrategia]
    ctx = _alinhar_contexto(df, df_maior)
    resultado = pd.DataFrame(index=df.index)
    for criterio in est.criterios:
        resultado[f"compra_{criterio.nome}"] = criterio.compra(df, ctx).fillna(False).astype(bool)
        resultado[f"venda_{criterio.nome}"] = criterio.venda(df, ctx).fillna(False).astype(bool)
    for direcao in ("compra", "venda"):
        resultado[f"score_{direcao}"] = sum(
            resultado[f"{direcao}_{c.nome}"].astype(int) * c.pontos for c in est.criterios
        )
    return resultado


def classificar(score: int) -> str:
    if score >= LIMIAR_FORTE:
        return "FORTE"
    if score >= LIMIAR_MODERADO:
        return "MODERADO"
    return "FRACO"


def avaliar(df: pd.DataFrame, df_maior: pd.DataFrame, estrategia: str = "confluencia",
            atr_stop: float = 1.5, atr_alvo: float = 3.0) -> dict:
    """Avalia o último candle fechado e devolve um diagnóstico completo."""
    est = ESTRATEGIAS[estrategia]
    scores = calcular_scores(df, df_maior, estrategia)
    ultimo = scores.iloc[-1]
    candle = df.iloc[-1]

    score_compra = int(ultimo["score_compra"])
    score_venda = int(ultimo["score_venda"])
    if score_compra >= score_venda:
        direcao, score = "COMPRA", score_compra
    else:
        direcao, score = "VENDA", score_venda

    prefixo = direcao.lower()
    criterios = [
        {"descricao": c.descricao, "pontos": c.pontos, "atendido": bool(ultimo[f"{prefixo}_{c.nome}"])}
        for c in est.criterios
    ]

    preco = float(candle["fechamento"])
    atr_valor = float(candle["atr"])
    if direcao == "COMPRA":
        stop = preco - atr_stop * atr_valor
        alvo = preco + atr_alvo * atr_valor
    else:
        stop = preco + atr_stop * atr_valor
        alvo = preco - atr_alvo * atr_valor

    return {
        "estrategia": est.titulo,
        "direcao": direcao,
        "score": score,
        "score_compra": score_compra,
        "score_venda": score_venda,
        "forca": classificar(score),
        "criterios": criterios,
        "preco": preco,
        "stop": stop,
        "alvo": alvo,
        "atr": atr_valor,
        "rsi": float(candle["rsi"]),
        "adx": float(candle["adx"]),
        "data_candle": df.index[-1],
    }
