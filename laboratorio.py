"""Laboratório de estratégias: testa todas as combinações e mede a assertividade real.

Para cada combinação de ESTRATÉGIA x SAÍDA (stop/alvo) x LIMIAR, roda o backtest
em todos os ativos informados e mede a taxa de acerto separando:

  TREINO (primeiros 70% dos dados)  — onde é fácil "acertar" por ajuste excessivo
  TESTE  (últimos 30%, fora da amostra) — o número que realmente importa

Uma configuração só é aprovada se, NO PERÍODO DE TESTE, atingir a meta de taxa de
acerto E tiver fator de lucro > 1 (acertar muito perdendo dinheiro não serve).

Uso:
  python laboratorio.py BTCUSDT ETHUSDT SOLUSDT
  python laboratorio.py BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT --tf 4h --candles 3000 --meta 65
"""

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cripto import backtest, dados
from cripto.estrategia import ESTRATEGIAS, calcular_scores
from cripto.indicadores import adicionar_indicadores
from cripto.priceaction import adicionar_priceaction

TIMEFRAME_CONTEXTO = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}

# (stop em ATR, alvo em ATR) — risco/retorno = alvo/stop
SAIDAS = [
    (2.0, 1.0),   # RR 0,5:1 — alvo curto: acerta mais, ganha menos por acerto
    (1.5, 1.0),   # RR 0,7:1
    (1.5, 1.5),   # RR 1:1
    (2.5, 2.5),   # RR 1:1 amplo — stops menos sujeitos a ruído
    (1.5, 3.0),   # RR 2:1 — clássico: acerta menos, ganha mais por acerto
]
LIMIARES = [70, 85]
PROPORCAO_TREINO = 0.7
MINIMO_TRADES_TESTE = 8


def metricas(operacoes):
    """Taxa de acerto e fator de lucro de uma lista de operações fechadas."""
    fechadas = [op for op in operacoes if op.resultado != "ABERTA"]
    total = len(fechadas)
    if total == 0:
        return 0, 0.0, 0.0
    vitorias = sum(1 for op in fechadas if op.lucro > 0)
    ganhos = sum(op.lucro for op in fechadas if op.lucro > 0)
    perdas = abs(sum(op.lucro for op in fechadas if op.lucro < 0))
    fator = ganhos / perdas if perdas else float("inf")
    return total, 100 * vitorias / total, fator


