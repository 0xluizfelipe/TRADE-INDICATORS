# Varredura do sistema — bugs, defeitos e propostas para a nova versão

Data: 06/07/2026 · Base: commit `75c1da0` (main)
Método: leitura integral dos ~2.800 linhas + execução real (análise, backtest, laboratório,
scan, API do simulador) + verificação empírica dos bugs suspeitos com dados sintéticos.

> **ATUALIZAÇÃO 06/07/2026 — todos os bugs B1–B14 foram CORRIGIDOS** (um commit por
> correção nesta branch; veja `git log`). Cada fix foi verificado: testes sintéticos
> (RSI, Fibonacci, cap de nocional, filtro de gap), curl na API (400/403/colisão de
> porta) e navegador real (preço ao vivo fluindo + lib local). As seções 2 (método)
> e 3 (melhorias) permanecem como plano da nova versão.

---

## 1. BUGS CONFIRMADOS

### 🔴 B1 — Gráfico/preço "ao vivo" do simulador fica congelado entre candles
**Onde:** `web/simulador.html` → `atualizarCandles()` (linhas ~436-447)
`api/klines?limite=2` devolve `[candle_fechado_anterior, candle_em_formação]` e o código chama
`serie.update()` para os dois. A lightweight-charts 4.2.0 **lança
`Cannot update oldest data`** quando o `time` é menor que o do último bar (confirmado no código-fonte
da lib: `if (key(novo) < key(último)) throw`). O erro estoura no primeiro candle, o `catch` engole,
e nada é atualizado: `candlesAtuais`, o preço ao vivo e os indicadores só mudam **no fechamento do
candle** (a cada 4h no timeframe padrão!) ou ao trocar de par. O `ultimoPrecoVivo` congelado também
contamina o "Sugerir stop/alvo".
**Correção:** só chamar `update()` para candles com `time >=` o último do gráfico
(ex.: `dados.filter(c => c.time >= candlesAtuais.at(-1).time).forEach(...)`), atualizando
`candlesAtuais` de forma correspondente.

### 🔴 B2 — Backtest permite posição maior que o capital (alavancagem implícita em spot)
**Onde:** `cripto/backtest.py:251` — `quantidade = (capital * risco) / risco_unitario` sem teto.
Com stop apertado o nocional passa do capital: ATR de 0,5% do preço + stop 1,5×ATR ⇒ nocional
**1,33× o capital** (verificado numericamente). Em timeframes menores (15m/1h), onde ATR% é
rotineiramente < 0,5%, os backtests assumem tamanhos impossíveis para spot (o modelo usa taxa spot
de 0,1%) — resultados otimistas/irreais e risco real por trade maior que o declarado.
**Correção:** limitar `quantidade ≤ capital / entrada` (spot) ou modelar margem/alavancagem
explícita (perp), reportando quando o risco pedido não é atingível.

### 🟠 B3 — Stops podem ser pulados na reconstrução retroativa da carteira (simulador)
**Onde:** `cripto/carteira.py:353` — `recentes = candles[... >= inicio - 60_000]`
A margem de 1 minuto só cobre o intervalo "1m". Quando o gap offline exige candles maiores, o candle
que **contém** o `ultimo_check_ms` é excluído do replay: janela ignorada de até 14 min (gap ~10 dias,
15m), 59 min (1h), ~4h (4h) e ~24h (fallback 1d) — verificado numericamente. Se o stop/alvo/liquidação
foi atingido só nesse trecho e o preço voltou, a posição sobrevive indevidamente.
**Correção:** usar como margem a duração do candle do intervalo escolhido (ex.: `inicio - tam*60_000`).

### 🟠 B4 — RSI vale 100 no warmup e em séries sem queda
**Onde:** `cripto/indicadores.py:26` — `.fillna(100.0)`
Confirmado: série *caindo* → RSI[0] = 100; série *flat* → RSI = 100. O backtest escapa pelo warmup de
210 candles, mas a análise ao vivo de ativos recém-listados pode disparar critérios como
`rsi > 70` (venda da estratégia `reversao`) sem fundamento — e `_recente()` propaga por 3-5 candles.
**Correção:** preencher NaN com 50 (neutro) e tratar o caso ganho=perda=0 como 50, reservando 100
somente para perda média = 0 com ganho > 0.

