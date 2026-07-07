# Analisador Cripto — análise técnica por confluência de indicadores

**Versão 3.1** (julho/2026)

Sistema que analisa gráficos de criptomoedas usando os indicadores mais consagrados
do mercado **e price action** (padrões de candle, estrutura de mercado, suporte e
resistência) e aponta oportunidades de compra e venda **apenas quando vários sinais
independentes concordam entre si** (confluência) — a abordagem defendida por traders
profissionais e pela literatura de análise técnica.

Inclui um **laboratório de estratégias** que mede a assertividade histórica real de
cada combinação estratégia/stop/alvo, validando em dados fora da amostra.

## Novidades da versão 3.1

- **🤖 Bots de operação automática no simulador**: cada bot vigia um par + timeframe +
  estratégia e opera sozinho a cada candle fechado, com risco dimensionado (máx. 2%),
  stop/alvo por ATR, filtro de regime, guardas de exposição e jornal de decisões.
- **Validação walk-forward para os bots** (5 ativos, custos completos): a config
  `rompimento` 1d stop 1,5×/alvo 3,0× ATR limiar 85 foi **aprovada fora da amostra**
  (FL 1,41 em 109 operações/~5 anos); `reversao` 4h 2,0×/1,0× limiar 70 passou nos 3
  filtros do laboratório. O veredito do walk-forward agora respeita a relação
  risco/retorno das configs (não exige mais acerto fixo).
- **Painel de desempenho por bot** + comparativo Manual × Bots na interface.
- **🔔 Notificações nativas do sistema** quando um bot abre posição ou uma operação fecha.
- **`iniciar-simulador.bat`**: mantém servidor e bots sempre no ar (com auto-reinício).
- Carteira: campo `bot` no histórico/CSV e diretório de dados isolável
  (`ANALISTA_CRIPTO_DIR`) para testes.

## Novidades da versão 3.0

Varredura completa do sistema com **14 bugs corrigidos e verificados** (detalhes em
`RELATORIO-VARREDURA.md`). Destaques:

- **Simulador**: preço/gráfico ao vivo voltaram a fluir (update travava entre candles),
  lightweight-charts embarcada (funciona sem CDN), proteção contra requisições de
  outros sites (CSRF), opção `--porta` e detecção de instância duplicada no Windows.
- **Backtest mais honesto ainda**: o nocional agora é limitado ao capital (antes um
  stop apertado criava alavancagem implícita impossível em spot).
- **Carteira**: o replay offline não pula mais stops no candle parcial do último check.
- **Indicadores**: RSI neutro (50) no aquecimento e a zona de ouro de Fibonacci passou
  a exigir swing confirmado na direção (não lê mais repique de queda como pullback de alta).
- **Robustez**: scan reporta pares que falharam, laboratório ignora ativo com histórico
  curto sem derrubar a grade, erros de API com código e mensagem corretos.

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
| `--estrategia` | confluencia, tendencia_pa, reversao, rompimento, divergencia, fibonacci ou tendencia_ema | confluencia |
| `--candles` | Candles no backtest | 1500 |
| `--capital` | Capital inicial simulado | 1000 |
| `--risco` | Risco por operação (% do capital) | 1 |
| `--limiar` | Score mínimo para entrar | 70 |
| `--stop` / `--alvo` | Stop e alvo em múltiplos de ATR | 1.5 / 3.0 |
| `--sem-venda` | Só operações de compra (long only) | desligado |
| `--regime` | Só entra A FAVOR do regime (COMPRA em ALTA, VENDA em BAIXA) | desligado |

O backtest é deliberadamente honesto: entra só na abertura do candle seguinte ao
sinal, **verifica stop e alvo já no candle de entrada** (com o stop tendo
prioridade em caso de empate), desconta taxa + slippage + funding e **só analisa
candles fechados** — o candle em formação ainda pode mudar de cara ("repaint") e
por isso nunca gera sinal, nem aqui nem na análise ao vivo.

