"""Backtest da estratégia: simula as operações em dados históricos reais.

Regras da simulação (conservadoras, para não inflar o resultado):
- Entrada na ABERTURA do candle seguinte ao sinal (nada de olhar o futuro).
- Stop a 1,5x ATR e alvo a 3,0x ATR (risco/retorno 2:1).
- Se stop e alvo são atingidos no mesmo candle, assume-se o STOP (pior caso).
- Taxa de 0,1% por lado (padrão Binance spot) descontada em cada operação.
- Risco fixo por operação (% do capital), como manda a gestão de risco.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .estrategia import LIMIAR_FORTE, calcular_scores


@dataclass
class Operacao:
    direcao: str
    entrada_data: pd.Timestamp
    entrada: float
    stop: float
    alvo: float
    saida_data: pd.Timestamp | None = None
    saida: float | None = None
    resultado: str = ""
    lucro: float = 0.0
    regime: str = ""  # regime de mercado no momento da entrada (ALTA/BAIXA/LATERAL)


@dataclass
class ResultadoBacktest:
    simbolo: str
    timeframe: str
    periodo_inicio: pd.Timestamp
    periodo_fim: pd.Timestamp
    capital_inicial: float
    capital_final: float
    operacoes: list[Operacao] = field(default_factory=list)
    curva_capital: list[float] = field(default_factory=list)
    retorno_comprar_segurar: float = 0.0

    @property
    def total(self) -> int:
        return len(self.operacoes)

    @property
    def vitorias(self) -> int:
        return sum(1 for op in self.operacoes if op.lucro > 0)

    @property
    def taxa_acerto(self) -> float:
        return 100 * self.vitorias / self.total if self.total else 0.0

    @property
    def retorno_total(self) -> float:
        return 100 * (self.capital_final / self.capital_inicial - 1)

    @property
    def fator_lucro(self) -> float:
        ganhos = sum(op.lucro for op in self.operacoes if op.lucro > 0)
        perdas = abs(sum(op.lucro for op in self.operacoes if op.lucro < 0))
        return ganhos / perdas if perdas else float("inf")

    @property
    def drawdown_maximo(self) -> float:
        pico, maior_queda = float("-inf"), 0.0
        for valor in self.curva_capital:
            pico = max(pico, valor)
            maior_queda = max(maior_queda, (pico - valor) / pico)
        return 100 * maior_queda

    @property
    def expectativa(self) -> float:
        """Lucro médio por operação, em % do capital arriscado no período."""
        if not self.total:
            return 0.0
        return sum(op.lucro for op in self.operacoes) / self.total


def executar(
    df: pd.DataFrame,
    df_maior: pd.DataFrame,
    simbolo: str,
    timeframe: str,
    estrategia: str = "confluencia",
    scores: pd.DataFrame | None = None,
    limiar: int = LIMIAR_FORTE,
    capital_inicial: float = 1000.0,
    risco_por_operacao: float = 0.01,
    taxa: float = 0.001,
    slippage: float = 0.0005,
    funding_8h: float = 0.0001,
    atr_stop: float = 1.5,
    atr_alvo: float = 3.0,
    gestao: str = "fixo",
    permitir_venda: bool = True,
) -> ResultadoBacktest:
    # gestao da saída:
    #   "fixo"      stop e alvo fixos (comportamento original)
    #   "breakeven" ao atingir +1R a favor, move o stop para o preço de entrada (zero a zero)
    #   "trailing"  além do breakeven, persegue o stop a atr_stop x ATR do melhor preço
    #   "parcial"   realiza metade em +1R, move o resto para breakeven e deixa correr até o alvo
    if gestao not in ("fixo", "breakeven", "trailing", "parcial"):
        raise ValueError(f"gestão de saída inválida: {gestao}")
    # slippage: escorregamento de preço em ordens a mercado (entrada e stop) — sempre
    #   contra você, como na vida real. Alvo é ordem limitada, sem slippage.
    # funding_8h: custo de funding de perpétuos a cada 8h (perpétuos cobram funding sobre
    #   o nocional; modelado como custo constante para não superestimar o retorno).
    if scores is None:
        scores = calcular_scores(df, df_maior, estrategia)

    # nº de períodos de 8h em cada candle, para cobrar funding proporcional ao tempo
    _horas_candle = {"15m": 0.25, "1h": 1, "4h": 4, "1d": 24, "1w": 168}.get(timeframe, 4)
    funding_por_candle = funding_8h * _horas_candle / 8

    capital = capital_inicial
    resultado = ResultadoBacktest(
        simbolo=simbolo,
        timeframe=timeframe,
        periodo_inicio=df.index[0],
        periodo_fim=df.index[-1],
        capital_inicial=capital_inicial,
        capital_final=capital_inicial,
    )
    posicao: Operacao | None = None
    quantidade = 0.0
    candles_aberta = 0  # quantos candles a posição atual está aberta (para o funding)
    # estado dinâmico da posição aberta (usado pela gestão de saída)
    stop_atual = melhor = risco0 = atr0 = alvo_parcial = 0.0
    qtd_rest = gross_acum = fees_acum = funding_acum = 0.0
    be_ativo = parcial_feita = False
    FRAC_PARCIAL = 0.5  # quanto da posição é realizado no primeiro alvo (modo "parcial")

    abertura = df["abertura"].to_numpy()
    maxima = df["maxima"].to_numpy()
    minima = df["minima"].to_numpy()
    atr_arr = df["atr"].to_numpy()
    regime_arr = (df["regime"].to_numpy() if "regime" in df.columns
                  else np.array(["?"] * len(df)))
    score_compra = scores["score_compra"].to_numpy()
    score_venda = scores["score_venda"].to_numpy()
    datas = df.index

    # ignora o aquecimento dos indicadores (EMA200 precisa de ~200 candles)
    inicio = 210
    if len(df) <= inicio + 30:
        raise ValueError(
            f"{simbolo}: histórico insuficiente para backtest "
            f"({len(df)} candles em {timeframe}; mínimo {inicio + 30}). "
            "Ativo listado há pouco tempo — sem como validar a estratégia nele."
        )

    for i in range(inicio, len(df)):
        if posicao is not None:
            longa = posicao.direcao == "COMPRA"
            entrada_p = posicao.entrada
            candles_aberta += 1
            funding_acum += qtd_rest * entrada_p * funding_por_candle  # funding do candle
            hi, lo = maxima[i], minima[i]

            saida = None  # (preco_saida, tag) quando a posição (resto) for encerrada
            # 1) STOP / trailing (ordem a mercado, sofre slippage; prioridade = pior caso)
            if (lo <= stop_atual) if longa else (hi >= stop_atual):
                preco_saida = stop_atual * (1 - slippage) if longa else stop_atual * (1 + slippage)
                lucro_travado = stop_atual > entrada_p if longa else stop_atual < entrada_p
                saida = (preco_saida, "TRAIL" if lucro_travado else "STOP")
            # 2) PARCIAL (ordem limitada, sem slippage): realiza fração no primeiro alvo (+1R)
            if saida is None and gestao == "parcial" and not parcial_feita and (
                    (hi >= alvo_parcial) if longa else (lo <= alvo_parcial)):
                qtd_p = qtd_rest * FRAC_PARCIAL
                gross_acum += (qtd_p * (alvo_parcial - entrada_p) if longa
                               else qtd_p * (entrada_p - alvo_parcial))
                fees_acum += taxa * qtd_p * alvo_parcial
                qtd_rest -= qtd_p
                parcial_feita = True
                stop_atual = max(stop_atual, entrada_p) if longa else min(stop_atual, entrada_p)
            # 3) ALVO cheio (ordem limitada, sem slippage)
            if saida is None and ((hi >= posicao.alvo) if longa else (lo <= posicao.alvo)):
                saida = (posicao.alvo, "ALVO")

            if saida is not None:
                preco_saida, tag = saida
                gross_rest = (qtd_rest * (preco_saida - entrada_p) if longa
                              else qtd_rest * (entrada_p - preco_saida))
                fees_acum += taxa * qtd_rest * preco_saida
                posicao.lucro = gross_acum + gross_rest - fees_acum - funding_acum
                posicao.saida = preco_saida
                posicao.saida_data = datas[i]
                posicao.resultado = tag
                capital += posicao.lucro
                resultado.operacoes.append(posicao)
                posicao = None
                candles_aberta = 0
                resultado.curva_capital.append(capital)
                continue

            # 4) atualiza melhor preço, breakeven e trailing para o PRÓXIMO candle
            melhor = max(melhor, hi) if longa else min(melhor, lo)
            if gestao in ("breakeven", "trailing", "parcial") and not be_ativo and (
                    (melhor >= entrada_p + risco0) if longa else (melhor <= entrada_p - risco0)):
                stop_atual = max(stop_atual, entrada_p) if longa else min(stop_atual, entrada_p)
                be_ativo = True
            if gestao == "trailing" and be_ativo:
                novo = melhor - atr_stop * atr0 if longa else melhor + atr_stop * atr0
                stop_atual = max(stop_atual, novo) if longa else min(stop_atual, novo)
            resultado.curva_capital.append(capital)
            continue

        resultado.curva_capital.append(capital)

        # sinal no candle anterior já fechado -> entra na abertura do candle atual
        if i == 0 or capital <= 0:
            continue
        sinal_compra = score_compra[i - 1] >= limiar
        sinal_venda = permitir_venda and score_venda[i - 1] >= limiar
        if not (sinal_compra or sinal_venda):
            continue

        direcao = "COMPRA" if score_compra[i - 1] >= score_venda[i - 1] else "VENDA"
        preco_ref = abertura[i]
        atr_sinal = atr_arr[i - 1]
        if atr_sinal <= 0 or preco_ref <= 0:
            continue
        # entrada a mercado: paga slippage (compra um pouco acima / vende um pouco abaixo)
        entrada = preco_ref * (1 + slippage) if direcao == "COMPRA" else preco_ref * (1 - slippage)
        if direcao == "COMPRA":
            stop = entrada - atr_stop * atr_sinal
            alvo = entrada + atr_alvo * atr_sinal
        else:
            stop = entrada + atr_stop * atr_sinal
            alvo = entrada - atr_alvo * atr_sinal

        risco_unitario = abs(entrada - stop)
        quantidade = (capital * risco_por_operacao) / risco_unitario
        posicao = Operacao(direcao=direcao, entrada_data=datas[i], entrada=entrada,
                           stop=stop, alvo=alvo, regime=str(regime_arr[i - 1]))
        # inicializa o estado dinâmico da nova posição
        stop_atual, risco0, atr0, melhor = stop, risco_unitario, atr_sinal, entrada
        qtd_rest, gross_acum = quantidade, 0.0
        fees_acum = taxa * quantidade * entrada  # taxa de entrada (nocional cheio)
        funding_acum = 0.0
        be_ativo = parcial_feita = False
        alvo_parcial = entrada + risco0 if direcao == "COMPRA" else entrada - risco0
        candles_aberta = 0

    # posição ainda aberta no fim: fecha a mercado pelo último fechamento (com slippage)
    if posicao is not None:
        longa = posicao.direcao == "COMPRA"
        ref = float(df["fechamento"].iloc[-1])
        ultimo = ref * (1 - slippage) if longa else ref * (1 + slippage)
        gross_rest = (qtd_rest * (ultimo - posicao.entrada) if longa
                      else qtd_rest * (posicao.entrada - ultimo))
        fees_acum += taxa * qtd_rest * ultimo
        posicao.lucro = gross_acum + gross_rest - fees_acum - funding_acum
        posicao.saida = ultimo
        posicao.saida_data = datas[-1]
        posicao.resultado = "ABERTA"
        capital += posicao.lucro
        resultado.operacoes.append(posicao)

    resultado.capital_final = capital
    preco_ini = float(df["fechamento"].iloc[inicio])
    preco_fim = float(df["fechamento"].iloc[-1])
    resultado.retorno_comprar_segurar = 100 * (preco_fim / preco_ini - 1)
    return resultado
