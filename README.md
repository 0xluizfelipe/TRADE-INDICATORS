# Analisador Cripto — análise técnica por confluência de indicadores

Sistema que analisa gráficos de criptomoedas usando os indicadores mais consagrados
do mercado **e price action** (padrões de candle, estrutura de mercado, suporte e
resistência) e aponta oportunidades de compra e venda **apenas quando vários sinais
independentes concordam entre si** (confluência) — a abordagem defendida por traders
profissionais e pela literatura de análise técnica.

Inclui um **laboratório de estratégias** que mede a assertividade histórica real de
cada combinação estratégia/stop/alvo, validando em dados fora da amostra.

## Instalação (uma vez só)

```
python -m pip install -r requirements.txt
```

Não precisa de conta nem chave de API — os dados vêm da API pública da Binance.

## Como usar

### 1. Escanear o mercado em busca de oportunidades

```
python analisar.py --scan
```

Analisa os 50 pares USDT com maior volume e ranqueia os que têm sinal de
compra ou venda no momento. Opções: `--top 100` (mais pares), `--tf 1d` (outro timeframe).

### 2. Analisar um ativo específico

```
python analisar.py BTCUSDT
python analisar.py ETHUSDT --tf 1h
```

Mostra o score de compra e de venda (0 a 100), quais critérios foram atendidos e,
se houver sinal forte, a sugestão de entrada, stop loss e alvo.

### 3. Testar a estratégia no histórico (backtest) — **faça isso antes de operar**

```
python analisar.py BTCUSDT --backtest
python analisar.py BTCUSDT --backtest --candles 3000 --tf 1d --sem-venda
```

Simula as operações que a estratégia teria feito no passado e mostra taxa de acerto,
fator de lucro, retorno, drawdown e comparação com simplesmente comprar e segurar.

| Opção | Significado | Padrão |
|---|---|---|
| `--tf` | Timeframe operado (15m, 1h, 4h, 1d) | 4h |
| `--estrategia` | confluencia, tendencia_pa, reversao ou rompimento | confluencia |
| `--candles` | Candles no backtest | 1500 |
| `--capital` | Capital inicial simulado | 1000 |
| `--risco` | Risco por operação (% do capital) | 1 |
| `--limiar` | Score mínimo para entrar | 70 |
| `--stop` / `--alvo` | Stop e alvo em múltiplos de ATR | 1.5 / 3.0 |
| `--sem-venda` | Só operações de compra (long only) | desligado |

### 4. Laboratório: descobrir qual estratégia tem a melhor assertividade histórica

```
python laboratorio.py BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT --tf 4h --meta 65
```

Testa **todas as combinações** de estratégia x stop/alvo x limiar nos ativos
informados e mede a taxa de acerto separando treino (primeiros 70% dos dados) e
**teste (últimos 30%, que a otimização não viu)**. Só aprova configurações que
batem a meta de acerto no teste E têm fator de lucro acima de 1 — taxa de acerto
alta que perde dinheiro não serve. Essa validação fora da amostra é o que separa
uma estratégia real de uma ilusão estatística (overfitting).

### 5. Simulador: treinar com USDT fictício em preços reais (paper trading)

```
python simulador.py
```

