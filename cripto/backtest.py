"""Backtest da estratégia: simula as operações em dados históricos reais.

Regras da simulação (conservadoras, para não inflar o resultado):
- Entrada na ABERTURA do candle seguinte ao sinal (nada de olhar o futuro).
- Stop a 1,5x ATR e alvo a 3,0x ATR (risco/retorno 2:1).
- Se stop e alvo são atingidos no mesmo candle, assume-se o STOP (pior caso).
- Taxa de 0,1% por lado (padrão Binance spot) descontada em cada operação.
- Risco fixo por operação (% do capital), como manda a gestão de risco.
"""

from dataclasses import dataclass, field

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
    permitir_venda: bool = True,
) -> ResultadoBacktest:
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

    abertura = df["abertura"].to_numpy()
    maxima = df["maxima"].to_numpy()
    minima = df["minima"].to_numpy()
    atr_arr = df["atr"].to_numpy()
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
            # verifica saída no candle atual (stop tem prioridade — pior caso)
            if posicao.direcao == "COMPRA":
                bateu_stop = minima[i] <= posicao.stop
                bateu_alvo = maxima[i] >= posicao.alvo
            else:
                bateu_stop = maxima[i] >= posicao.stop
                bateu_alvo = minima[i] <= posicao.alvo

            candles_aberta += 1
            if bateu_stop or bateu_alvo:
                # stop = ordem a mercado (sofre slippage contra você);
                # alvo = ordem limitada (preenche no preço, sem slippage)
                if bateu_stop:
                    preco_saida = (posicao.stop * (1 - slippage) if posicao.direcao == "COMPRA"
                                   else posicao.stop * (1 + slippage))
                else:
                    preco_saida = posicao.alvo
                if posicao.direcao == "COMPRA":
                    bruto = quantidade * (preco_saida - posicao.entrada)
                else:
                    bruto = quantidade * (posicao.entrada - preco_saida)
                funding = quantidade * posicao.entrada * funding_por_candle * candles_aberta
                custos = taxa * quantidade * (posicao.entrada + preco_saida) + funding
                posicao.lucro = bruto - custos
                posicao.saida = preco_saida
                posicao.saida_data = datas[i]
                posicao.resultado = "STOP" if bateu_stop else "ALVO"
                capital += posicao.lucro
                resultado.operacoes.append(posicao)
                posicao = None
                candles_aberta = 0
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
        posicao = Operacao(direcao=direcao, entrada_data=datas[i], entrada=entrada, stop=stop, alvo=alvo)
        candles_aberta = 0

    # posição ainda aberta no fim: fecha a mercado pelo último fechamento (com slippage)
    if posicao is not None:
        ref = float(df["fechamento"].iloc[-1])
        ultimo = ref * (1 - slippage) if posicao.direcao == "COMPRA" else ref * (1 + slippage)
        if posicao.direcao == "COMPRA":
            bruto = quantidade * (ultimo - posicao.entrada)
        else:
            bruto = quantidade * (posicao.entrada - ultimo)
        funding = quantidade * posicao.entrada * funding_por_candle * candles_aberta
        posicao.lucro = bruto - taxa * quantidade * (posicao.entrada + ultimo) - funding
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