def main():
    parser = argparse.ArgumentParser(description="Laboratório de estratégias")
    parser.add_argument("simbolos", nargs="+", help="Pares, ex: BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--tf", default="4h", choices=list(TIMEFRAME_CONTEXTO))
    parser.add_argument("--candles", type=int, default=3000)
    parser.add_argument("--meta", type=float, default=65.0, help="Taxa de acerto alvo (%%)")
    parser.add_argument("--sem-venda", action="store_true", help="Apenas operações de compra")
    args = parser.parse_args()

    simbolos = [s.upper() for s in args.simbolos]
    tf_maior = TIMEFRAME_CONTEXTO[args.tf]

    # ----- carrega os dados uma única vez -----
    mercado = {}
    for simbolo in simbolos:
        print(f"Baixando {args.candles} candles de {simbolo} ({args.tf} + contexto {tf_maior})...")
        df = adicionar_priceaction(adicionar_indicadores(
            dados.buscar_candles(simbolo, args.tf, args.candles)))
        df_maior = adicionar_indicadores(
            dados.buscar_candles(simbolo, tf_maior, max(400, args.candles // 4)))
        corte = df.index[int(len(df) * PROPORCAO_TREINO)]
        mercado[simbolo] = (df, df_maior, corte)

    # ----- roda a grade de combinações -----
    print(f"\nTestando {len(ESTRATEGIAS)} estratégias x {len(SAIDAS)} saídas x "
          f"{len(LIMIARES)} limiares em {len(simbolos)} ativos...")
    resultados = []
    for nome_estrategia in ESTRATEGIAS:
        scores_cache = {
            simbolo: calcular_scores(df, df_maior, nome_estrategia)
            for simbolo, (df, df_maior, _) in mercado.items()
        }
        for stop, alvo in SAIDAS:
            for limiar in LIMIARES:
                ops_treino, ops_teste, retornos = [], [], []
                for simbolo, (df, df_maior, corte) in mercado.items():
                    res = backtest.executar(
                        df, df_maior, simbolo=simbolo, timeframe=args.tf,
                        scores=scores_cache[simbolo], limiar=limiar,
                        atr_stop=stop, atr_alvo=alvo,
                        permitir_venda=not args.sem_venda,
                    )
                    ops_treino += [op for op in res.operacoes if op.entrada_data < corte]
                    ops_teste += [op for op in res.operacoes if op.entrada_data >= corte]
                    retornos.append(res.retorno_total)
                n_treino, wr_treino, _ = metricas(ops_treino)
                n_teste, wr_teste, fl_teste = metricas(ops_teste)
                resultados.append({
                    "estrategia": nome_estrategia, "stop": stop, "alvo": alvo,
                    "limiar": limiar, "n_treino": n_treino, "n_teste": n_teste,
                    "wr_treino": wr_treino, "wr_teste": wr_teste, "fl_teste": fl_teste,
                    "retorno_medio": sum(retornos) / len(retornos),
                    "aprovada": wr_teste >= args.meta and fl_teste > 1.0
                                and n_teste >= MINIMO_TRADES_TESTE,
                })

    confiaveis = [r for r in resultados if r["n_teste"] >= MINIMO_TRADES_TESTE]
    descartadas = len(resultados) - len(confiaveis)
    confiaveis.sort(key=lambda r: (r["aprovada"], r["wr_teste"]), reverse=True)

    # ----- relatório -----
    L = 98
    print()
    print("=" * L)
    print(f"  RESULTADO DO LABORATÓRIO  |  {', '.join(simbolos)}  |  {args.tf}")
    print(f"  Meta: acerto >= {args.meta:.0f}% no TESTE (30% finais, fora da amostra) "
          f"com fator de lucro > 1")
    print("=" * L)
    cab = (f"  {'ESTRATÉGIA':<14}{'STOP':>6}{'ALVO':>6}{'RR':>6}{'LIMIAR':>8}"
           f"{'TRADES':>8}{'AC.TREINO':>11}{'AC.TESTE':>10}{'FL TESTE':>10}{'RET.MÉD':>9}  META")
    print(cab)
    print("-" * L)
    for r in confiaveis:
        rr = r["alvo"] / r["stop"]
        fl = "inf" if r["fl_teste"] == float("inf") else f"{r['fl_teste']:.2f}"
        marca = "  <<<" if r["aprovada"] else ""
        print(f"  {r['estrategia']:<14}{r['stop']:>6.1f}{r['alvo']:>6.1f}{rr:>6.2f}"
              f"{r['limiar']:>8}{r['n_treino'] + r['n_teste']:>8}"
              f"{r['wr_treino']:>10.1f}%{r['wr_teste']:>9.1f}%{fl:>10}"
              f"{r['retorno_medio']:>8.1f}%{marca}")
    print("-" * L)
    if descartadas:
        print(f"  ({descartadas} combinações ocultadas por terem menos de "
              f"{MINIMO_TRADES_TESTE} operações no período de teste)")

    aprovadas = [r for r in confiaveis if r["aprovada"]]
    if aprovadas:
        melhor = aprovadas[0]
        print(f"\n  {len(aprovadas)} configuração(ões) bateram a meta FORA da amostra.")
        print("  A melhor delas pode ser usada assim:")
        print(f"    python analisar.py PAR --tf {args.tf} --estrategia {melhor['estrategia']}"
              f" --stop {melhor['stop']} --alvo {melhor['alvo']} --limiar {melhor['limiar']}")
        print(f"    python analisar.py PAR --tf {args.tf} --backtest --estrategia "
              f"{melhor['estrategia']} --stop {melhor['stop']} --alvo {melhor['alvo']}"
              f" --limiar {melhor['limiar']}")
    else:
        print(f"\n  Nenhuma configuração sustentou acerto >= {args.meta:.0f}% com lucro fora da amostra.")
        print("  Isso é um resultado honesto, não um defeito: evita operar uma ilusão.")
        print("  Tente outros ativos, outro timeframe (--tf 1d) ou uma meta realista (--meta 55).")

    print("\n  Como ler a tabela:")
    print("  - AC.TREINO alto com AC.TESTE baixo = ajuste excessivo (overfitting). Desconfie.")
    print("  - RR baixo (alvo curto) aumenta o acerto, mas cada perda custa mais que cada ganho;")
    print("    por isso o fator de lucro (FL) precisa ficar acima de 1,0.")
    print("  - Acerto mínimo para empatar: RR 0,5 -> 67% | RR 0,7 -> 60% | RR 1 -> 50% | RR 2 -> 34%")
    print("    (sem contar taxas; na prática, precisa de um pouco mais).")


if __name__ == "__main__":
    main()
