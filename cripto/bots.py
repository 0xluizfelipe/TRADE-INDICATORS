"""Bots de operação automática do simulador (paper trading).

Cada bot vigia UM par em UM timeframe com UMA das estratégias existentes.
A cada candle FECHADO ele recalcula o score e, se o limiar for atingido E as
guardas de risco passarem, abre a posição na carteira fictícia com stop/alvo
por ATR e margem dimensionada pelo risco — o mesmo fluxo validado no backtest
(sinal no candle fechado -> entrada a mercado em seguida, sem repaint).

Guardas de risco de CADA operação, na ordem em que são checadas:
1. filtro de regime opcional (COMPRA só em ALTA, VENDA só em BAIXA);
2. uma posição por bot (sem piramidar);
3. risco por operação limitado a RISCO_MAX_PCT do capital (2%);
4. exposição na mesma direção limitada a EXPOSICAO_MAX_PCT (40%) do capital;
5. alavancagem de bot limitada a 10x (acima disso é zona de liquidação de varejo).
Cada decisão — entrada, sinal fraco, pulo por guarda ou erro — fica no jornal.

Os bots NÃO operam retroativamente: se o simulador ficar fechado, retomam no
próximo candle. As posições já abertas continuam protegidas pelo
stop/alvo/liquidação da carteira, que reexecuta o período offline candle a candle.
"""

import json
import threading
import time
import uuid

from . import TIMEFRAME_CONTEXTO, dados
from .carteira import _BASE_DADOS, EXPOSICAO_MAX_PCT, RISCO_MAX_PCT
from .estrategia import ESTRATEGIAS, avaliar
from .fluxo import adicionar_fluxo
from .indicadores import adicionar_indicadores
from .priceaction import adicionar_priceaction

ARQUIVO_BOTS = _BASE_DADOS / "bots.json"
INTERVALO_MOTOR = 20       # segundos entre passadas do motor
ALAVANCAGEM_MAX_BOT = 10   # teto de alavancagem para operação automática
TAMANHO_JORNAL = 40        # eventos guardados por bot

_MINUTOS_TF = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def _agora_ms() -> int:
    return int(time.time() * 1000)


