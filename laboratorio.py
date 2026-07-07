"""Laboratório de estratégias: mede a assertividade real, sem se enganar.

Três camadas de validação, da mais simples à mais rigorosa:

1. TREINO x TESTE (70/30): mede o acerto no terço final que a otimização não viu.
2. INTERVALO DE CONFIANÇA: um acerto alto com poucas operações pode ser sorte.
   Só aprova se o limite INFERIOR do acerto (95% de confiança, Wilson) ainda
   superar o ponto de empate da relação risco/retorno.
3. WALK-FORWARD: simula o uso honesto — em cada janela, escolhe a melhor config
   usando SÓ o passado e mede o resultado na janela seguinte. É o teste mais
   próximo da realidade (padrão de quants para evitar overfitting).

O backtest já inclui taxa + slippage + funding, então os números aqui são
conservadores de propósito (backtests "limpos" enganam a favor).

Uso:
  python laboratorio.py BTCUSDT ETHUSDT SOLUSDT
  python laboratorio.py BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT --tf 4h --candles 3000 --meta 65
"""

import argparse
import math
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cripto import TIMEFRAME_CONTEXTO, backtest, dados
from cripto.estrategia import ESTRATEGIAS, calcular_scores
from cripto.fluxo import adicionar_fluxo
from cripto.indicadores import adicionar_indicadores
from cripto.priceaction import adicionar_priceaction

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
MINIMO_TRADES_IS = 20   # mínimo no "passado" para uma config ser elegível no walk-forward


def metricas(trades):
    """(nº, taxa de acerto %, fator de lucro) de uma lista de operações fechadas.

    Aceita objetos Operacao ou tuplas (data, lucro, resultado)."""
    def lucro(t): return t.lucro if hasattr(t, "lucro") else t[1]
    def res(t): return t.resultado if hasattr(t, "resultado") else t[2]
    fechadas = [t for t in trades if res(t) != "ABERTA"]
    total = len(fechadas)
    if total == 0:
        return 0, 0.0, 0.0
    vitorias = sum(1 for t in fechadas if lucro(t) > 0)
    ganhos = sum(lucro(t) for t in fechadas if lucro(t) > 0)
    perdas = abs(sum(lucro(t) for t in fechadas if lucro(t) < 0))
    fator = ganhos / perdas if perdas else float("inf")
    return total, 100 * vitorias / total, fator


def wilson_inferior(taxa_pct: float, n: int, z: float = 1.96) -> float:
    """Limite inferior do intervalo de confiança de 95% para a taxa de acerto (Wilson)."""
    if n == 0:
        return 0.0
    p = taxa_pct / 100
    centro = p + z * z / (2 * n)
    margem = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return 100 * (centro - margem) / (1 + z * z / n)


def break_even(stop: float, alvo: float) -> float:
    """Taxa de acerto mínima para empatar com essa relação risco/retorno (%)."""
    return 100 * stop / (stop + alvo)


def walk_forward(combos: list[dict], n_folds: int):
    """Walk-forward ancorado: em cada janela escolhe a melhor config olhando só o
    passado e mede na janela seguinte. Devolve os trades OOS agregados e o histórico."""
    todas_datas = sorted(t[0] for c in combos for t in c["trades"])
    if len(todas_datas) < 50:
        return None
    t0, t1 = todas_datas[0], todas_datas[-1]
    limites = [t0 + (t1 - t0) * k / (n_folds + 1) for k in range(n_folds + 2)]

    oos_total, historico = [], []
    for j in range(1, n_folds + 1):
        ini_oos, fim_oos = limites[j], limites[j + 1]
        # escolhe a melhor config usando SÓ dados anteriores ao início da janela OOS
        candidatas = []
        for c in combos:
            passado = [t for t in c["trades"] if t[0] < ini_oos]
            n_is, wr_is, fl_is = metricas(passado)
            if n_is >= MINIMO_TRADES_IS and fl_is > 1.0:
                candidatas.append((fl_is, wr_is, c))
        if not candidatas:
            continue
        _, _, escolhida = max(candidatas, key=lambda x: (x[0], x[1]))
        oos = [t for t in escolhida["trades"] if ini_oos <= t[0] < fim_oos]
        n_oos, wr_oos, fl_oos = metricas(oos)
        oos_total += oos
        historico.append({
            "janela": j, "ini": ini_oos, "fim": fim_oos,
            "estrategia": escolhida["estrategia"], "stop": escolhida["stop"],
            "alvo": escolhida["alvo"], "limiar": escolhida["limiar"],
            "n_oos": n_oos, "wr_oos": wr_oos, "fl_oos": fl_oos,
        })
    return {"oos": oos_total, "historico": historico}


