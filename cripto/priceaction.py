"""Price action: padrões de candle, estrutura, suporte/resistência, rompimentos,
divergências (RSI x preço) e zona de Fibonacci.

Tudo é calculado de forma vetorizada e SEM olhar o futuro:
- Padrões de candle usam apenas o candle atual e os anteriores.
- Swings (topos/fundos) usam fractais de 5 candles e só são considerados
  "confirmados" 2 candles depois — como um trader veria no gráfico real.
- Divergências comparam os dois últimos swings de preço com o RSI nesses mesmos
  pontos (precisa que adicionar_indicadores tenha rodado antes, para existir o RSI).
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
    alta = c > o
    baixa = c < o

    # ---------------- Padrões de UM candle ----------------
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
    # Doji: corpo minúsculo — indecisão (neutro, usado como aviso de reversão em extremos)
    df["pa_doji"] = corpo <= 0.1 * amplitude

    # ---------------- Padrões de DOIS candles ----------------
    o1, c1 = o.shift(1), c.shift(1)
    corpo1 = corpo.shift(1)
    alta1, baixa1 = alta.shift(1), baixa.shift(1)
    meio1 = (o1 + c1) / 2

    # Engolfo: corpo atual engole o corpo anterior, na direção oposta
    df["pa_engolfo_alta"] = (alta & baixa1 & (c >= o1) & (o <= c1) & (corpo > corpo1))
    df["pa_engolfo_baixa"] = (baixa & alta1 & (c <= o1) & (o >= c1) & (corpo > corpo1))
    # Harami: corpo pequeno dentro do corpo grande anterior (perda de força)
    df["pa_harami_alta"] = (alta & baixa1 & (corpo < corpo1) & (c <= o1) & (o >= c1))
    df["pa_harami_baixa"] = (baixa & alta1 & (corpo < corpo1) & (c >= o1) & (o <= c1))
    # Piercing / Nuvem negra: penetração de mais da metade do corpo anterior
    df["pa_piercing"] = (alta & baixa1 & (o < c1) & (c > meio1) & (c < o1))
    df["pa_nuvem_negra"] = (baixa & alta1 & (o > c1) & (c < meio1) & (c > o1))
    # Tweezer: dois candles com a mesma máxima/mínima (rejeição de nível)
    tol = 0.001 * c
    df["pa_tweezer_fundo"] = baixa1 & alta & ((l - l.shift(1)).abs() <= tol)
    df["pa_tweezer_topo"] = alta1 & baixa & ((h - h.shift(1)).abs() <= tol)

    # ---------------- Padrões de TRÊS candles ----------------
    o2, c2 = o.shift(2), c.shift(2)
    corpo2 = corpo.shift(2)
    # Estrela da manhã / da tarde (reversão de 3 candles)
    df["pa_estrela_manha"] = (
        (c2 < o2) & (corpo2 > amplitude.shift(2) * 0.5)        # 1º: queda forte
        & (corpo1 < corpo2 * 0.5)                              # 2º: indecisão (corpo pequeno)
        & alta & (c > (o2 + c2) / 2)                           # 3º: alta que recupera metade da queda
    )
    df["pa_estrela_tarde"] = (
        (c2 > o2) & (corpo2 > amplitude.shift(2) * 0.5)
        & (corpo1 < corpo2 * 0.5)
        & baixa & (c < (o2 + c2) / 2)
    )
    # Três soldados brancos / três corvos negros (continuação forte)
    df["pa_tres_soldados"] = alta & alta1 & (c2 > o2) & (c > c1) & (c1 > c2)
    df["pa_tres_corvos"] = baixa & baixa1 & (c2 < o2) & (c < c1) & (c1 < c2)

    # ---------------- Sinais combinados de reversão (candles) ----------------
    df["pa_reversao_alta"] = (
        df["pa_martelo"] | df["pa_engolfo_alta"] | df["pa_harami_alta"]
        | df["pa_piercing"] | df["pa_tweezer_fundo"] | df["pa_estrela_manha"]
    )
    df["pa_reversao_baixa"] = (
        df["pa_estrela"] | df["pa_engolfo_baixa"] | df["pa_harami_baixa"]
        | df["pa_nuvem_negra"] | df["pa_tweezer_topo"] | df["pa_estrela_tarde"]
    )

    # ---------------- Estrutura de mercado (fractais de 5, confirmados 2 depois) ----------------
    eh_topo = h == h.rolling(5, center=True).max()
    eh_fundo = l == l.rolling(5, center=True).min()
    preco_topo = h.where(eh_topo).shift(2)   # valor do topo, conhecido só na confirmação
    preco_fundo = l.where(eh_fundo).shift(2)

    ult_topo, pen_topo = preco_topo.ffill(), _penultimo_valor(preco_topo)
    ult_fundo, pen_fundo = preco_fundo.ffill(), _penultimo_valor(preco_fundo)

    df["pa_estrutura_alta"] = (ult_topo > pen_topo) & (ult_fundo > pen_fundo)
    df["pa_estrutura_baixa"] = (ult_topo < pen_topo) & (ult_fundo < pen_fundo)
    df["pa_suporte"] = ult_fundo
    df["pa_resistencia"] = ult_topo

    # ---------------- Rompimentos (fechamento além do extremo de 20 candles) ----------------
    df["pa_rompimento_alta"] = c > h.rolling(20).max().shift(1)
    df["pa_rompimento_baixa"] = c < l.rolling(20).min().shift(1)

    # ---------------- Divergências RSI x preço ----------------
    # Compara os dois últimos swings; o RSI é capturado no mesmo candle do pivô.
    if "rsi" in df.columns:
        rsi = df["rsi"]
        rsi_topo = rsi.where(eh_topo).shift(2)
        rsi_fundo = rsi.where(eh_fundo).shift(2)
        ult_rt, pen_rt = rsi_topo.ffill(), _penultimo_valor(rsi_topo)
        ult_rf, pen_rf = rsi_fundo.ffill(), _penultimo_valor(rsi_fundo)

        novo_fundo = preco_fundo.notna()   # candle em que um fundo acabou de confirmar
        novo_topo = preco_topo.notna()
        # Regular de alta: preço faz fundo MAIS BAIXO, RSI faz fundo MAIS ALTO -> reversão p/ cima
        div_alta_reg = novo_fundo & (ult_fundo < pen_fundo) & (ult_rf > pen_rf)
        # Oculta de alta: preço fundo mais alto, RSI fundo mais baixo -> continuação de alta
        div_alta_ocu = novo_fundo & (ult_fundo > pen_fundo) & (ult_rf < pen_rf)
        # Regular de baixa: preço topo mais alto, RSI topo mais baixo -> reversão p/ baixo
        div_baixa_reg = novo_topo & (ult_topo > pen_topo) & (ult_rt < pen_rt)
        # Oculta de baixa: preço topo mais baixo, RSI topo mais alto -> continuação de baixa
        div_baixa_ocu = novo_topo & (ult_topo < pen_topo) & (ult_rt > pen_rt)

        df["pa_div_alta"] = div_alta_reg.fillna(False)
        df["pa_div_baixa"] = div_baixa_reg.fillna(False)
        df["pa_div_alta_oculta"] = div_alta_ocu.fillna(False)
        df["pa_div_baixa_oculta"] = div_baixa_ocu.fillna(False)
    else:
        for col in ("pa_div_alta", "pa_div_baixa", "pa_div_alta_oculta", "pa_div_baixa_oculta"):
            df[col] = False

    # ---------------- Fibonacci: zona de ouro (50%–61,8%) do último swing ----------------
    intervalo = (ult_topo - ult_fundo).replace(0, np.nan)
    # A retração só faz sentido na direção do ÚLTIMO swing confirmado: pullback de
    # alta exige fundo -> topo (topo mais recente); de baixa, o espelho. Sem essa
    # checagem, o repique de uma queda era lido como "pullback de alta".
    ordem = pd.Series(np.arange(len(df), dtype=float), index=df.index)
    pos_topo = ordem.where(preco_topo.notna()).ffill()
    pos_fundo = ordem.where(preco_fundo.notna()).ffill()
    swing_de_alta = pos_topo > pos_fundo
    swing_de_baixa = pos_fundo > pos_topo
    # Pullback de ALTA: após subir do fundo ao topo, preço recua para a zona 50–61,8%
    zona_long_topo = ult_topo - 0.5 * intervalo
    zona_long_fundo = ult_topo - 0.618 * intervalo
    df["pa_fib_long"] = ((l <= zona_long_topo) & (l >= zona_long_fundo)
                         & (c >= zona_long_fundo) & swing_de_alta)
    # Pullback de BAIXA: após cair do topo ao fundo, preço sobe para a zona 50–61,8%
    zona_short_fundo = ult_fundo + 0.5 * intervalo
    zona_short_topo = ult_fundo + 0.618 * intervalo
    df["pa_fib_short"] = ((h >= zona_short_fundo) & (h <= zona_short_topo)
                          & (c <= zona_short_topo) & swing_de_baixa)
    df["pa_fib_long"] = df["pa_fib_long"].fillna(False)
    df["pa_fib_short"] = df["pa_fib_short"].fillna(False)

    # ---------------- Volume contraído no pullback (estratégia 10 EMA) ----------------
    # Volume da correção menor que a média = pressão vendedora/compradora fraca
    df["pa_volume_contraido"] = df["volume"] < df["volume_media"]
    return df