class MotorBots:
    """Gerencia os bots e roda o laço de avaliação em uma thread própria."""

    def __init__(self, carteira):
        self.carteira = carteira
        self._trava = threading.RLock()
        self.bots: list[dict] = []
        if ARQUIVO_BOTS.exists():
            self.bots = json.loads(ARQUIVO_BOTS.read_text(encoding="utf-8"))

    # ------------------------- persistência -------------------------

    def _salvar(self):
        with self._trava:
            temporario = ARQUIVO_BOTS.with_suffix(".tmp")
            temporario.write_text(json.dumps(self.bots, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
            temporario.replace(ARQUIVO_BOTS)

    def _anotar(self, bot: dict, tipo: str, msg: str):
        """Registra um evento no jornal do bot (entrada/sinal/pulado/gestao/erro/info)."""
        with self._trava:
            msg = str(msg)[:220]
            # o mesmo erro repetido a cada passada (20s) não deve inundar o jornal
            if (tipo == "erro" and bot["jornal"]
                    and bot["jornal"][-1]["tipo"] == "erro" and bot["jornal"][-1]["msg"] == msg):
                return
            bot["jornal"].append({"t": _agora_ms(), "tipo": tipo, "msg": msg})
            del bot["jornal"][:-TAMANHO_JORNAL]

    # ------------------------- gestão -------------------------

    def criar(self, simbolo: str, tf: str, estrategia: str, limiar=70,
              stop=2.0, alvo=1.0, risco_pct=1.0, alavancagem=1,
              filtro_regime=True, direcoes=("COMPRA", "VENDA"),
              gestao: str = "fixo", nome: str | None = None) -> dict:
        simbolo = simbolo.upper().strip()
        if tf not in _MINUTOS_TF:
            raise ValueError(f"Timeframe inválido: {tf} (use {', '.join(_MINUTOS_TF)})")
        if estrategia not in ESTRATEGIAS:
            raise ValueError(f"Estratégia inválida: {estrategia}")
        if gestao not in ("fixo", "breakeven", "trailing", "parcial"):
            raise ValueError("Gestão de saída inválida (fixo, breakeven, trailing ou parcial).")
        limiar = int(limiar)
        if not 1 <= limiar <= 100:
            raise ValueError("Limiar deve ficar entre 1 e 100.")
        stop, alvo = float(stop), float(alvo)
        if stop <= 0 or alvo <= 0:
            raise ValueError("Stop e alvo (em múltiplos de ATR) devem ser maiores que zero.")
        risco_pct = float(risco_pct)
        if not 0 < risco_pct <= RISCO_MAX_PCT:
            raise ValueError(f"Risco por operação de bot deve ficar entre 0 e {RISCO_MAX_PCT:g}% "
                             "do capital (gestão de risco não é opcional no automático).")
        alavancagem = int(alavancagem)
        if not 1 <= alavancagem <= ALAVANCAGEM_MAX_BOT:
            raise ValueError(f"Alavancagem de bot é limitada a {ALAVANCAGEM_MAX_BOT}x.")
        direcoes = [str(d).upper() for d in (direcoes or [])]
        if not direcoes or any(d not in ("COMPRA", "VENDA") for d in direcoes):
            raise ValueError("Direções devem ser COMPRA e/ou VENDA.")
        dados.preco_atual(simbolo)  # valida que o par existe (levanta ValueError se não)

        bot = {
            "id": uuid.uuid4().hex[:8],
            "nome": (nome or f"{estrategia} {simbolo} {tf}").strip()[:40],
            "simbolo": simbolo, "tf": tf, "estrategia": estrategia,
            "limiar": limiar, "stop": stop, "alvo": alvo,
            "risco_pct": risco_pct, "alavancagem": alavancagem,
            "filtro_regime": bool(filtro_regime), "direcoes": direcoes,
            "gestao": gestao,        # fixo | breakeven | trailing | parcial
            "posicao_gestao": None,  # estado da gestão da posição aberta (persistido)
            "ativo": True, "criado_ms": _agora_ms(),
            "ultimo_candle_ms": 0,  # último candle já avaliado (evita avaliação dupla)
            "operacoes": 0,
            "jornal": [],
        }
        with self._trava:
            self.bots.append(bot)
            self._anotar(bot, "info",
                         "Bot criado — avalia o último candle fechado na próxima passada do motor "
                         f"(a cada {INTERVALO_MOTOR}s).")
            self._salvar()
        return dict(bot)

    def _achar(self, id_bot: str) -> dict:
        bot = next((b for b in self.bots if b["id"] == id_bot), None)
        if bot is None:
            raise ValueError(f"Bot {id_bot} não encontrado.")
        return bot

    def alternar(self, id_bot: str) -> dict:
        with self._trava:
            bot = self._achar(id_bot)
            bot["ativo"] = not bot["ativo"]
            self._anotar(bot, "info", "Bot retomado." if bot["ativo"] else
                         "Bot pausado (posições abertas continuam com stop/alvo ativos).")
            self._salvar()
            return dict(bot)

    def excluir(self, id_bot: str):
        with self._trava:
            bot = self._achar(id_bot)
            self.bots.remove(bot)
            self._salvar()

    def listar(self) -> list[dict]:
        """Snapshot dos bots com o jornal do mais novo para o mais antigo."""
        with self._trava:
            return [{**b, "jornal": list(reversed(b["jornal"][-15:]))} for b in self.bots]

    # ------------------------- motor -------------------------

    def iniciar(self):
        threading.Thread(target=self._laco, daemon=True, name="motor-bots").start()

    def _laco(self):
        while True:
            try:
                self._passada()
            except Exception:
                pass  # nunca derruba o motor; erros por bot já vão ao jornal
            time.sleep(INTERVALO_MOTOR)

    def _passada(self):
        # executa stop/alvo/liquidação pendentes mesmo sem nenhuma aba do navegador
        # aberta — com bots, o simulador precisa se bastar sozinho
        try:
            self.carteira.verificar_saidas()
        except Exception:
            pass
        self._gerir_posicoes()
        with self._trava:
            pendentes = [b for b in self.bots if b["ativo"]]
        mudou = False
        for bot in pendentes:
            minutos = _MINUTOS_TF[bot["tf"]]
            # início do candle corrente (alinhado ao relógio UTC, como a Binance)
            candle_atual = int(time.time() // (minutos * 60)) * minutos * 60 * 1000
            if bot["ultimo_candle_ms"] >= candle_atual:
                continue  # o último candle fechado deste timeframe já foi avaliado
            try:
                self._avaliar_e_operar(bot)
            except Exception as erro:
                self._anotar(bot, "erro", f"Falha na avaliação: {erro}")
            bot["ultimo_candle_ms"] = candle_atual
            mudou = True
        if mudou:
            self._salvar()

    # ------------------------- gestão de saída -------------------------
    # Validada no laboratório (RELATORIO-VARREDURA.md §5.1): trailing aprovou no
    # walk-forward 1d com folga; em configs de alvo curto (ex.: stop 2.0/alvo 1.0)
    # o gatilho de +1R fica além do alvo e a gestão raramente ativa (usar "fixo").

    def _gerir_posicoes(self):
        """Aplica breakeven/trailing/parcial às posições abertas pelos bots.

        Roda mesmo com o bot PAUSADO: pausar impede novas entradas, não abandona
        a posição já aberta."""
        mudou = False
        for bot in list(self.bots):
            if bot.get("gestao", "fixo") == "fixo":
                continue
            pos = next((p for p in self.carteira.posicoes
                        if p.get("bot") == bot["id"]), None)
            if pos is None:
                if bot.get("posicao_gestao") is not None:
                    bot["posicao_gestao"] = None  # posição fechou; limpa o estado
                    mudou = True
                continue
            try:
                mudou = self._gerir_uma(bot, pos) or mudou
            except Exception as erro:
                self._anotar(bot, "erro", f"Gestão da posição: {erro}")
        if mudou:
            self._salvar()

    def _gerir_uma(self, bot: dict, pos: dict) -> bool:
        """Gestão de UMA posição. Segue as mesmas regras do backtest:
        +1R -> breakeven (e parcial no modo parcial); trailing persegue o melhor
        preço a stop×ATR de distância depois do breakeven. Devolve True se algo
        do estado precisa ser persistido."""
        if not pos.get("stop"):
            return False  # sem stop inicial não existe o "R" de referência
        longa = pos["direcao"] == "COMPRA"
        mudou = False
        est = bot.get("posicao_gestao")
        if not est or est.get("id") != pos["id"]:
            risco0 = abs(pos["entrada"] - pos["stop"])
            est = {"id": pos["id"], "melhor": pos["entrada"], "risco0": risco0,
                   "atr0": risco0 / bot["stop"],  # ATR da entrada (stop = mult × ATR)
                   "be": False, "parcial": False, "stop_anotado": pos["stop"]}
            bot["posicao_gestao"] = est
            mudou = True

        preco = dados.preco_atual(pos["simbolo"])
        r, entrada = est["risco0"], pos["entrada"]
        alvo_1r = entrada + r if longa else entrada - r
        no_lucro_1r = (preco >= alvo_1r) if longa else (preco <= alvo_1r)

        # 1) PARCIAL: realiza metade quando o preço ESTÁ a +1R (semântica de ordem
        # limitada: só executa no nível, nunca abaixo dele)
        if bot["gestao"] == "parcial" and not est["parcial"] and no_lucro_1r:
            reg = self.carteira.fechar_parcial(pos["id"], 0.5)
            est["parcial"] = True
            mudou = True
            self._anotar(bot, "gestao",
                         f"Parcial de 50% a {reg['saida']:g} (+1R): {reg['resultado']:+.2f} "
                         "USDT — o resto corre até o alvo.")

        # 2) BREAKEVEN (breakeven/trailing/parcial): stop na entrada em +1R
        if bot["gestao"] in ("breakeven", "trailing", "parcial") and not est["be"] and no_lucro_1r:
            if self._mover_stop(pos, entrada):
                est["be"] = True
                est["stop_anotado"] = entrada
                mudou = True
                self._anotar(bot, "gestao",
                             f"+1R atingido — stop movido para a entrada ({entrada:g}): risco zerado.")

        # 3) TRAILING: após o breakeven, persegue o melhor preço a stop×ATR
        if bot["gestao"] == "trailing":
            melhor_antes = est["melhor"]
            est["melhor"] = max(est["melhor"], preco) if longa else min(est["melhor"], preco)
            if abs(est["melhor"] - melhor_antes) >= 0.2 * r:
                mudou = True  # persiste o topo/fundo (sobrevive a reinício do simulador)
            if est["be"]:
                dist = bot["stop"] * est["atr0"]
                novo = est["melhor"] - dist if longa else est["melhor"] + dist
                melhora = (novo > pos["stop"]) if longa else (novo < pos["stop"])
                if melhora and self._mover_stop(pos, novo):
                    mudou = True
                    # anota só em marcos (0,5×ATR) para não inundar o jornal
                    if abs(novo - est["stop_anotado"]) >= 0.5 * est["atr0"]:
                        self._anotar(bot, "gestao",
                                     f"Trailing: stop {novo:g} (melhor preço {est['melhor']:g}).")
                        est["stop_anotado"] = novo
        return mudou

    def _mover_stop(self, pos: dict, novo_stop: float) -> bool:
        """Move o stop via carteira. False se o preço atual não permite o nível
        agora (ex.: preço recuou abaixo dele) — tenta de novo na próxima passada."""
        try:
            self.carteira.editar(pos["id"], stop=novo_stop, alvo=pos["alvo"])
            return True
        except ValueError:
            return False

    # ------------------------- decisão de operação -------------------------

    def _avaliar_e_operar(self, bot: dict):
        simbolo, tf = bot["simbolo"], bot["tf"]
        df = adicionar_priceaction(adicionar_indicadores(
            dados.buscar_candles(simbolo, tf, 400, apenas_fechados=True)))
        if bot["estrategia"] == "fluxo":
            df = adicionar_fluxo(df, simbolo, tf)
        df_maior = adicionar_indicadores(
            dados.buscar_candles(simbolo, TIMEFRAME_CONTEXTO[tf], 400, apenas_fechados=True))
        diag = avaliar(df, df_maior, bot["estrategia"], bot["stop"], bot["alvo"])

        # direção com sinal válido (no empate de scores, o maior vence)
        candidatos = [(d, diag[f"score_{d.lower()}"]) for d in bot["direcoes"]
                      if diag[f"score_{d.lower()}"] >= bot["limiar"]]
        if not candidatos:
            self._anotar(bot, "sinal",
                         f"Sem sinal: compra {diag['score_compra']} / venda "
                         f"{diag['score_venda']} (limiar {bot['limiar']}).")
            return
        direcao, score = max(candidatos, key=lambda c: c[1])

        # guarda 1: filtro de regime
        if bot["filtro_regime"] and not (
                (direcao == "COMPRA" and diag["regime"] == "ALTA")
                or (direcao == "VENDA" and diag["regime"] == "BAIXA")):
            self._anotar(bot, "pulado",
                         f"{direcao} {score}/100, mas regime {diag['regime']} — "
                         "só opero a favor do regime.")
            return

        # guarda 2: uma posição por bot
        if any(p.get("bot") == bot["id"] for p in self.carteira.posicoes):
            self._anotar(bot, "pulado", f"{direcao} {score}/100, mas já tenho posição aberta.")
            return

        # stop/alvo por ATR com referência no preço AO VIVO (a entrada é a mercado,
        # como no backtest: sinal no candle fechado -> entrada no candle seguinte)
        preco = dados.preco_atual(simbolo)
        atr = float(diag["atr"])
        if not atr > 0:
            self._anotar(bot, "erro", "ATR indisponível — sem como posicionar o stop.")
            return
        if direcao == "COMPRA":
            stop, alvo = preco - bot["stop"] * atr, preco + bot["alvo"] * atr
        else:
            stop, alvo = preco + bot["stop"] * atr, preco - bot["alvo"] * atr

        # análise de risco da operação: margem para arriscar exatamente risco_pct%
        dim = self.carteira.dimensionar(simbolo, direcao, bot["risco_pct"],
                                        stop, bot["alavancagem"])
        margem, equity = dim["margem"], dim["equity"]
        if margem < 1:
            self._anotar(bot, "pulado", "Margem calculada menor que 1 USDT — saldo insuficiente.")
            return

        # guarda 4: exposição direcional total (cripto é correlacionada)
        exposicao = margem + sum(p["margem"] for p in self.carteira.posicoes
                                 if p["direcao"] == direcao)
        if equity > 0 and 100 * exposicao / equity > EXPOSICAO_MAX_PCT:
            self._anotar(bot, "pulado",
                         f"{direcao} {score}/100, mas a exposição em {direcao} passaria de "
                         f"{EXPOSICAO_MAX_PCT:.0f}% do capital.")
            return

        posicao = self.carteira.abrir(
            simbolo, direcao, margem, bot["alavancagem"], stop=stop, alvo=alvo,
            regime=diag["regime"], estrategia=bot["estrategia"], score=int(score),
            nota=f"bot {bot['nome']}", bot=bot["id"])
        with self._trava:
            bot["operacoes"] = bot.get("operacoes", 0) + 1
        risco_txt = (f"risco {dim['risco_usdt']:.2f} USDT ({bot['risco_pct']:g}% de "
                     f"{equity:,.0f})" + (" limitado pelo saldo" if dim["limitada_pelo_saldo"] else ""))
        avisos = "  ⚠ " + " • ".join(posicao["avisos"]) if posicao.get("avisos") else ""
        self._anotar(bot, "entrada",
                     f"{direcao} {score}/100 @ {posicao['entrada']:g} | stop {stop:g} | "
                     f"alvo {alvo:g} | margem {margem:.2f} ({bot['alavancagem']}x) | "
                     f"{risco_txt}.{avisos}")