def _stats_lucros(lucros: list[float]):
    """(nº, acerto %, fator de lucro) de uma lista de lucros."""
    n = len(lucros)
    if n == 0:
        return 0, 0.0, 0.0
    vit = sum(1 for x in lucros if x > 0)
    ganhos = sum(x for x in lucros if x > 0)
    perdas = abs(sum(x for x in lucros if x < 0))
    return n, 100 * vit / n, (ganhos / perdas if perdas else float("inf"))


def relatorio_regime(resultados: list[dict]):
    """Para a melhor config de cada estratégia, separa o desempenho por regime de
    mercado (ALTA/BAIXA/LATERAL). Revela edge CONDICIONAL que a média esconde."""
    melhores = {}
    for r in resultados:
        if r["n_teste"] < MINIMO_TRADES_TESTE:
            continue
        atual = melhores.get(r["estrategia"])
        if atual is None or r["fl_teste"] > atual["fl_teste"]:
            melhores[r["estrategia"]] = r
    linhas = []
    for estrategia, r in melhores.items():
        por_regime = {"ALTA": [], "BAIXA": [], "LATERAL": []}
        for (_, lucro, res, reg) in r["trades"]:
            if res != "ABERTA" and reg in por_regime:
                por_regime[reg].append(lucro)
        linhas.append((estrategia, r, por_regime))
    return linhas