Abre uma interface no navegador (http://127.0.0.1:8765) com:

- **Gráfico de candles em tempo real** de qualquer par da Binance (15m, 1h, 4h, 1d)
- **Compra e venda** com USDT fictício (carteira começa com 10.000)
- **Alavancagem de 1x a 25x** com preço de liquidação calculado e exibido no gráfico
- **Stop loss e alvo** opcionais — o botão "Sugerir" preenche com a configuração
  aprovada no laboratório (stop 2×ATR, alvo 1×ATR)
- **Score das 4 estratégias** direto na tela, com os critérios atendidos
- **Botão "Varrer top 25"**: analisa as 25 maiores criptos em paralelo (~5 s) com a
  estratégia selecionada e ranqueia os melhores gráficos — clicar num resultado abre
  o gráfico do par com a direção e a análise já carregadas
- **Botão "Varrer (todas estratégias)"**: roda as 4 estratégias em cada um dos 25 pares
  e ordena por CONSENSO — quantas estratégias apontam a mesma direção. 3/4 ou 4/4
  concordando (badge dourado) é o sinal mais confiável, pois é confluência entre
  métodos independentes, não um pico isolado de uma estratégia só
- Execução automática de stop/alvo/liquidação, **inclusive retroativa**: se você
  fechar o simulador, ao reabrir ele verifica os candles do período e executa as
  saídas no preço certo (pior caso primeiro, como no backtest)
- Taxa de 0,05% por lado (taker de futuros) e histórico com taxa de acerto

A carteira fica salva em `carteira.json`. O botão "Reiniciar carteira" zera tudo.
Use o simulador por algumas semanas antes de pensar em dinheiro real: ele valida
não só a estratégia, mas a SUA disciplina em segui-la.

## As estratégias

São quatro, todas combinando indicadores e price action por pontuação (0 a 100):

| Estratégia | Lógica | Principais sinais |
|---|---|---|
| `confluencia` | Indicadores clássicos a favor da tendência maior | EMA, RSI, MACD, Bollinger, ADX, volume |
| `tendencia_pa` | Pullback na média com padrão de candle | Estrutura de topos/fundos, EMA21/50, engolfo/martelo |
| `reversao` | Reversão à média em extremos | RSI < 30 / > 70, Bollinger, suporte/resistência, padrão de candle |
| `rompimento` | Rompimento de 20 candles com força | Máx/mín de 20, volume 1,5x, ADX > 25 |

O price action (`cripto/priceaction.py`) detecta: martelo, estrela cadente,
engolfo de alta/baixa, estrutura de mercado (topos e fundos ascendentes ou
descendentes via fractais confirmados), suporte/resistência pelos últimos swings
e rompimentos — tudo sem olhar o futuro, como um trader veria no gráfico.

## A pontuação da estratégia `confluencia` (padrão)

Só há sinal quando o timeframe maior confirma a tendência E o timeframe operado
dá o gatilho de entrada. Cada critério soma pontos (as outras estratégias seguem
a mesma lógica de pontuação — veja os critérios em `cripto/estrategia.py`):

| Critério | Pontos | Indicador |
|---|---|---|
| Tendência do timeframe maior | 15 | Preço vs EMA200 |
| Cruzamento de médias vigente no maior | 10 | EMA50 vs EMA200 |
| Momento do timeframe maior a favor | 5 | Histograma MACD |
| Tendência local | 10 | Preço vs EMA200 |
| Gatilho de momento | 15 | RSI 14 (saída de extremo ou pullback) |
| Reversão de momento recente | 15 | Cruzamento MACD (12/26/9) |
| Extremo de volatilidade | 10 | Bandas de Bollinger (20, 2) |
| Tendência com força (não lateral) | 10 | ADX 14 > 20 |
| Confirmação de volume | 10 | Volume > 1,2x média 20 |

- **Score ≥ 70**: sinal forte. **55–69**: moderado. **< 55**: fique de fora.
- **Stop loss**: 1,5x ATR(14) — se adapta à volatilidade do ativo.
- **Alvo**: 3x ATR — risco/retorno de 2:1 (com 2:1, basta acertar ~38% das vezes
  para ficar no lucro, já descontando as taxas).
- **Risco por operação**: 1% do capital (regra de ouro da gestão de risco — uma
  sequência de 10 perdas custa menos de 10% do capital).

## Como interpretar o backtest

- **Fator de lucro > 1,0** = estratégia lucrativa no período; **> 1,5** = bom.
- **Taxa de acerto** sozinha não diz nada: com risco/retorno 2:1, 40% de acerto já é lucro.
- **Drawdown máximo** = pior queda do capital no caminho. Pergunte-se: eu aguentaria?
- Compare sempre com **comprar e segurar**: a estratégia precisa justificar o trabalho.
- Teste o MESMO timeframe e limiar que pretende usar. Se mudar algo, reteste.
- Teste em vários ativos e períodos. Resultado bom em um único par pode ser sorte.

## Avisos importantes

- **Nenhuma estratégia garante lucro.** Análise técnica trabalha com probabilidades,
  não certezas. Resultado passado não garante resultado futuro.
- A vantagem real vem da **disciplina**: seguir o stop, respeitar o risco de 1% e
  não operar sem confluência. A ferramenta ajuda; a disciplina é sua.
- Comece com pouco dinheiro (ou em modo simulado, anotando as operações) até
  confiar na estratégia e em você mesmo.
- Cripto é um mercado de altíssimo risco. Nunca invista o que não pode perder.

## Estrutura do projeto

```
analisar.py            Interface de linha de comando (análise, scan, backtest)
laboratorio.py         Comparador de estratégias com validação fora da amostra
simulador.py           Paper trading no navegador com gráficos em tempo real
duracao.py             Mede acerto histórico e duração típica de uma configuração
web/simulador.html     Interface visual do simulador
cripto/dados.py        Coleta de dados (API pública da Binance)
cripto/indicadores.py  EMA, RSI, MACD, Bollinger, ATR, ADX, volume
cripto/priceaction.py  Padrões de candle, estrutura, suporte/resistência, rompimentos
cripto/estrategia.py   As 4 estratégias e o sistema de pontuação
cripto/backtest.py     Simulador de operações em dados históricos
cripto/carteira.py     Carteira fictícia do paper trading (salva em carteira.json)
```
