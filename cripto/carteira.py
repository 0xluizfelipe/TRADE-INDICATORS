"""Carteira de simulação (paper trading) com USDT fictício.

Regras da simulação — espelham o mercado de futuros real:
- Compra (long) e venda (short), alavancagem de 1x a 25x, margem isolada.
- Taxa de 0,05% do valor nocional na abertura e no fechamento (taker de futuros).
- Liquidação simplificada: quando o prejuízo consome a margem
  (preço de liquidação = entrada -/+ entrada/alavancagem).
- Stop, alvo e liquidação são verificados também RETROATIVAMENTE: ao consultar a
  conta, os candles desde a última verificação são percorridos e as saídas são
  executadas no nível correto, mesmo que o simulador tenha ficado fechado.
- Empate no mesmo candle resolve pelo pior caso (liquidação > stop > alvo).
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from . import dados

# A carteira NÃO pode ficar na pasta do projeto se ela estiver no OneDrive/Dropbox:
# a sincronização em nuvem pode reverter o arquivo enquanto o simulador grava,
# apagando suas operações. Por isso guardamos em uma pasta local não sincronizada.
_BASE_DADOS = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "analista-cripto"
_BASE_DADOS.mkdir(parents=True, exist_ok=True)
ARQUIVO = _BASE_DADOS / "carteira.json"
SALDO_INICIAL = 10_000.0
TAXA = 0.0005  # 0,05% por lado
ALAVANCAGEM_MAXIMA = 25

# --- Limites de gestão de risco (baseados na prática das mesas profissionais) ---
RISCO_MAX_PCT = 2.0        # risco recomendado por operação: 1–2% do capital
ALAVANCAGEM_ALERTA = 10    # liquidações de varejo concentram-se em 10x+
EXPOSICAO_MAX_PCT = 40.0   # exposição máx. recomendada na mesma direção (% do capital)

_trava = threading.Lock()


def _agora_ms() -> int:
    return int(time.time() * 1000)


def _intervalo_para_gap(minutos: float) -> tuple[str, int]:
    """Escolhe o timeframe mais fino possível para cobrir o período offline."""
    for intervalo, tam in (("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240)):
        candles = int(minutos / tam) + 3
        if candles <= 1000:
            return intervalo, candles
    return "1d", 1000


class Carteira:
    def __init__(self):
        self.carregar()

    # ------------------------- persistência -------------------------

    def carregar(self):
        if ARQUIVO.exists():
            estado = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        else:
            estado = {"saldo": SALDO_INICIAL, "posicoes": [], "historico": []}
        self.saldo: float = estado["saldo"]
        self.posicoes: list[dict] = estado["posicoes"]
        self.historico: list[dict] = estado["historico"]

    def salvar(self):
        estado = {"saldo": self.saldo, "posicoes": self.posicoes, "historico": self.historico}
        # Gravação atômica: escreve em arquivo temporário e substitui de uma vez,
        # para nunca deixar o carteira.json pela metade se algo interromper.
        temporario = ARQUIVO.with_suffix(".tmp")
        temporario.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
        temporario.replace(ARQUIVO)

    def resetar(self):
        with _trava:
            self.saldo = SALDO_INICIAL
            self.posicoes = []
            self.historico = []
            self.salvar()

    # ------------------------- operações -------------------------

    def _equity_base(self) -> float:
        """Capital de referência (custo): saldo livre + margens presas nas posições."""
        return self.saldo + sum(p["margem"] for p in self.posicoes)

    def abrir(self, simbolo: str, direcao: str, margem: float, alavancagem: int,
              stop: float | None = None, alvo: float | None = None,
              regime: str | None = None, estrategia: str | None = None,
              score: int | None = None) -> dict:
        simbolo = simbolo.upper()
        direcao = direcao.upper()
        if direcao not in ("COMPRA", "VENDA"):
            raise ValueError("Direção deve ser COMPRA ou VENDA.")
        alavancagem = int(alavancagem)
        if not 1 <= alavancagem <= ALAVANCAGEM_MAXIMA:
            raise ValueError(f"Alavancagem deve estar entre 1 e {ALAVANCAGEM_MAXIMA}x.")
        if margem <= 0:
            raise ValueError("Margem deve ser maior que zero.")
        # GUARDRAIL nº1: alavancagem sem stop é a forma mais rápida de quebrar a conta.
        if alavancagem > 1 and stop is None:
            raise ValueError(
                f"Posição alavancada ({alavancagem}x) exige stop loss. "
                "Defina um stop (use 'Sugerir stop/alvo') antes de abrir — "
                "operar alavancado sem stop é a causa nº1 de liquidação.")

        preco = dados.preco_atual(simbolo)
        quantidade = margem * alavancagem / preco
        taxa_abertura = TAXA * quantidade * preco

        with _trava:
            if margem + taxa_abertura > self.saldo:
                raise ValueError(
                    f"Saldo insuficiente: precisa de {margem + taxa_abertura:.2f} USDT "
                    f"(margem + taxa), disponível {self.saldo:.2f}.")

            if direcao == "COMPRA":
                liquidacao = preco * (1 - 1 / alavancagem)
                if stop is not None and not 0 < stop < preco:
                    raise ValueError("Para COMPRA o stop deve ficar abaixo do preço atual.")
                if alvo is not None and alvo <= preco:
                    raise ValueError("Para COMPRA o alvo deve ficar acima do preço atual.")
            else:
                liquidacao = preco * (1 + 1 / alavancagem)
                if stop is not None and stop <= preco:
                    raise ValueError("Para VENDA o stop deve ficar acima do preço atual.")
                if alvo is not None and not 0 < alvo < preco:
                    raise ValueError("Para VENDA o alvo deve ficar abaixo do preço atual.")

            # --- Avaliação de risco (avisa, mas não bloqueia, exceto o stop acima) ---
            equity = self._equity_base()
            avisos: list[str] = []
            risco_pct = None
            if stop is not None:
                risco_pct = 100 * quantidade * abs(preco - stop) / equity if equity > 0 else 0
                if risco_pct > RISCO_MAX_PCT:
                    avisos.append(
                        f"Risco de {risco_pct:.1f}% do capital nesta operação "
                        f"(o recomendado é até {RISCO_MAX_PCT:.0f}%).")
            if alavancagem >= ALAVANCAGEM_ALERTA:
                mov = 100 / alavancagem
                avisos.append(
                    f"Alavancagem alta ({alavancagem}x): um movimento de ~{mov:.1f}% "
                    "contra você já liquida a posição.")
            exposicao = margem + sum(p["margem"] for p in self.posicoes
                                     if p["direcao"] == direcao)
            exp_pct = 100 * exposicao / equity if equity > 0 else 0
            mesma_direcao = sum(1 for p in self.posicoes if p["direcao"] == direcao)
            if exp_pct > EXPOSICAO_MAX_PCT and mesma_direcao >= 1:
                avisos.append(
                    f"Exposição de {exp_pct:.0f}% do capital em {mesma_direcao + 1} posições "
                    f"de {direcao} — cripto é correlacionada, então elas tendem a ganhar "
                    "ou perder juntas (é quase uma aposta só, maior do que parece).")

            # operar contra o regime (comprar em tendência de baixa, ou vender em alta)
            contra_regime = ((direcao == "COMPRA" and regime == "BAIXA")
                             or (direcao == "VENDA" and regime == "ALTA"))
            if contra_regime:
                avisos.append(
                    f"Operação CONTRA o regime ({direcao} com mercado em {regime}) — "
                    "ir contra a tendência costuma sair caro.")

            posicao = {
                "id": uuid.uuid4().hex[:8],
                "simbolo": simbolo,
                "direcao": direcao,
                "margem": margem,
                "alavancagem": alavancagem,
                "entrada": preco,
                "quantidade": quantidade,
                "stop": stop,
                "alvo": alvo,
                "liquidacao": liquidacao,
                "taxa_paga": taxa_abertura,
                "risco_pct": risco_pct,
                # campos do diário/coach de disciplina
                "tinha_stop": stop is not None,
                "regime": regime or "?",
                "contra_regime": contra_regime,
                "estrategia": estrategia or "manual",
                "score": score,
                "abertura_ms": _agora_ms(),
                "ultimo_check_ms": _agora_ms(),
            }
            self.saldo -= margem + taxa_abertura
            self.posicoes.append(posicao)
            self.salvar()
            return {**posicao, "avisos": avisos}

    def dimensionar(self, simbolo: str, direcao: str, risco_pct: float,
                    stop: float, alavancagem: int) -> dict:
        """Calcula a margem para arriscar exatamente `risco_pct`% do capital até o stop.

        É a forma profissional de dimensionar: você decide quanto pode perder (risco),
        e o tamanho da posição decorre disso — não o contrário.
        """
        direcao = direcao.upper()
        alavancagem = int(alavancagem)
        if not 1 <= alavancagem <= ALAVANCAGEM_MAXIMA:
            raise ValueError(f"Alavancagem deve estar entre 1 e {ALAVANCAGEM_MAXIMA}x.")
        if risco_pct <= 0:
            raise ValueError("O risco por operação deve ser maior que zero.")
        preco = dados.preco_atual(simbolo)
        if direcao == "COMPRA" and not 0 < stop < preco:
            raise ValueError("Para COMPRA o stop deve ficar abaixo do preço atual.")
        if direcao == "VENDA" and stop <= preco:
            raise ValueError("Para VENDA o stop deve ficar acima do preço atual.")

        with _trava:
            equity = self._equity_base()
            saldo_livre = self.saldo
        risco_usdt = risco_pct / 100 * equity
        risco_unitario = abs(preco - stop)
        quantidade = risco_usdt / risco_unitario
        margem = quantidade * preco / alavancagem
        limitada = margem > saldo_livre
        if limitada:  # não dá pra arriscar tanto com o saldo atual
            margem = saldo_livre * 0.98
        return {
            "preco": preco, "margem": round(margem, 2), "risco_usdt": round(risco_usdt, 2),
            "risco_pct": risco_pct, "equity": round(equity, 2), "limitada_pelo_saldo": limitada,
        }

    def _encerrar(self, posicao: dict, preco_saida: float, motivo: str, quando_ms: int):
        sinal = 1 if posicao["direcao"] == "COMPRA" else -1
        pnl_bruto = posicao["quantidade"] * (preco_saida - posicao["entrada"]) * sinal
        taxa_fechamento = TAXA * posicao["quantidade"] * preco_saida
        if motivo == "LIQUIDACAO":
            devolvido = 0.0  # margem inteira perdida
            pnl_bruto = -posicao["margem"]
            taxa_fechamento = 0.0
        else:
            devolvido = max(0.0, posicao["margem"] + pnl_bruto - taxa_fechamento)
        resultado = devolvido - posicao["margem"] - posicao["taxa_paga"]

        self.saldo += devolvido
        self.posicoes = [p for p in self.posicoes if p["id"] != posicao["id"]]
        registro = {
            "id": posicao["id"],
            "simbolo": posicao["simbolo"],
            "direcao": posicao["direcao"],
            "alavancagem": posicao["alavancagem"],
            "margem": posicao["margem"],
            "entrada": posicao["entrada"],
            "saida": preco_saida,
            "motivo": motivo,
            "resultado": resultado,
            # campos do diário/coach (records antigos podem não ter; o diário trata isso)
            "tinha_stop": posicao.get("tinha_stop"),
            "regime": posicao.get("regime", "?"),
            "contra_regime": posicao.get("contra_regime"),
            "estrategia": posicao.get("estrategia", "manual"),
            "score": posicao.get("score"),
            "abertura_ms": posicao["abertura_ms"],
            "fechamento_ms": quando_ms,
        }
        self.historico.append(registro)
        return registro

    def fechar(self, id_posicao: str) -> dict:
        with _trava:
            posicao = next((p for p in self.posicoes if p["id"] == id_posicao), None)
            if posicao is None:
                raise ValueError(f"Posição {id_posicao} não encontrada.")
            preco = dados.preco_atual(posicao["simbolo"])
            registro = self._encerrar(posicao, preco, "MANUAL", _agora_ms())
            self.salvar()
            return registro

    # ------------------------- saídas automáticas -------------------------

    def _nivel_atingido(self, posicao: dict, minima: float, maxima: float):
        """Verifica liquidação, stop e alvo num candle — pior caso primeiro."""
        compra = posicao["direcao"] == "COMPRA"
        toca_baixo = lambda nivel: nivel is not None and minima <= nivel
        toca_cima = lambda nivel: nivel is not None and maxima >= nivel
        if compra:
            if toca_baixo(posicao["liquidacao"]) and posicao["alavancagem"] > 1:
                return "LIQUIDACAO", posicao["liquidacao"]
            if toca_baixo(posicao["stop"]):
                return "STOP", posicao["stop"]
            if toca_cima(posicao["alvo"]):
                return "ALVO", posicao["alvo"]
        else:
            if toca_cima(posicao["liquidacao"]):
                return "LIQUIDACAO", posicao["liquidacao"]
            if toca_cima(posicao["stop"]):
                return "STOP", posicao["stop"]
            if toca_baixo(posicao["alvo"]):
                return "ALVO", posicao["alvo"]
        return None

    def _saida_por_candles(self, posicao: dict, agora: int) -> bool:
        """Reconstrói o período desde a última verificação candle a candle.

        Cobre o tempo em que o simulador ficou fechado: percorre os candles e,
        no primeiro que atinge um nível, encerra a posição no preço correto.
        Retorna True se a posição foi encerrada.
        """
        minutos = (agora - posicao["ultimo_check_ms"]) / 60_000
        if minutos < 2:
            return False  # gap curto: o preço ao vivo já resolve, sem custo de baixar candles
        intervalo, n_candles = _intervalo_para_gap(minutos)
        try:
            candles = dados.buscar_candles(posicao["simbolo"], intervalo, n_candles)
        except Exception:
            return False  # sem rede; o preço ao vivo (ou a próxima consulta) tenta de novo
        inicio = posicao["ultimo_check_ms"]
        recentes = candles[candles.index.view("int64") // 10**6 >= inicio - 60_000]
        for data, candle in recentes.iterrows():
            atingido = self._nivel_atingido(posicao, candle["minima"], candle["maxima"])
            if atingido:
                motivo, nivel = atingido
                self._encerrar(posicao, nivel, motivo, int(data.value // 10**6))
                return True
        return False

    def verificar_saidas(self):
        """Executa stop/alvo/liquidação. Roda a CADA consulta da conta.

        Duas camadas: (1) reconstrução por candles para o tempo offline e
        (2) checagem do preço ao vivo agora — esta funciona mesmo com a página
        atualizando de poucos em poucos segundos, que antes era ignorada.
        """
        with _trava:
            agora = _agora_ms()
            houve_mudanca = False
            precos: dict[str, float] = {}
            for posicao in list(self.posicoes):
                if self._saida_por_candles(posicao, agora):
                    houve_mudanca = True
                    continue
                simbolo = posicao["simbolo"]
                try:
                    if simbolo not in precos:
                        precos[simbolo] = dados.preco_atual(simbolo)
                except Exception:
                    continue  # sem rede agora; mantém o relógio para reavaliar depois
                atingido = self._nivel_atingido(posicao, precos[simbolo], precos[simbolo])
                if atingido:
                    motivo, nivel = atingido
                    self._encerrar(posicao, nivel, motivo, agora)
                    houve_mudanca = True
                else:
                    posicao["ultimo_check_ms"] = agora
            if houve_mudanca or self.posicoes:
                self.salvar()

    # ------------------------- consulta -------------------------

    def estado(self) -> dict:
        self.verificar_saidas()
        with _trava:
            precos = {}
            for posicao in self.posicoes:
                simbolo = posicao["simbolo"]
                if simbolo not in precos:
                    try:
                        precos[simbolo] = dados.preco_atual(simbolo)
                    except Exception:
                        precos[simbolo] = posicao["entrada"]

            posicoes_abertas = []
            patrimonio = self.saldo
            for posicao in self.posicoes:
                preco = precos[posicao["simbolo"]]
                sinal = 1 if posicao["direcao"] == "COMPRA" else -1
                pnl = posicao["quantidade"] * (preco - posicao["entrada"]) * sinal
                pnl = max(pnl, -posicao["margem"])
                patrimonio += posicao["margem"] + pnl
                posicoes_abertas.append({
                    **posicao,
                    "preco_atual": preco,
                    "pnl": pnl,
                    "pnl_pct": 100 * pnl / posicao["margem"],
                })

            fechadas = sorted(self.historico, key=lambda r: r["fechamento_ms"], reverse=True)
            vitorias = sum(1 for r in self.historico if r["resultado"] > 0)
            return {
                "saldo": self.saldo,
                "patrimonio": patrimonio,
                "saldo_inicial": SALDO_INICIAL,
                "retorno_pct": 100 * (patrimonio / SALDO_INICIAL - 1),
                "posicoes": posicoes_abertas,
                "historico": fechadas[:30],
                "total_fechadas": len(self.historico),
                "taxa_acerto": 100 * vitorias / len(self.historico) if self.historico else 0,
            }

    def diario(self) -> dict:
        """Coach de disciplina: analisa o histórico e aponta os SEUS padrões de erro."""
        with _trava:
            hist = list(self.historico)

        def resumo(registros):
            n = len(registros)
            soma = sum(r["resultado"] for r in registros)
            vit = sum(1 for r in registros if r["resultado"] > 0)
            return {"n": n, "total": round(soma, 2),
                    "acerto": round(100 * vit / n, 1) if n else 0}

        sem_stop = [r for r in hist if r.get("tinha_stop") is False]
        com_stop = [r for r in hist if r.get("tinha_stop") is True]
        contra = [r for r in hist if r.get("contra_regime") is True]
        a_favor = [r for r in hist if r.get("contra_regime") is False]
        liquidacoes = [r for r in hist if r.get("motivo") == "LIQUIDACAO"]
        alav_alta = [r for r in hist if r.get("alavancagem", 1) >= ALAVANCAGEM_ALERTA]

        licoes = []
        if sem_stop:
            r = resumo(sem_stop)
            licoes.append(f"Você abriu {r['n']} operação(ões) SEM stop, somando "
                          f"{r['total']:+.2f} USDT. Stop em toda operação não é opcional.")
        if liquidacoes:
            custo = sum(r["resultado"] for r in liquidacoes)
            licoes.append(f"{len(liquidacoes)} liquidação(ões) custaram {custo:+.2f} USDT — "
                          "cada uma é uma perda total da margem que um stop teria limitado.")
        if contra and a_favor:
            rc, rf = resumo(contra), resumo(a_favor)
            licoes.append(f"Contra o regime: {rc['acerto']:.0f}% de acerto ({rc['total']:+.2f} USDT). "
                          f"A favor do regime: {rf['acerto']:.0f}% ({rf['total']:+.2f} USDT). "
                          "Operar a favor da tendência tende a render mais.")
        if alav_alta:
            r = resumo(alav_alta)
            licoes.append(f"Operações com 10x+ : {r['n']}, resultado {r['total']:+.2f} USDT "
                          f"({r['acerto']:.0f}% de acerto). Alavancagem amplia o erro, não a habilidade.")
        if not hist:
            licoes.append("Sem operações fechadas ainda — o coach aparece conforme você opera.")
        elif not licoes:
            licoes.append("Disciplina em dia: stops definidos, sem liquidações, a favor do regime. 👏")

        return {
            "total": len(hist),
            "sem_stop": resumo(sem_stop), "com_stop": resumo(com_stop),
            "contra_regime": resumo(contra), "a_favor_regime": resumo(a_favor),
            "liquidacoes": {"n": len(liquidacoes),
                            "custo": round(sum(r["resultado"] for r in liquidacoes), 2)},
            "alavancagem_alta": resumo(alav_alta),
            "licoes": licoes,
        }