### 🟠 B5 — Zona de Fibonacci não confere a ordem do swing (LONG em plena baixa)
**Onde:** `cripto/priceaction.py:150-160`
`pa_fib_long`/`pa_fib_short` usam `ult_topo`/`ult_fundo` sem verificar **qual confirmou por último**.
Confirmado com série sintética de baixa clara: 4 disparos de `pa_fib_long` nos repiques. O critério
vale **30 pontos** na estratégia `fibonacci` — pontua "pullback de alta" onde não houve swing de alta.
**Correção:** condicionar `pa_fib_long` a fundo→topo (topo mais recente que o fundo) e o espelho para
short — a posição do último evento em `preco_topo`/`preco_fundo` já permite isso.

### 🟡 B6 — Rotas GET da API respondem 500 para erro de entrada do usuário
**Onde:** `simulador.py:313-314` — confirmado via curl: `tf=2h` ou `limite=abc` ⇒ HTTP 500.
O `do_POST` já mapeia `ValueError/KeyError → 400`; o `do_GET` não.

### 🟡 B7 — Retry inútil e mensagem crua para par inexistente
**Onde:** `cripto/dados.py:31-42` — 400 Bad Request entra no loop de 3 tentativas (com sleeps) e a
mensagem final é técnica (`400 Client Error for url...`) em vez de "o par existe?". Não repetir 4xx.

### 🟡 B8 — "Preço atual" no CLI é o fechamento do último candle FECHADO
**Onde:** `analisar.py:68` — pode estar até 1 candle defasado (4h no padrão). `dados.preco_atual()`
existe e não é usado aqui. Rotular como "fechamento do candle analisado" e/ou exibir o preço vivo ao lado.

### 🟡 B9 — Curva de patrimônio não limpa após "Reiniciar"
**Onde:** `web/simulador.html` → `atualizarEquity()` retorna cedo com lista vazia; a curva antiga
permanece após o reset até uma nova operação fechar.

### 🟡 B10 — Laboratório quebra inteiro se 1 ativo tem histórico curto
**Onde:** `laboratorio.py:190-196` — `backtest.executar` levanta `ValueError` (histórico
insuficiente) sem try por símbolo: a grade toda (e os downloads já feitos) é perdida com traceback.

### 🟡 B11 — Scan engole erros silenciosamente
**Onde:** `analisar.py:110-111` — `except Exception: continue`. Se a rede cai no meio, o scan
"termina" com poucos pares sem avisar quantos falharam (e o `sleep` de cortesia fica dentro do `try`,
não executa em falha).

### 🟡 B12 — Servidor local sem verificação de origem (CSRF)
**Onde:** `simulador.py` — qualquer página aberta no navegador pode dar `POST` em
`http://127.0.0.1:8765/api/reset` ou `/api/abrir` (o Content-Type não é validado). É carteira
fictícia, mas é SEU histórico de treino. Correção simples: validar cabeçalho `Origin`/`Host`.

### 🟡 B13 — Porta fixa 8765 sem tratamento de colisão
**Onde:** `simulador.py:30,363` — segunda instância morre com traceback `OSError` (aconteceu nesta
varredura: havia outra instância rodando). Adicionar `--porta` e mensagem amigável.

### ⚪ B14 — Miúdos (código morto / inconsistências)
- `laboratorio.py:81` — parâmetro `meta` de `walk_forward()` nunca é usado.
- `cripto/backtest.py:135` — `candles_aberta` é incrementado e nunca lido (morto).
- `cripto/backtest.py:121` — `_horas_candle` não tem 30m/2h/6h/8h/12h (funding proporcional errado
  se esses TFs forem liberados; hoje o CLI restringe as escolhas).
- `TIMEFRAME_CONTEXTO` duplicado em 4 arquivos (analisar/laboratorio/simulador/duracao).
- `duracao.py` baixa candles **sem** `apenas_fechados=True` (inconsistente com o resto).
- `web/simulador.html` — `nota` do usuário injetada via innerHTML (auto-XSS local, baixo risco);
  dependência única do CDN unpkg: sem internet até o unpkg, `LightweightCharts` fica indefinido, o
  script morre no início e **nenhum** botão da página funciona.
- `simulador.py:239` — na varredura por famílias, a família PREÇO conta como "1" mesmo com score
  fraco (< 55), inflando o placar `familias/4` de pares sem sinal de preço.
- `cripto/carteira.py` — `verificar_saidas()` segura o lock durante chamadas de rede (Binance lenta
  ⇒ UI trava); caches de `dados.py`/`fluxo.py` são acessados por 8 threads sem lock (corrida benigna,
  só duplica downloads).

---

## 2. OBSERVAÇÕES DE MÉTODO (não são bugs, mas afetam a confiança nos números)

1. **Custos híbridos:** o backtest mistura taxa spot (0,1%) com funding de perp e permite short —
   spot não tem short e perp taker é ~0,04-0,05%. Conservador, porém impreciso nos dois casos.
   Sugerido: flag `--mercado spot|perp` com custos e restrições coerentes.
