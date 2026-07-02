"""Analisador de criptomoedas — análise técnica por confluência de indicadores.

Uso:
  python analisar.py BTCUSDT                 Análise completa de um ativo
  python analisar.py --scan                  Escaneia os pares com maior volume e ranqueia oportunidades
  python analisar.py BTCUSDT --backtest      Testa a estratégia no histórico do ativo

Opções:
  --tf 15m|1h|4h|1d     Timeframe operado (padrão: 4h; o contexto usa o timeframe acima)
  --estrategia NOME     confluencia | tendencia_pa | reversao | rompimento
  --candles N           Quantidade de candles no backtest (padrão: 1500)
  --capital VALOR       Capital inicial do backtest (padrão: 1000)
  --risco PCT           Risco por operação em % (padrão: 1)
  --limiar N            Score mínimo para sinal (padrão: 70)
  --stop N / --alvo N   Stop e alvo em múltiplos de ATR (padrão: 1.5 / 3.0)
  --top N               Quantos pares escanear no --scan (padrão: 50)
  --sem-venda           Backtest apenas com operações de compra (long only)

Para descobrir qual combinação tem a melhor assertividade histórica:
  python laboratorio.py BTCUSDT ETHUSDT SOLUSDT
"""

import argparse
import sys
import time

# Garante acentuação correta no console do Windows, mesmo com saída redirecionada
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cripto import backtest, dados, estrategia
from cripto.indicadores import adicionar_indicadores
from cripto.priceaction import adicionar_priceaction

TIMEFRAME_CONTEXTO = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
LARGURA = 64


def linha(caractere="-"):
    print(caractere * LARGURA)


