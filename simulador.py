"""Simulador de trades (paper trading) com gráficos em tempo real.

Inicia um servidor local e abre a interface no navegador:
  python simulador.py

Lá você pode abrir operações de COMPRA e VENDA com USDT fictício, com ou sem
alavancagem, acompanhar o preço ao vivo e validar as estratégias sem arriscar
um centavo. Stop, alvo e liquidação são executados automaticamente — inclusive
de forma retroativa se o simulador ficar fechado por um tempo.
"""

import argparse
import json
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cripto import TIMEFRAME_CONTEXTO, dados, estrategia
from cripto.carteira import Carteira
from cripto.fluxo import adicionar_fluxo
from cripto.indicadores import adicionar_indicadores
from cripto.priceaction import adicionar_priceaction

PORTA = 8765  # padrão; substituída pela opção --porta em main()
PAGINA = Path(__file__).resolve().parent / "web" / "simulador.html"

carteira = Carteira()


def api_klines(params):
    simbolo = params.get("simbolo", ["BTCUSDT"])[0]
    tf = params.get("tf", ["4h"])[0]
    limite = min(int(params.get("limite", ["400"])[0]), 1000)
    df = dados.buscar_candles(simbolo, tf, limite)
    return [
        {"time": int(indice.value // 10**9), "open": linha["abertura"],
         "high": linha["maxima"], "low": linha["minima"], "close": linha["fechamento"],
         "volume": linha["volume"]}
        for indice, linha in df.iterrows()
    ]


def api_analise(params):
    simbolo = params.get("simbolo", ["BTCUSDT"])[0]
    tf = params.get("tf", ["4h"])[0]
    nome = params.get("estrategia", ["confluencia"])[0]
    if tf not in TIMEFRAME_CONTEXTO:
        raise ValueError(f"Timeframe inválido: {tf}")
    # apenas candles FECHADOS: o candle em formação ainda pode mudar de cara
    # ("repaint") — e o backtest, que valida a estratégia, só olha candle fechado.
    df = adicionar_priceaction(adicionar_indicadores(
        dados.buscar_candles(simbolo, tf, 400, apenas_fechados=True)))
    if nome == "fluxo":  # a estratégia de fluxo precisa das colunas de delta/funding/RS
        df = adicionar_fluxo(df, simbolo, tf)
    df_maior = adicionar_indicadores(
        dados.buscar_candles(simbolo, TIMEFRAME_CONTEXTO[tf], 400, apenas_fechados=True))
    diag = estrategia.avaliar(df, df_maior, nome, atr_stop=2.0, atr_alvo=1.0)
    diag["data_candle"] = str(diag["data_candle"])
    diag["regime_maior"] = str(df_maior["regime"].iloc[-1])
    diag["tf"] = tf
    diag["tf_maior"] = TIMEFRAME_CONTEXTO[tf]
    return diag


def api_pares(params):
    return dados.melhores_pares_usdt(60)


# Cache da varredura: o resultado vale enquanto o candle atual não muda muito
_cache_varredura: dict[tuple, tuple[float, dict]] = {}
_VALIDADE_VARREDURA = 180  # segundos


def api_varredura(params):
    tf = params.get("tf", ["4h"])[0]
    nome = params.get("estrategia", ["confluencia"])[0]
    if tf not in TIMEFRAME_CONTEXTO:
        raise ValueError(f"Timeframe inválido: {tf}")
    if nome not in estrategia.ESTRATEGIAS:
        raise ValueError(f"Estratégia inválida: {nome}")

    chave = (tf, nome)
    agora = time.time()
    em_cache = _cache_varredura.get(chave)
    if em_cache and agora - em_cache[0] < _VALIDADE_VARREDURA:
        return em_cache[1]

    pares = dados.melhores_pares_usdt(25)

    def avaliar_par(par):
        try:
            df = adicionar_priceaction(adicionar_indicadores(
                dados.buscar_candles(par, tf, 400, apenas_fechados=True)))
            if nome == "fluxo":
                df = adicionar_fluxo(df, par, tf)
            df_maior = adicionar_indicadores(
                dados.buscar_candles(par, TIMEFRAME_CONTEXTO[tf], 400, apenas_fechados=True))
            diag = estrategia.avaliar(df, df_maior, nome, atr_stop=2.0, atr_alvo=1.0)
            return {
                "simbolo": par, "direcao": diag["direcao"], "score": diag["score"],
                "score_compra": diag["score_compra"], "score_venda": diag["score_venda"],
                "forca": diag["forca"], "preco": diag["preco"],
                "stop": diag["stop"], "alvo": diag["alvo"],
                "rsi": diag["rsi"], "adx": diag["adx"],
            }
        except Exception:
            return None  # par sem histórico suficiente ou falha de rede: ignora

    with ThreadPoolExecutor(max_workers=8) as executor:
        linhas = [r for r in executor.map(avaliar_par, pares) if r]
    linhas.sort(key=lambda r: r["score"], reverse=True)

    resultado = {"tf": tf, "estrategia": nome, "avaliados": len(linhas), "resultados": linhas}
    _cache_varredura[chave] = (agora, resultado)
    return resultado


def api_varredura_total(params):
    """Varre as 25 maiores criptos rodando TODAS as estratégias em cada par.

    Para cada par devolve o melhor sinal entre as estratégias e o CONSENSO:
    quantas estratégias apontam a mesma direção (com força ao menos moderada).
    Consenso alto = confluência entre métodos independentes, o sinal mais confiável.
    """
    tf = params.get("tf", ["4h"])[0]
    if tf not in TIMEFRAME_CONTEXTO:
        raise ValueError(f"Timeframe inválido: {tf}")

    chave = (tf, "__todas__")
    agora = time.time()
    em_cache = _cache_varredura.get(chave)
    if em_cache and agora - em_cache[0] < _VALIDADE_VARREDURA:
        return em_cache[1]

    pares = dados.melhores_pares_usdt(25)
    # mantém o consenso original entre as 7 estratégias CLÁSSICAS; a estratégia
    # de fluxo participa da varredura por FAMÍLIAS (botão próprio), não desta
    nomes = [n for n in estrategia.ESTRATEGIAS if n != "fluxo"]

    def avaliar_par(par):
        try:
            df = adicionar_priceaction(adicionar_indicadores(
                dados.buscar_candles(par, tf, 400, apenas_fechados=True)))
            df_maior = adicionar_indicadores(
                dados.buscar_candles(par, TIMEFRAME_CONTEXTO[tf], 400, apenas_fechados=True))
            sinais = [estrategia.avaliar(df, df_maior, nome, atr_stop=2.0, atr_alvo=1.0)
                      for nome in nomes]
            for diag, nome in zip(sinais, nomes):
                diag["nome"] = nome
            melhor = max(sinais, key=lambda d: d["score"])
            consenso = sum(1 for d in sinais if d["direcao"] == melhor["direcao"]
                           and d["score"] >= estrategia.LIMIAR_MODERADO)
            return {
                "simbolo": par, "direcao": melhor["direcao"], "score": melhor["score"],
                "forca": melhor["forca"], "estrategia": melhor["nome"],
                "titulo": melhor["estrategia"], "preco": melhor["preco"],
                "stop": melhor["stop"], "alvo": melhor["alvo"],
                "rsi": melhor["rsi"], "adx": melhor["adx"],
                "consenso": consenso, "total": len(nomes),
                "detalhe": [{"estrategia": d["nome"], "direcao": d["direcao"],
                             "score": d["score"]} for d in sinais],
            }
        except Exception:
            return None  # par sem histórico suficiente ou falha de rede: ignora

    with ThreadPoolExecutor(max_workers=8) as executor:
        linhas = [r for r in executor.map(avaliar_par, pares) if r]
    # ordena por consenso e, em empate, pelo melhor score
    linhas.sort(key=lambda r: (r["consenso"], r["score"]), reverse=True)

    resultado = {"tf": tf, "modo": "todas", "avaliados": len(linhas), "resultados": linhas}
    _cache_varredura[chave] = (agora, resultado)
    return resultado


def api_varredura_familias(params):
    """NOVO: consenso entre FAMÍLIAS de informação independentes.

    Consenso entre as 7 estratégias clássicas conta juízes lendo o mesmo jornal
    (todas leem preço OHLCV). Aqui cada família lê um DADO diferente:
      PREÇO   — melhor das 7 estratégias clássicas (como no botão Consenso)
      FLUXO   — delta/CVD/trade médio (compra vs venda agressiva, ordem a ordem)
      FUNDING — posicionamento dos alavancados em extremo CONTRA a direção
      FORÇA   — desempenho relativo vs BTC (demanda própria do ativo)
    Concordância entre famílias independentes vale mais que 5/7 entre primas.
    """
    tf = params.get("tf", ["4h"])[0]
    if tf not in TIMEFRAME_CONTEXTO:
        raise ValueError(f"Timeframe inválido: {tf}")

    chave = (tf, "__familias__")
    agora = time.time()
    em_cache = _cache_varredura.get(chave)
    if em_cache and agora - em_cache[0] < _VALIDADE_VARREDURA:
        return em_cache[1]

    pares = dados.melhores_pares_usdt(25)
    nomes_preco = [n for n in estrategia.ESTRATEGIAS if n != "fluxo"]

    def avaliar_par(par):
        try:
            df = adicionar_priceaction(adicionar_indicadores(
                dados.buscar_candles(par, tf, 400, apenas_fechados=True)))
            df = adicionar_fluxo(df, par, tf)
            df_maior = adicionar_indicadores(
                dados.buscar_candles(par, TIMEFRAME_CONTEXTO[tf], 400, apenas_fechados=True))

            sinais = [estrategia.avaliar(df, df_maior, nome, atr_stop=2.0, atr_alvo=1.0)
                      for nome in nomes_preco]
            for diag, nome in zip(sinais, nomes_preco):
                diag["nome"] = nome
            melhor = max(sinais, key=lambda d: d["score"])
            consenso_preco = sum(1 for d in sinais if d["direcao"] == melhor["direcao"]
                                 and d["score"] >= estrategia.LIMIAR_MODERADO)
            compra = melhor["direcao"] == "COMPRA"

            diag_fluxo = estrategia.avaliar(df, df_maior, "fluxo", atr_stop=2.0, atr_alvo=1.0)
            score_fluxo = diag_fluxo["score_compra"] if compra else diag_fluxo["score_venda"]
            fluxo_ok = score_fluxo >= estrategia.LIMIAR_MODERADO

            ultimo = df.iloc[-1]
            perc = ultimo.get("funding_perc")
            if perc is None or perc != perc:  # NaN: par sem perpétuo ou sem dado
                funding_ok = None
            else:
                funding_ok = bool(perc <= 0.15) if compra else bool(perc >= 0.85)
            if par == "BTCUSDT":
                rs_ok = None  # BTC é a própria referência da força relativa
            else:
                rs_ok = bool(ultimo["rs_sobe"]) if compra else bool(ultimo["rs_desce"])

            # a família PREÇO só conta se o melhor sinal tem força ao menos moderada
            # (antes contava sempre, inflando o placar de pares sem sinal de preço)
            preco_ok = melhor["score"] >= estrategia.LIMIAR_MODERADO
            familias = (int(preco_ok) + int(fluxo_ok) + int(funding_ok is True)
                        + int(rs_ok is True))
            return {
                "simbolo": par, "direcao": melhor["direcao"], "score": melhor["score"],
                "forca": melhor["forca"], "estrategia": melhor["nome"],
                "consenso_preco": consenso_preco, "total_preco": len(nomes_preco),
                "score_fluxo": score_fluxo, "fluxo_ok": fluxo_ok,
                "funding_ok": funding_ok,
                "funding_perc": None if perc is None or perc != perc else round(100 * perc),
                "rs_ok": rs_ok,
                "familias": familias, "total_familias": 4,
                "preco": melhor["preco"],
            }
        except Exception:
            return None  # par sem histórico suficiente ou falha de rede: ignora

    with ThreadPoolExecutor(max_workers=8) as executor:
        linhas = [r for r in executor.map(avaliar_par, pares) if r]
    linhas.sort(key=lambda r: (r["familias"], r["score_fluxo"], r["score"]), reverse=True)

    resultado = {"tf": tf, "modo": "familias", "avaliados": len(linhas), "resultados": linhas}
    _cache_varredura[chave] = (agora, resultado)
    return resultado


class Manipulador(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass  # silencia o log de cada requisição

    def _origem_confiavel(self) -> bool:
        """Barra requisições disparadas por páginas de terceiros (CSRF/DNS rebinding).

        Qualquer site aberto no navegador consegue dar POST em 127.0.0.1 — sem esta
        checagem, uma página maliciosa poderia resetar a carteira ou abrir posições.
        O navegador sempre envia Host (e Origin em POSTs): ambos precisam ser locais.
        """
        locais = {f"127.0.0.1:{PORTA}", f"localhost:{PORTA}"}
        if self.headers.get("Host", "") not in locais:
            return False
        origem = self.headers.get("Origin")
        return origem is None or origem in {f"http://{h}" for h in locais}

    def _responder(self, conteudo, tipo="application/json", codigo=200, cabecalhos=None):
        corpo = (json.dumps(conteudo, ensure_ascii=False)
                 if tipo == "application/json" else conteudo).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        for chave, valor in (cabecalhos or {}).items():
            self.send_header(chave, valor)
        self.end_headers()
        self.wfile.write(corpo)

    def _erro(self, mensagem, codigo=400):
        self._responder({"erro": str(mensagem)}, codigo=codigo)

    def do_GET(self):
        if not self._origem_confiavel():
            self._erro("Origem não autorizada", 403)
            return
        url = urlparse(self.path)
        params = parse_qs(url.query)
        try:
            if url.path == "/":
                self._responder(PAGINA.read_text(encoding="utf-8"), tipo="text/html")
            elif url.path == "/lightweight-charts.js":
                self._responder((PAGINA.parent / "lightweight-charts.js").read_text(encoding="utf-8"),
                                tipo="application/javascript")
            elif url.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            elif url.path == "/api/klines":
                self._responder(api_klines(params))
            elif url.path == "/api/conta":
                self._responder(carteira.estado())
            elif url.path == "/api/diario":
                self._responder(carteira.diario())
            elif url.path == "/api/equity":
                self._responder(carteira.curva_patrimonio())
            elif url.path == "/api/exportar":
                self._responder(carteira.exportar_csv(), tipo="text/csv", cabecalhos={
                    "Content-Disposition": 'attachment; filename="historico-trades.csv"'})
            elif url.path == "/api/analise":
                self._responder(api_analise(params))
            elif url.path == "/api/pares":
                self._responder(api_pares(params))
            elif url.path == "/api/varredura":
                self._responder(api_varredura(params))
            elif url.path == "/api/varredura_total":
                self._responder(api_varredura_total(params))
            elif url.path == "/api/varredura_familias":
                self._responder(api_varredura_familias(params))
            else:
                self._erro("Rota não encontrada", 404)
        except (ValueError, KeyError) as erro:
            self._erro(erro, 400)  # erro de entrada (tf/limite/par inválido), não do servidor
        except Exception as erro:
            self._erro(erro, 500)

    def do_POST(self):
        if not self._origem_confiavel():
            self._erro("Origem não autorizada", 403)
            return
        url = urlparse(self.path)
        tamanho = int(self.headers.get("Content-Length", 0))
        try:
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
            if url.path == "/api/abrir":
                posicao = carteira.abrir(
                    simbolo=corpo["simbolo"],
                    direcao=corpo["direcao"],
                    margem=float(corpo["margem"]),
                    alavancagem=int(corpo.get("alavancagem", 1)),
                    stop=float(corpo["stop"]) if corpo.get("stop") else None,
                    alvo=float(corpo["alvo"]) if corpo.get("alvo") else None,
                    regime=corpo.get("regime"),
                    estrategia=corpo.get("estrategia"),
                    score=int(corpo["score"]) if corpo.get("score") is not None else None,
                    nota=corpo.get("nota"),
                )
                self._responder(posicao)
            elif url.path == "/api/fechar":
                self._responder(carteira.fechar(corpo["id"]))
            elif url.path == "/api/editar":
                self._responder(carteira.editar(
                    corpo["id"],
                    stop=float(corpo["stop"]) if corpo.get("stop") else None,
                    alvo=float(corpo["alvo"]) if corpo.get("alvo") else None,
                ))
            elif url.path == "/api/dimensionar":
                self._responder(carteira.dimensionar(
                    simbolo=corpo["simbolo"],
                    direcao=corpo["direcao"],
                    risco_pct=float(corpo["risco_pct"]),
                    stop=float(corpo["stop"]),
                    alavancagem=int(corpo.get("alavancagem", 1)),
                ))
            elif url.path == "/api/reset":
                carteira.resetar()
                self._responder({"ok": True})
            else:
                self._erro("Rota não encontrada", 404)
        except (ValueError, KeyError) as erro:
            self._erro(erro, 400)
        except Exception as erro:
            self._erro(erro, 500)


class _Servidor(ThreadingHTTPServer):
    # No Windows, o SO_REUSEADDR padrão do http.server deixa DUAS instâncias
    # "dividirem" a mesma porta em silêncio (requisições vão para qualquer uma).
    # Desligado, a segunda instância falha com OSError e avisamos direito.
    allow_reuse_address = sys.platform != "win32"


def main():
    global PORTA
    parser = argparse.ArgumentParser(description="Simulador de trades (paper trading)")
    parser.add_argument("--porta", type=int, default=PORTA,
                        help=f"Porta do servidor local (padrão: {PORTA})")
    parser.add_argument("--sem-navegador", action="store_true",
                        help="Não abre o navegador automaticamente")
    args = parser.parse_args()
    PORTA = args.porta

    endereco = f"http://127.0.0.1:{PORTA}"
    try:
        servidor = _Servidor(("127.0.0.1", PORTA), Manipulador)
    except OSError:
        print(f"A porta {PORTA} já está em uso — provavelmente outro simulador aberto.")
        print(f"Use a janela que já está rodando ({endereco}) ou inicie em outra porta:")
        print(f"  python simulador.py --porta {PORTA + 1}")
        sys.exit(1)
    print("=" * 56)
    print("  SIMULADOR DE TRADES — paper trading com USDT fictício")
    print(f"  Aberto em: {endereco}")
    print("  Para encerrar: Ctrl+C nesta janela")
    print("=" * 56)
    if not args.sem_navegador:
        threading.Timer(1.0, lambda: webbrowser.open(endereco)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nSimulador encerrado. Sua carteira continua salva.")


if __name__ == "__main__":
    main()