2. **Drawdown subestimado:** a `curva_capital` só marca capital **realizado**; uma posição aberta
   afundando não aparece no DD até fechar. Marcar a mercado candle a candle.
3. **Ranking do laboratório ainda "minera" o teste:** a tabela ordena/aprova olhando os 30% de teste
   com 80 configs → viés de seleção múltipla (o walk-forward mitiga — mantê-lo como veredito final).
4. **Contexto multi-timeframe 1 candle mais defasado que o necessário** (`_alinhar_contexto` com
   `shift(1)` sobre dados que já são só de candles fechados) — seguro, mas perde reatividade.
5. **Sem testes automatizados nem CI** — nenhum `test_*.py` no repositório. Para um sistema cuja
   proposta é "backtest honesto", uma suíte que prove ausência de look-ahead é o maior upgrade de
   credibilidade possível.

---

## 3. MELHORIAS E NOVAS FUNÇÕES PROPOSTAS (nova versão)

### Fundação (fazer primeiro)
1. **Corrigir B1–B5** (os que mudam números/decisões).
2. **Suíte pytest + GitHub Actions:** indicadores contra valores de referência; teste anti-look-ahead
   (recalcular o sinal do candle i usando só dados ≤ i e comparar); casos B3/B4/B5 como regressão.
3. **Cache local de candles** (parquet/sqlite incremental): laboratório e scans re-baixam tudo a cada
   execução hoje; com cache o lab roda offline e em segundos, e some o risco de rate-limit.
4. **Modelo de mercado explícito** (`--mercado spot|perp`): taxas, funding, short e cap de nocional
   coerentes por modo (resolve B2 e a observação 1 de uma vez).

### Produto (o que destrava mais valor)
5. **Alertas automáticos:** processo `monitorar.py` que roda em loop (watchlist + timeframe), dispara
   notificação (Telegram/Discord/notificação do Windows) quando score ≥ limiar ou quando famílias
   concordam — hoje o usuário precisa lembrar de escanear.
6. **Ordens pendentes no simulador:** limite/stop-entry + OCO, com slippage também no paper trading
   (hoje só o backtest tem slippage; a entrada simulada é "perfeita").
7. **Backtest de carteira multi-ativo:** N pares simultâneos com capital único, limite de exposição
   por direção e correlação — é como o usuário realmente opera (o simulador já expõe esse conceito).
8. **Relatório rico do backtest:** exportar HTML/PNG com curva de capital, drawdown, distribuição de
   trades, MAE/MFE, duração média, resultado por regime e por hora/dia da semana.
9. **Walk-forward embutido no CLI** (`analisar.py PAR --walkforward`): re-otimização por janela sem
   precisar do laboratório completo.
10. **Métricas novas:** Sharpe/Sortino anualizados, expectativa em R, sequência máxima de perdas,
    Kelly fracionário sugerido.

### Dados novos (o caminho que `fluxo.py` abriu)
11. **Open interest** (`fapi /futures/data/openInterestHist`), **long/short ratio** e **liquidações**
    como 5ª/6ª famílias na varredura por famílias.
12. **Dominância/beta vs BTC** no relatório de análise (já existe a força relativa — expor o número).
13. **Timeframes 30m/2h** (infra já suporta; falta liberar nos CLIs e completar `_horas_candle` e
    `TIMEFRAME_CONTEXTO`).

### Qualidade de vida
14. **`pyproject.toml`** com `pip install -e .` e comandos `analisar`, `laboratorio`, `simulador`.
15. **Modo replay** no simulador (avançar candle a candle num período histórico para treinar decisão).
16. **Config YAML** para pesos/critérios das estratégias (hoje é preciso editar `estrategia.py`).
17. **`--porta` no simulador + verificação de instância já aberta** (abre o navegador na existente).

---

## 4. O QUE JÁ ESTÁ BOM (para manter na nova versão)

- Separação limpa dados → indicadores → price action → estratégia → backtest.
- Backtest genuinamente conservador: entrada no candle seguinte, stop prioritário no empate,
  checagem do candle de entrada, custos descontados — raro em projetos desse tipo.
- Laboratório com treino/teste, IC de Wilson e walk-forward — metodologia acima da média.
- Price action vetorizado sem olhar o futuro (fractais confirmados com shift) — auditado, correto.
- Simulador com guardrails de risco (stop obrigatório alavancado, avisos de exposição, coach).
- Gravação atômica da carteira fora do OneDrive — decisão consciente e correta.