def main():
    parser = argparse.ArgumentParser(description="Laboratório de estratégias")
    parser.add_argument("simbolos", nargs="+", help="Pares, ex: BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--tf", default="4h", choices=list(TIMEFRAME_CONTEXTO))
    parser.add_argument("--candles", type=int, default=3000)
    parser.add_argument("--meta", type=float, default=65.0, help="Taxa de acerto alvo (%%)")
    parser.add_argument("--folds", type=int, default=4, help="Janelas de walk-forward")
    parser.add_argument("--gestao", default="fixo",
                        choices=["fixo", "breakeven", "trailing", "parcial"],
                        help="Gestão da saída aplicada a toda a grade")
    parser.add_argument("--sem-venda", action="store_true", help="Apenas operações de compra")
    parser.add_argument("--regime", action="store_true",
                        help="Só entra A FAVOR do regime (COMPRA em ALTA, VENDA em BAIXA)")
    args = parser.parse_args()

    simbolos = [s.upper() for s in args.simbolos]
    tf_maior = TIMEFRAME_CONTEXTO[args.tf]

    # ----- carrega os dados uma única vez -----
    # o backtest exige > 240 candles (210 de aquecimento + 30); um ativo com menos
    # (ou que falhe ao baixar) é IGNORADO com aviso, em vez de derrubar a grade toda
    MINIMO_CANDLES = 241
    mercado = {}
    for simbolo in simbolos:
        print(f"Baixando {args.candles} candles de {simbolo} ({args.tf} + contexto {tf_maior})...")
        try:
            df = adicionar_priceaction(adicionar_indicadores(
                dados.buscar_candles(simbolo, args.tf, args.candles, apenas_fechados=True)))
            df = adicionar_fluxo(df, simbolo, args.tf)  # p/ estratégia fluxo (tolera falha)
            df_maior = adicionar_indicadores(
                dados.buscar_candles(simbolo, tf_maior, max(400, args.candles // 4),
                                     apenas_fechados=True))
        except (ConnectionError, ValueError) as erro:
            print(f"  AVISO: {simbolo} ignorado — {erro}")
            continue
        if len(df) < MINIMO_CANDLES:
            print(f"  AVISO: {simbolo} ignorado — histórico curto ({len(df)} candles em "
                  f"{args.tf}; mínimo {MINIMO_CANDLES}). Ativo listado há pouco tempo.")
            continue
        corte = df.index[int(len(df) * PROPORCAO_TREINO)]
        mercado[simbolo] = (df, df_maior, corte)

    if not mercado:
        print("\nNenhum ativo com histórico suficiente para o laboratório.")
        sys.exit(1)

    # ----- roda a grade de combinações -----
    print(f"\nTestando {len(ESTRATEGIAS)} estratégias x {len(SAIDAS)} saídas x "
          f"{len(LIMIARES)} limiares em {len(mercado)} ativos (com taxa+slippage+funding)...")
    resultados = []
    for nome_estrategia in ESTRATEGIAS:
        scores_cache = {
            simbolo: calcular_scores(df, df_maior, nome_estrategia)
            for simbolo, (df, df_maior, _) in mercado.items()
        }
        for stop, alvo in SAIDAS:
            for limiar in LIMIARES:
                ops_treino, ops_teste, todos, retornos = [], [], [], []
                for simbolo, (df, df_maior, corte) in mercado.items():
                    res = backtest.executar(
                        df, df_maior, simbolo=simbolo, timeframe=args.tf,
                        scores=scores_cache[simbolo], limiar=limiar,
                        atr_stop=stop, atr_alvo=alvo, gestao=args.gestao,
                        permitir_venda=not args.sem_venda,
                        filtro_regime=args.regime,
                    )
                    ops_treino += [op for op in res.operacoes if op.entrada_data < corte]
                    ops_teste += [op for op in res.operacoes if op.entrada_data >= corte]
                    todos += [(op.entrada_data, op.lucro, op.resultado, op.regime)
                              for op in res.operacoes]
                    retornos.append(res.retorno_total)
                n_treino, wr_treino, _ = metricas(ops_treino)
                n_teste, wr_teste, fl_teste = metricas(ops_teste)
                ci = wilson_inferior(wr_teste, n_teste)
                be = break_even(stop, alvo)
                resultados.append({
                    "estrategia": nome_estrategia, "stop": stop, "alvo": alvo,
                    "limiar": limiar, "n_treino": n_treino, "n_teste": n_teste,
                    "wr_treino": wr_treino, "wr_teste": wr_teste, "fl_teste": fl_teste,
                    "ci": ci, "break_even": be, "retorno_medio": sum(retornos) / len(retornos),
                    "trades": todos,
                    # aprovada = bate a meta, dá lucro E é estatisticamente confiável
                    # (o pior caso do acerto, com 95% de confiança, ainda empata ou ganha)
                    "aprovada": (wr_teste >= args.meta and fl_teste > 1.0
                                 and n_teste >= MINIMO_TRADES_TESTE and ci >= be),
                })

    confiaveis = [r for r in resultados if r["n_teste"] >= MINIMO_TRADES_TESTE]
    descartadas = len(resultados) - len(confiaveis)
    confiaveis.sort(key=lambda r: (r["aprovada"], r["wr_teste"]), reverse=True)

    # ----- relatório: tabela 70/30 com intervalo de confiança -----
    L = 104
    print()
    print("=" * L)
    print(f"  RESULTADO DO LABORATÓRIO  |  {', '.join(mercado)}  |  {args.tf}"
          + ("  |  SÓ A FAVOR DO REGIME" if args.regime else ""))
    print(f"  Aprova se: acerto >= {args.meta:.0f}% no teste, fator de lucro > 1 E o pior caso")
    print(f"  do acerto (IC 95%) ainda superar o ponto de empate da relação risco/retorno.")
    print("=" * L)
    cab = (f"  {'ESTRATÉGIA':<13}{'STOP':>5}{'ALVO':>5}{'RR':>6}{'LIM':>5}"
           f"{'N':>6}{'AC.TREINO':>10}{'AC.TESTE':>10}{'IC95↓':>8}{'EMPATE':>8}{'FL':>7}  OK")
    print(cab)
    print("-" * L)
    for r in confiaveis[:30]:
        rr = r["alvo"] / r["stop"]
        fl = "inf" if r["fl_teste"] == float("inf") else f"{r['fl_teste']:.2f}"
        marca = "  <<<" if r["aprovada"] else ""
        print(f"  {r['estrategia']:<13}{r['stop']:>5.1f}{r['alvo']:>5.1f}{rr:>6.2f}"
              f"{r['limiar']:>5}{r['n_treino'] + r['n_teste']:>6}"
              f"{r['wr_treino']:>9.1f}%{r['wr_teste']:>9.1f}%{r['ci']:>7.1f}%"
              f"{r['break_even']:>7.1f}%{fl:>7}{marca}")
    print("-" * L)
    if descartadas:
        print(f"  ({descartadas} combinações ocultadas por terem menos de "
              f"{MINIMO_TRADES_TESTE} operações no teste)")

    aprovadas = [r for r in confiaveis if r["aprovada"]]
    if aprovadas:
        melhor = aprovadas[0]
        print(f"\n  {len(aprovadas)} config(s) passaram nos 3 filtros (meta + lucro + confiança).")
        print("  A melhor:")
        print(f"    python analisar.py PAR --tf {args.tf} --estrategia {melhor['estrategia']}"
              f" --stop {melhor['stop']} --alvo {melhor['alvo']} --limiar {melhor['limiar']}")
    else:
        print(f"\n  Nenhuma config passou nos 3 filtros (meta {args.meta:.0f}% + lucro + confiança).")
        print("  Resultado honesto: acerto alto com poucos trades costuma ser sorte, não método.")

    # ----- walk-forward -----
    wf = walk_forward(resultados, args.folds)
    print()
    print("=" * L)
    print("  WALK-FORWARD (escolhe a melhor config olhando só o passado e mede no futuro)")
    print("=" * L)
    if not wf or not wf["historico"]:
        print("  Dados insuficientes para o walk-forward (poucas operações no histórico).")
    else:
        print(f"  {'JANELA':<8}{'PERÍODO OOS':<26}{'CONFIG ESCOLHIDA NO PASSADO':<34}"
              f"{'N':>5}{'ACERTO':>9}{'FL':>7}")
        print("-" * L)
        for h in wf["historico"]:
            fl = "inf" if h["fl_oos"] == float("inf") else f"{h['fl_oos']:.2f}"
            cfg = f"{h['estrategia']} {h['stop']:g}/{h['alvo']:g} lim{h['limiar']}"
            periodo = f"{h['ini']:%d/%m/%y}-{h['fim']:%d/%m/%y}"
            print(f"  #{h['janela']:<7}{periodo:<26}{cfg:<34}"
                  f"{h['n_oos']:>5}{h['wr_oos']:>8.1f}%{fl:>7}")
        print("-" * L)
        n_wf, wr_wf, fl_wf = metricas(wf["oos"])
        ci_wf = wilson_inferior(wr_wf, n_wf)
        fl_txt = "inf" if fl_wf == float("inf") else f"{fl_wf:.2f}"
        print(f"  AGREGADO fora da amostra: {n_wf} operações | acerto {wr_wf:.1f}% "
              f"(IC95↓ {ci_wf:.1f}%) | fator de lucro {fl_txt}")
        veredito = ("APROVADO" if wr_wf >= args.meta and fl_wf > 1.0 and n_wf >= MINIMO_TRADES_TESTE
                    else "REPROVADO")
        print(f"  Veredito walk-forward: {veredito} "
              f"(é o número mais próximo do que você viveria operando de verdade)")

    # ----- desempenho por regime de mercado -----
    print()
    print("=" * L)
    print("  DESEMPENHO POR REGIME DE MERCADO (melhor config de cada estratégia)")
    print("  Revela edge condicional: uma estratégia pode só funcionar em ALTA, ou só em BAIXA.")
    print("=" * L)
    print(f"  {'ESTRATÉGIA':<15}{'CONFIG':<16}"
          f"{'ALTA (n/acerto)':>17}{'BAIXA (n/acerto)':>18}{'LATERAL (n/acerto)':>20}")
    print("-" * L)
    for estrategia, r, por_regime in relatorio_regime(resultados):
        cfg = f"{r['stop']:g}/{r['alvo']:g} lim{r['limiar']}"
        celulas = []
        for reg in ("ALTA", "BAIXA", "LATERAL"):
            n, wr, _ = _stats_lucros(por_regime[reg])
            celulas.append(f"{n}/{wr:.0f}%" if n else "—")
        print(f"  {estrategia:<15}{cfg:<16}{celulas[0]:>17}{celulas[1]:>18}{celulas[2]:>20}")
    print("-" * L)
    print("  (acerto por regime usa todas as operações da config, não só o teste — mais amostra)")

    print("\n  Como ler:")
    print("  - IC95↓ = pior caso do acerto com 95% de confiança. Se < EMPATE, pode ser só sorte.")
    print("  - Walk-forward é o teste mais duro: se reprova aqui, desconfie do resto.")
    print("  - Regime: se uma estratégia acerta muito mais em um regime, opere SÓ nele.")
    print("  - Empate por RR: 0,5->67% | 0,7->60% | 1->50% | 2->34% (já com custos, exija folga).")


if __name__ == "__main__":
    main()
