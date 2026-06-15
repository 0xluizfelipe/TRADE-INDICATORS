"""Price action: padrões de candle, estrutura de mercado, suporte/resistência e rompimentos.

Tudo é calculado de forma vetorizada e SEM olhar o futuro:
- Padrões de candle usam apenas o candle atual e o anterior.
- Swings (topos/fundos) usam fractais de 5 candles e só são considerados
  "confirmados" 2 candles depois — como um trader veria no gráfico real.
"""

import numpy as np
import pandas as pd


def _penultimo_valor(eventos: pd.Series) -> pd.Series:
    """Para uma série esparsa de eventos (NaN fora deles), devolve em cada candle
    o valor do PENÚLTIMO evento já ocorrido."""
    valores = eventos.dropna()
    resultado = pd.Series(np.nan, index=eventos.index)
    resultado.loc[valores.index] = valores.shift(1)
    return resultado.ffill()


def adicionar_priceaction(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta as colunas de price action ao DataFrame de candles."""
    df = df.copy()
    o, h, l, c = df["abertura"], df["maxima"], df["minima"], df["fechamento"]

    corpo = (c - o).abs()
    corpo_min = pd.concat([o, c], axis=1).min(axis=1)
    corpo_max = pd.concat([o, c], axis=1).max(axis=1)
    sombra_inferior = corpo_min - l
    sombra_superior = h - corpo_max
    amplitude = (h - l).replace(0, np.nan)

    # --- Padrões de candle de reversão ---
    # Martelo: sombra inferior longa (>= 2x corpo), quase sem sombra superior
    df["pa_martelo"] = (
        (sombra_inferior >= 2 * corpo)
        & (sombra_superior <= 0.3 * amplitude)
        & (corpo >= 0.05 * amplitude)
    )
    # Estrela cadente: o espelho do martelo
    df["pa_estrela"] = (
        (sombra_superior >= 2 * corpo)
        & (sombra_inferior <= 0.3 * amplitude)
        & (corpo >= 0.05 * amplitude)
    )
    # Engolfo: corpo atual engole o corpo do candle anterior, na direção oposta
    df["pa_engolfo_alta"] = (
        (c > o) & (c.shift(1) < o.shift(1))
        & (c >= o.shift(1)) & (o <= c.shift(1))
        & (corpo > corpo.shift(1))
    )
    df["pa_engolfo_baixa"] = (
        (c < o) & (c.shift(1) > o.shift(1))
        & (c <= o.shift(1)) & (o >= c.shift(1))
        & (corpo > corpo.shift(1))
    )

    # --- Estrutura de mercado (fractais de 5 candles, confirmados 2 depois) ---
    eh_topo = h == h.rolling(5, center=True).max()
    eh_fundo = l == l.rolling(5, center=True).min()
    topos = h.where(eh_topo).shift(2)    # valor do topo, conhecido só na confirmação
    fundos = l.where(eh_fundo).shift(2)

    ultimo_topo = topos.ffill()
    penultimo_topo = _penultimo_valor(topos)
    ultimo_fundo = fundos.ffill()
    penultimo_fundo = _penultimo_valor(fundos)

    # Topos e fundos ascendentes = tendência de alta estrutural (e vice-versa)
    df["pa_estrutura_alta"] = (ultimo_topo > penultimo_topo) & (ultimo_fundo > penultimo_fundo)
    df["pa_estrutura_baixa"] = (ultimo_topo < penultimo_topo) & (ultimo_fundo < penultimo_fundo)

    # Último fundo/topo confirmados funcionam como suporte/resistência
    df["pa_suporte"] = ultimo_fundo
    df["pa_resistencia"] = ultimo_topo

    # --- Rompimentos (fechamento além do extremo dos últimos 20 candles) ---
    df["pa_rompimento_alta"] = c > h.rolling(20).max().shift(1)
    df["pa_rompimento_baixa"] = c < l.rolling(20).min().shift(1)
    return df
