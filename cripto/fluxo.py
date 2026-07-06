"""Análise de FLUXO DE ORDENS e dados independentes do preço.

As 7 estratégias clássicas leem a mesma série OHLCV — juízes lendo o mesmo
jornal. Este módulo agrega as famílias de informação que o preço NÃO contém:

- Volume delta e CVD: compra agressiva − venda agressiva (taker_base, que a
  Binance entrega em cada candle). É o fluxo REAL, não a heurística do OBV.
- Trade médio: volume ÷ nº de trades — proxy de participação institucional.
- Funding rate: posicionamento dos alavancados (extremo = multidão de um lado).
- Força relativa vs BTC: a alt tem demanda própria ou só segue o mercado?

Tudo tolerante a falha: sem futuros ou sem rede, as colunas ficam NaN/False e
os critérios da estratégia `fluxo` simplesmente não pontuam (nunca quebram).
"""

import time

import numpy as np
import pandas as pd

from . import dados

# Cache do fechamento do BTC por timeframe (referência da força relativa):
# uma varredura de 25 pares reutiliza a mesma série em vez de baixar 25 vezes.
_cache_btc: dict[str, tuple[float, pd.Series]] = {}
_VALIDADE_BTC = 180  # segundos


def _fechamento_btc(timeframe: str, candles: int) -> pd.Series:
    agora = time.time()
    em_cache = _cache_btc.get(timeframe)
    if em_cache and agora - em_cache[0] < _VALIDADE_BTC and len(em_cache[1]) >= candles:
        return em_cache[1]
    serie = dados.buscar_candles("BTCUSDT", timeframe, candles,
                                 apenas_fechados=True)["fechamento"]
    _cache_btc[timeframe] = (agora, serie)
    return serie


def adicionar_fluxo(df: pd.DataFrame, simbolo: str, timeframe: str) -> pd.DataFrame:
    """Acrescenta as colunas de fluxo/posicionamento ao DataFrame de candles."""
    df = df.copy()
    simbolo = simbolo.upper()

    # ---------------- volume delta e CVD ----------------
    if "taker_base" in df.columns:
        delta = 2 * df["taker_base"] - df["volume"]  # compra agressiva − venda agressiva
        df["delta_norm"] = (delta / df["volume"].replace(0, np.nan)).clip(-1, 1)
        df["delta_ema"] = df["delta_norm"].ewm(span=5, adjust=False).mean()
        df["cvd"] = delta.fillna(0).cumsum()
        df["cvd_ema"] = df["cvd"].ewm(span=21, adjust=False).mean()

        # Divergência CVD × preço: preço faz novo extremo de 20 candles, mas o
        # fluxo acumulado NÃO acompanha — o extremo está "vazio" de agressão.
        novo_fundo = df["minima"] <= df["minima"].rolling(20).min().shift(1)
        novo_topo = df["maxima"] >= df["maxima"].rolling(20).max().shift(1)
        df["fluxo_div_alta"] = novo_fundo & (df["cvd"] > df["cvd"].rolling(20).min().shift(1))
        df["fluxo_div_baixa"] = novo_topo & (df["cvd"] < df["cvd"].rolling(20).max().shift(1))

    # ---------------- trade médio (baleia vs varejo) ----------------
    if "trades" in df.columns:
        trade_medio = df["volume"] / df["trades"].replace(0, np.nan)
        df["trade_medio_alto"] = trade_medio > trade_medio.rolling(20).mean()

    # ---------------- funding (posicionamento dos alavancados) ----------------
    try:
        funding = dados.buscar_funding(simbolo, int(df.index[0].value // 10**6))
        alinhado = funding.reindex(df.index.union(funding.index)).ffill().reindex(df.index)
        df["funding"] = alinhado
        # percentil móvel (~15 dias em 4h): onde o funding ATUAL está vs o recente
        df["funding_perc"] = alinhado.rolling(90, min_periods=30).rank(pct=True)
    except Exception:
        df["funding"] = np.nan
        df["funding_perc"] = np.nan

    # ---------------- força relativa vs BTC ----------------
    try:
        if simbolo == "BTCUSDT":
            raise ValueError("BTC é a própria referência")
        btc = _fechamento_btc(timeframe, len(df))
        ratio = df["fechamento"] / btc.reindex(df.index).ffill()
        ratio_ema = ratio.ewm(span=20, adjust=False).mean()
        df["rs_sobe"] = (ratio_ema > ratio_ema.shift(3)).fillna(False)
        df["rs_desce"] = (ratio_ema < ratio_ema.shift(3)).fillna(False)
    except Exception:
        df["rs_sobe"] = False
        df["rs_desce"] = False

    return df