### 4. Laboratório: descobrir qual estratégia tem a melhor assertividade histórica

```
python laboratorio.py BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT --tf 4h --meta 65
```

Testa **todas as combinações** de estratégia x stop/alvo x limiar nos ativos
informados, com **três camadas de validação** (cada vez mais rigorosas):

1. **Treino/teste 70/30** — mede o acerto no terço final que a otimização não viu.
2. **Intervalo de confiança (Wilson 95%)** — só aprova se o *pior caso* do acerto
   ainda superar o ponto de empate. Mata o "72% com 42 trades" que pode ser sorte.
3. **Walk-forward** — escolhe a melhor config olhando só o passado e mede na janela
   seguinte, repetidamente. É o teste mais próximo da realidade e o veredito final.

O backtest inclui **taxa + slippage + funding**, então os números são conservadores
de propósito (backtests "limpos" enganam a favor). Essa pilha de validação é o que
separa uma estratégia real de uma ilusão estatística (overfitting).

O laboratório também mostra o **desempenho por regime de mercado** (ALTA/BAIXA/LATERAL)
— revela quando uma estratégia só funciona em um regime — e aceita `--gestao`
(fixo/breakeven/trailing/parcial) para comparar tipos de saída e `--regime` para
testar a grade inteira operando SÓ a favor do regime. Ex.:

```
python laboratorio.py BTCUSDT ETHUSDT SOLUSDT --tf 4h --gestao trailing
python laboratorio.py BTCUSDT ETHUSDT SOLUSDT --tf 4h --regime
```

### 5. Simulador: treinar com USDT fictício em preços reais (paper trading)

```
python simulador.py
```