def carregar(simbolo: str, tf: str, candles: int = 500):
    tf_maior = TIMEFRAME_CONTEXTO[tf]
    df = adicionar_priceaction(adicionar_indicadores(dados.buscar_candles(simbolo, tf, candles)))
    df_maior = adicionar_indicadores(dados.buscar_candles(simbolo, tf_maior, max(400, candles // 4)))
    return df, df_maior, tf_maior


def comando_analise(args):
    simbolo = args.simbolo.upper()
    df, df_maior, tf_maior = carregar(simbolo, args.tf)
    diag = estrategia.avaliar(df, df_maior, args.estrategia, args.stop, args.alvo)

    linha("=")
    print(f"  {simbolo}  |  timeframe {args.tf} (contexto {tf_maior})")
    print(f"  Estratégia: {diag['estrategia']}")
    print(f"  Candle analisado: {diag['data_candle']:%d/%m/%Y %H:%M} UTC")
    linha("=")
    print(f"  Preço atual:      {diag['preco']:,.6g} USDT")
    print(f"  RSI(14):          {diag['rsi']:.1f}")
    print(f"  ADX(14):          {diag['adx']:.1f}")
    print(f"  ATR(14):          {diag['atr']:,.6g}")
    linha()
    print(f"  Score COMPRA: {diag['score_compra']:3d}/100    Score VENDA: {diag['score_venda']:3d}/100")
    print(f"  Direção dominante: {diag['direcao']}  ({diag['forca']})")
    linha()
    print("  Critérios da direção dominante:")
    for criterio in diag["criterios"]:
        marca = "[X]" if criterio["atendido"] else "[ ]"
        print(f"   {marca} (+{criterio['pontos']:>2}) {criterio['descricao']}")
    linha()

    if diag["score"] >= args.limiar:
        print(f"  >> SINAL FORTE de {diag['direcao']} <<")
        print(f"     Entrada sugerida: {diag['preco']:,.6g}")
        print(f"     Stop loss:        {diag['stop']:,.6g}  ({args.stop:g}x ATR)")
        print(f"     Alvo:             {diag['alvo']:,.6g}  (risco/retorno {args.alvo / args.stop:.2g}:1)")
        risco = abs(diag["preco"] - diag["stop"]) / diag["preco"] * 100
        print(f"     Risco até o stop: {risco:.2f}% do preço")
    elif diag["score"] >= estrategia.LIMIAR_MODERADO:
        print(f"  Sinal MODERADO de {diag['direcao']} — confluência incompleta.")
        print("  Aguarde mais critérios ou reduza o tamanho da posição.")
    else:
        print("  SEM SINAL no momento — confluência insuficiente.")
        print("  A melhor operação muitas vezes é não operar.")
    linha("=")


def comando_scan(args):
    print(f"Buscando os {args.top} pares USDT com maior volume em 24h...")
    pares = dados.melhores_pares_usdt(args.top)
    resultados = []
    for n, par in enumerate(pares, 1):
        print(f"\r  Analisando {n}/{len(pares)}: {par:<14}", end="", flush=True)
        try:
            df, df_maior, _ = carregar(par, args.tf, 400)
            diag = estrategia.avaliar(df, df_maior, args.estrategia, args.stop, args.alvo)
            resultados.append((par, diag))
            time.sleep(0.1)  # respeita o limite de requisições da Binance
        except Exception:
            continue
    print("\r" + " " * 40 + "\r", end="")

    resultados.sort(key=lambda item: item[1]["score"], reverse=True)
    com_sinal = [r for r in resultados if r[1]["score"] >= estrategia.LIMIAR_MODERADO]

    linha("=")
    print(f"  MELHORES OPORTUNIDADES  |  timeframe {args.tf}")
    linha("=")
    if not com_sinal:
        print("  Nenhum par atingiu confluência suficiente agora.")
        print("  Mercado sem oportunidades claras — não force operação.")
        mostrar = resultados[:5]
        if mostrar:
            print("\n  Maiores scores no momento (abaixo do mínimo):")
    else:
        mostrar = com_sinal[:15]
        print(f"  {'PAR':<14}{'DIREÇÃO':<10}{'SCORE':>6}  {'FORÇA':<10}{'PREÇO':>14}")
        linha()
    for par, diag in mostrar:
        print(f"  {par:<14}{diag['direcao']:<10}{diag['score']:>6}  "
              f"{diag['forca']:<10}{diag['preco']:>14,.6g}")
    linha("=")
    print("  Antes de operar, rode a análise completa e o backtest do par:")
    print(f"    python analisar.py PAR --tf {args.tf}")
    print(f"    python analisar.py PAR --tf {args.tf} --backtest")


def comando_backtest(args):
    simbolo = args.simbolo.upper()
    print(f"Baixando {args.candles} candles de {simbolo} ({args.tf})...")
    df, df_maior, tf_maior = carregar(simbolo, args.tf, args.candles)
    res = backtest.executar(
        df, df_maior,
        simbolo=simbolo,
        timeframe=args.tf,
        estrategia=args.estrategia,
        limiar=args.limiar,
        capital_inicial=args.capital,
        risco_por_operacao=args.risco / 100,
        atr_stop=args.stop,
        atr_alvo=args.alvo,
        slippage=args.slippage / 100,
        funding_8h=args.funding / 100,
        gestao=args.gestao,
        permitir_venda=not args.sem_venda,
    )

    linha("=")
    print(f"  BACKTEST  {simbolo}  |  {args.tf} (contexto {tf_maior})")
    print(f"  Estratégia: {args.estrategia}  |  Stop {args.stop:g}x ATR  |  Alvo {args.alvo:g}x ATR  |  Saída: {args.gestao}")
    print(f"  Período: {res.periodo_inicio:%d/%m/%Y} a {res.periodo_fim:%d/%m/%Y}")
    print(f"  Score mínimo: {args.limiar}  |  Risco por operação: {args.risco:.1f}%")
    print(f"  Custos: taxa 0,1% + slippage {args.slippage:g}%/lado + funding {args.funding:g}%/8h")
    linha("=")
    if res.total == 0:
        print("  Nenhuma operação gerada no período (estratégia seletiva).")
        print("  Tente mais candles (--candles 3000) ou outro timeframe.")
        linha("=")
        return

    print(f"  Operações:          {res.total}  "
          f"({sum(1 for o in res.operacoes if o.direcao == 'COMPRA')} compras, "
          f"{sum(1 for o in res.operacoes if o.direcao == 'VENDA')} vendas)")
    print(f"  Taxa de acerto:     {res.taxa_acerto:.1f}%  ({res.vitorias} vitórias)")
    fator = res.fator_lucro
    print(f"  Fator de lucro:     {'inf' if fator == float('inf') else f'{fator:.2f}'}"
          f"  (>1,0 = lucrativo; >1,5 = bom)")
    print(f"  Expectativa/trade:  {res.expectativa:+,.2f} USDT")
    linha()
    print(f"  Capital inicial:    {res.capital_inicial:,.2f} USDT")
    print(f"  Capital final:      {res.capital_final:,.2f} USDT")
    print(f"  Retorno:            {res.retorno_total:+.2f}%")
    print(f"  Comprar e segurar:  {res.retorno_comprar_segurar:+.2f}%  (comparação)")
    print(f"  Drawdown máximo:    {res.drawdown_maximo:.2f}%")
    linha()
    print("  Últimas 10 operações:")
    print(f"  {'DATA':<12}{'DIR':<8}{'ENTRADA':>12}{'SAÍDA':>12}{'RESULT':>8}{'LUCRO':>12}")
    for op in res.operacoes[-10:]:
        print(f"  {op.entrada_data:%d/%m/%y}{'':<4}{op.direcao:<8}"
              f"{op.entrada:>12,.6g}{op.saida:>12,.6g}{op.resultado:>8}{op.lucro:>+12,.2f}")
    linha("=")
    print("  Lembre-se: resultado passado não garante resultado futuro.")
    print("  Use o backtest para COMPARAR configurações, não como promessa de lucro.")


def main():
    parser = argparse.ArgumentParser(
        description="Analisador de criptomoedas por confluência de indicadores",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("simbolo", nargs="?", help="Par a analisar, ex: BTCUSDT")
    parser.add_argument("--scan", action="store_true", help="Escanear o mercado")
    parser.add_argument("--backtest", action="store_true", help="Backtest da estratégia")
    parser.add_argument("--tf", default="4h", choices=list(TIMEFRAME_CONTEXTO))
    parser.add_argument("--estrategia", default="confluencia",
                        choices=list(estrategia.ESTRATEGIAS),
                        help="Estratégia a usar (veja o laboratorio.py para comparar)")
    parser.add_argument("--candles", type=int, default=1500)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--risco", type=float, default=1.0)
    parser.add_argument("--limiar", type=int, default=estrategia.LIMIAR_FORTE)
    parser.add_argument("--stop", type=float, default=1.5, help="Stop em múltiplos de ATR")
    parser.add_argument("--alvo", type=float, default=3.0, help="Alvo em múltiplos de ATR")
    parser.add_argument("--slippage", type=float, default=0.05, help="Slippage por lado, em %% (padrão 0,05)")
    parser.add_argument("--funding", type=float, default=0.01, help="Funding a cada 8h, em %% (padrão 0,01)")
    parser.add_argument("--gestao", default="fixo", choices=["fixo", "breakeven", "trailing", "parcial"],
                        help="Gestão da saída: stop fixo, breakeven, trailing ou saída parcial")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--sem-venda", action="store_true")
    args = parser.parse_args()

    try:
        if args.scan:
            comando_scan(args)
        elif args.simbolo and args.backtest:
            comando_backtest(args)
        elif args.simbolo:
            comando_analise(args)
        else:
            parser.print_help()
            sys.exit(1)
    except (ConnectionError, ValueError) as erro:
        print(f"\nErro: {erro}")
        sys.exit(1)


if __name__ == "__main__":
    main()
