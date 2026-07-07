"""Mede a duração típica das operações de uma configuração, a partir do backtest."""

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

from cripto import TIMEFRAME_CONTEXTO, backtest, dados
from cripto.indicadores import adicionar_indicadores
from cripto.priceaction import adicionar_priceaction

CONFIGURACOES = [
    ("DOGEUSDT", "4h", "confluencia", 2.0, 1.0, 70),
    ("ADAUSDT", "4h", "confluencia", 2.0, 1.0, 70),
    ("XAUTUSDT", "1d", "reversao", 2.0, 1.0, 70),
    ("BCHUSDT", "1d", "reversao", 2.0, 1.0, 70),
]

for simbolo, tf, estrategia, stop, alvo, limiar in CONFIGURACOES:
    candles = 3000 if tf == "4h" else 1500
    # apenas candles FECHADOS, como no CLI/laboratório (o candle aberto ainda muda)
    df = adicionar_priceaction(adicionar_indicadores(
        dados.buscar_candles(simbolo, tf, candles, apenas_fechados=True)))
    df_maior = adicionar_indicadores(
        dados.buscar_candles(simbolo, TIMEFRAME_CONTEXTO[tf], max(400, candles // 4),
                             apenas_fechados=True))
    try:
        res = backtest.executar(df, df_maior, simbolo=simbolo, timeframe=tf,
                                estrategia=estrategia, limiar=limiar,
                                atr_stop=stop, atr_alvo=alvo)
    except ValueError as erro:
        print(f"{simbolo}: {erro}")
        continue
    fechadas = [op for op in res.operacoes if op.resultado != "ABERTA"]
    if not fechadas:
        print(f"{simbolo} {tf} {estrategia}/{limiar}: nenhuma operação fechada no histórico")
        continue
    duracoes = pd.Series([op.saida_data - op.entrada_data for op in fechadas])
    vitorias = sum(1 for op in fechadas if op.lucro > 0)
    print(f"{simbolo} {tf} {estrategia}/{limiar}: {len(fechadas)} operações | "
          f"acerto {100 * vitorias / len(fechadas):.0f}%")
    print(f"  duração mediana: {duracoes.median()} | média: {duracoes.mean()} | "
          f"75% encerram em até: {duracoes.quantile(0.75)}")