Abre uma interface no navegador (http://127.0.0.1:8765) com:

- **Gráfico de candles em tempo real** de qualquer par da Binance (15m, 1h, 4h, 1d),
  com **EMA 50, EMA 200, Bandas de Bollinger e volume** ligáveis por um clique e
  **contagem regressiva** para o fechamento do candle atual
- **Compra e venda** com USDT fictício (carteira começa com 10.000)
- **Alavancagem de 1x a 25x** com preço de liquidação calculado e exibido no gráfico
- **Gestão de risco embutida (guardrails):** stop obrigatório em posições alavancadas;
  botão "Calcular margem" que dimensiona a posição para arriscar só 1–2%
  do capital; avisos de alavancagem alta (10x+), exposição correlacionada e de operar
  **contra o regime** de mercado
- **Painel "Risco da carteira"**: margem em uso, quanto você perde se TODOS os stops
  baterem (em USDT e % do patrimônio), margem exposta sem stop e balanço
  compra × venda — o risco agregado também aparece no topo da tela
- **Gestão ativa da posição**: editar stop/alvo de posições abertas, botão
  "Stop → entrada" (breakeven em um clique) e **barra de progresso** mostrando onde
  o preço está entre o stop e o alvo
- **Curva de patrimônio** (equity curve) do resultado realizado, operação a operação
- **Diário de trades**: campo "por que este trade?" na abertura — a anotação aparece
  na posição, no histórico e no CSV exportado (revisar os próprios motivos é o
  exercício nº 1 de disciplina)
- **Exportar CSV**: histórico completo com datas, estratégia, score, regime e notas,
  pronto para planilha
- **Regime de mercado ao vivo** no painel de análise — do timeframe operado E do
  timeframe de contexto (ex.: 4h + 1d)
- **Coach de disciplina:** analisa seu histórico e aponta SEUS padrões de erro —
  operações sem stop, contra a tendência, liquidações e resultado por alavancagem
- **Stop loss e alvo** — o botão "Sugerir" preenche com stop 2×ATR e alvo 1×ATR a
  partir do preço ao vivo
- **Score das 7 estratégias** direto na tela, com os critérios atendidos, sempre no
  último candle FECHADO (sem repaint — o mesmo dado que o backtest enxerga)
- **Botão "🔍 Top 25"**: analisa as 25 maiores criptos em paralelo (~5 s) com a
  estratégia selecionada e ranqueia os melhores gráficos — clicar num resultado abre
  o gráfico do par com a direção e a análise já carregadas
- **Botão "🎯 Consenso"**: roda as 7 estratégias em cada um dos 25 pares
  e ordena por CONSENSO — quantas estratégias apontam a mesma direção. Consenso alto
  (badge dourado) é o sinal mais confiável, pois é confluência entre
  métodos independentes, não um pico isolado de uma estratégia só
- Execução automática de stop/alvo/liquidação, **inclusive retroativa**: se você
  fechar o simulador, ao reabrir ele verifica os candles do período e executa as
  saídas no preço certo (pior caso primeiro, como no backtest)
- Taxa de 0,05% por lado (taker de futuros) e histórico com taxa de acerto

A carteira fica salva fora da pasta do projeto (em `%LOCALAPPDATA%\analista-cripto\`,
protegida da sincronização do OneDrive). O botão "Reiniciar" zera tudo.
Use o simulador por algumas semanas antes de pensar em dinheiro real: ele valida
não só a estratégia, mas a SUA disciplina em segui-la.

### 6. Bots automáticos: as estratégias operando sozinhas no simulador

No card **"🤖 Bots automáticos"** você cria robôs que operam a carteira fictícia sem
intervenção. Cada bot vigia **um par + um timeframe (15m/1h/4h/1d) + uma estratégia**
(qualquer uma das 8) e, a cada **candle fechado** (sem repaint, igual ao backtest):

1. recalcula o score; se ficar abaixo do limiar configurado, não faz nada;
2. aplica o **filtro de regime** (opcional): COMPRA só em ALTA, VENDA só em BAIXA;
3. faz a **análise de risco da operação**: margem dimensionada para arriscar
   exatamente o % configurado do capital até o stop (máx. 2%), pula se a exposição
   na mesma direção passar de 40% do capital ou se o bot já tiver posição aberta;
4. abre a posição com **stop e alvo em múltiplos de ATR** (padrão 2×/1×, a config
   aprovada no laboratório) — a partir daí a própria carteira executa
   stop/alvo/liquidação automaticamente.

Cada decisão (entrada, sinal fraco, pulo por guarda de risco, erro) fica registrada
no **jornal do bot**, e as posições abertas por bot aparecem com o selo 🤖. Regras de
segurança fixas: risco por operação limitado a 2%, alavancagem de bot limitada a 10x,
uma posição por bot. Os bots operam **enquanto a janela do simulador estiver aberta**
(não operam retroativamente; ao reabrir, retomam no próximo candle).

Acompanhamento: cada card de bot mostra o **desempenho realizado** (resultado, taxa de
acerto, fator de lucro, posições abertas) e o topo do card compara **Manual × Bots** —
mesmo capital, mesmas regras. O botão **🔔 Notificações** no cabeçalho ativa avisos
nativos do sistema quando um bot abre posição ou qualquer operação fecha
(alvo/stop/liquidação), mesmo com a aba em segundo plano.

**Gestão da saída** (validada no laboratório — `RELATORIO-VARREDURA.md` §5.1): além da
saída fixa, o bot pode gerenciar a posição sozinho — **breakeven** (stop na entrada em
+1R), **trailing** (persegue o melhor preço a stop×ATR; ✅ aprovado no walk-forward 1d
com folga) e **parcial** (realiza metade em +1R, com semântica de ordem limitada).
Cada ação vira um evento 🛡️ no jornal do bot. Para configs de alvo curto (ex.: stop
2×/alvo 1× ATR), o gatilho de +1R fica além do alvo e a gestão raramente ativa —
nesses casos, use a saída fixa mesmo.

> Honestidade obrigatória: bot não cria edge — ele só executa com disciplina uma
> estratégia que você deve validar antes no `laboratorio.py` (walk-forward). Use os
> bots para medir, no simulado, como a estratégia se comporta operada friamente.

### 7. Deixar o simulador sempre ligado (bots operando)

Os bots só avaliam candles enquanto o servidor está no ar. Para não depender de
lembrar de abrir (nem perder horas de operação por uma janela fechada sem querer):

```
iniciar-simulador.bat
```

O script abre o simulador e o **reinicia sozinho** se ele cair; para parar de vez,
feche a janela. Para iniciar automaticamente junto com o Windows, crie a tarefa uma
única vez (Prompt de Comando, ajustando o caminho da pasta):

```
schtasks /Create /TN "Analisador Cripto" /SC ONLOGON /TR "\"C:\caminho\da\pasta\iniciar-simulador.bat\""
```

(para desfazer: `schtasks /Delete /TN "Analisador Cripto"`)

## As estratégias

São sete, todas combinando indicadores e price action por pontuação (0 a 100):

| Estratégia | Lógica | Principais sinais |
|---|---|---|
| `confluencia` | Indicadores clássicos a favor da tendência maior | EMA, RSI, MACD, Bollinger, ADX, volume |
| `tendencia_pa` | Pullback na média com padrão de candle | Estrutura de topos/fundos, EMA21/50, engolfo/martelo |
| `reversao` | Reversão à média em extremos | RSI < 30 / > 70, Bollinger, suporte/resistência, padrão de candle |
| `rompimento` | Rompimento de 20 candles com força | Máx/mín de 20, volume 1,5x, ADX > 25 |
| `divergencia` | Divergência RSI x preço + confirmação | Divergência regular, candle de reversão, nível, MACD, volume |
| `fibonacci` | Pullback na zona de ouro (50–61,8%) a favor da tendência | Retração de Fibonacci do último swing, estrutura, candle |
| `tendencia_ema` | Pullback na EMA10 com volume contraído (estratégia "10 EMA") | EMA10 vs EMA50, pullback, volume fraco na correção, candle |

> **O que o laboratório revelou (jul/2026, BTC/ETH/SOL, 4h, motor corrigido) — versão honesta:**
> o backtest ficou ainda mais rigoroso (agora verifica stop/alvo **já no candle de
> entrada**, o que backtests ingênuos ignoram) e os números caíram — sinal de que os
> antigos estavam otimistas. Sem filtro de regime, **nenhuma config passou nos 3
> filtros** e o walk-forward reprovou (acerto 40%, FL 0,73). Com o novo **filtro de
> regime** (`--regime`, só opera a favor da tendência), **1 config passou nos 3
> filtros**: `reversao` stop 1,5 / alvo 1,0 / limiar 70 — 88% de acerto no teste,
> FL 3,88, e o pior caso estatístico (IC95 65,7%) acima do ponto de empate (60%).
> MAS a amostra é pequena (55 trades) e o **walk-forward agregado continua
> REPROVADO** (38%, FL 0,78). **Tradução honesta: ainda não há edge comprovado para
> operar no automático; a `reversao` com filtro de regime é uma candidata a
> acompanhar no simulador, não uma promessa.** Isso não é defeito — é a ferramenta
> fazendo o trabalho dela: te mostrar isso AGORA, no simulado, e não com seu
> dinheiro. Rode `python laboratorio.py ...` em vários timeframes/ativos (com e sem
> `--regime`) e só arrisque dinheiro real no que passar no walk-forward.

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
cripto/indicadores.py  EMA, RSI, MACD, Bollinger, ATR, ADX, volume, regime
cripto/priceaction.py  Padrões de candle, estrutura, S/R, divergências, Fibonacci
cripto/estrategia.py   As 7 estratégias e o sistema de pontuação
cripto/backtest.py     Simulador de operações em dados históricos
cripto/carteira.py     Carteira fictícia (salva em %LOCALAPPDATA%\analista-cripto\)
```
