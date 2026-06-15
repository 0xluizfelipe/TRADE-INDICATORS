"""Indicadores técnicos clássicos, implementados com pandas (fórmulas de Wilder onde aplicável).

Indicadores incluídos — os mais usados e validados por traders e literatura:
- EMA 9/21/50/200 (tendência)
- RSI 14 (força/momento, Wilder)
- MACD 12/26/9 (momento e reversões)
- Bandas de Bollinger 20/2 (volatilidade e extremos)
- ATR 14 (volatilidade para dimensionar stop)
- ADX 14 (força da tendência)
- Média de volume 20 (confirmação)
"""

import numpy as np
import pandas as pd


def ema(serie: pd.Series, periodo: int) -> pd.Series:
    return serie.ewm(span=periodo, adjust=False).mean()


def rsi(fechamento: pd.Series, periodo: int = 14) -> pd.Series:
    delta = fechamento.diff()
    ganho = delta.clip(lower=0).ewm(alpha=1 / periodo, adjust=False).mean()
    perda = (-delta.clip(upper=0)).ewm(alpha=1 / periodo, adjust=False).mean()
    rs = ganho / perda.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def macd(fechamento: pd.Series, rapida: int = 12, lenta: int = 26, sinal: int = 9):
    linha = ema(fechamento, rapida) - ema(fechamento, lenta)
    linha_sinal = ema(linha, sinal)
    return linha, linha_sinal, linha - linha_sinal


def bollinger(fechamento: pd.Series, periodo: int = 20, desvios: float = 2.0):
    media = fechamento.rolling(periodo).mean()
    desvio = fechamento.rolling(periodo).std(ddof=0)
    return media, media + desvios * desvio, media - desvios * desvio


def _amplitude_verdadeira(df: pd.DataFrame) -> pd.Series:
    fechamento_anterior = df["fechamento"].shift(1)
    return pd.concat([
        df["maxima"] - df["minima"],
        (df["maxima"] - fechamento_anterior).abs(),
        (df["minima"] - fechamento_anterior).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    return _amplitude_verdadeira(df).ewm(alpha=1 / periodo, adjust=False).mean()


def adx(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    alta = df["maxima"].diff()
    baixa = -df["minima"].diff()
    dm_mais = pd.Series(np.where((alta > baixa) & (alta > 0), alta, 0.0), index=df.index)
    dm_menos = pd.Series(np.where((baixa > alta) & (baixa > 0), baixa, 0.0), index=df.index)
    atr_suave = _amplitude_verdadeira(df).ewm(alpha=1 / periodo, adjust=False).mean()
    di_mais = 100 * dm_mais.ewm(alpha=1 / periodo, adjust=False).mean() / atr_suave
    di_menos = 100 * dm_menos.ewm(alpha=1 / periodo, adjust=False).mean() / atr_suave
    dx = 100 * (di_mais - di_menos).abs() / (di_mais + di_menos).replace(0, np.nan)
    return dx.ewm(alpha=1 / periodo, adjust=False).mean().fillna(0.0)


def adicionar_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta todas as colunas de indicadores ao DataFrame de candles."""
    df = df.copy()
    fechamento = df["fechamento"]

    df["ema9"] = ema(fechamento, 9)
    df["ema21"] = ema(fechamento, 21)
    df["ema50"] = ema(fechamento, 50)
    df["ema200"] = ema(fechamento, 200)
    df["rsi"] = rsi(fechamento)
    df["macd"], df["macd_sinal"], df["macd_hist"] = macd(fechamento)
    df["bb_media"], df["bb_superior"], df["bb_inferior"] = bollinger(fechamento)
    df["atr"] = atr(df)
    df["adx"] = adx(df)
    df["volume_media"] = df["volume"].rolling(20).mean()
    return df
