"""Coleta de dados de mercado pela API pública da Binance (não precisa de conta nem chave)."""

import time

import pandas as pd
import requests

BASE_URL = "https://api.binance.com/api/v3"

# Tokens alavancados e stablecoins que não fazem sentido analisar
_IGNORAR_SUFIXOS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
_IGNORAR_PARES = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
    "EURUSDT", "USDPUSDT", "AEURUSDT", "PAXGUSDT", "EURIUSDT", "XUSDUSDT",
    "USD1USDT", "RLUSDUSDT", "USDEUSDT", "USDSUSDT",
}

_sessao = requests.Session()


def _get(caminho: str, params: dict | None = None) -> object:
    """Faz uma requisição GET com até 3 tentativas."""
    ultimo_erro = None
    for tentativa in range(3):
        try:
            resposta = _sessao.get(f"{BASE_URL}/{caminho}", params=params, timeout=20)
            if resposta.status_code == 429:  # limite de requisições: espera e tenta de novo
                time.sleep(5 * (tentativa + 1))
                continue
            resposta.raise_for_status()
            return resposta.json()
        except requests.RequestException as erro:
            ultimo_erro = erro
            time.sleep(1 + tentativa)
    raise ConnectionError(f"Falha ao acessar a Binance ({caminho}): {ultimo_erro}")


def buscar_candles(simbolo: str, intervalo: str = "4h", limite: int = 500) -> pd.DataFrame:
    """Baixa candles (OHLCV) de um par. Pagina automaticamente se limite > 1000.

    Retorna DataFrame indexado pela data de abertura do candle, com colunas:
    abertura, maxima, minima, fechamento, volume.
    """
    todos: list[list] = []
    fim = None
    restante = limite
    while restante > 0:
        params = {"symbol": simbolo.upper(), "interval": intervalo, "limit": min(restante, 1000)}
        if fim is not None:
            params["endTime"] = fim
        lote = _get("klines", params)
        if not lote:
            break
        todos = lote + todos
        restante -= len(lote)
        if len(lote) < params["limit"]:
            break  # chegou no início do histórico
        fim = lote[0][0] - 1  # busca o bloco anterior

    if not todos:
        raise ValueError(f"Nenhum dado retornado para {simbolo} ({intervalo}). O par existe?")

    df = pd.DataFrame(todos, columns=[
        "tempo_abertura", "abertura", "maxima", "minima", "fechamento", "volume",
        "tempo_fechamento", "volume_quote", "trades", "taker_base", "taker_quote", "ignorar",
    ])
    df["data"] = pd.to_datetime(df["tempo_abertura"], unit="ms", utc=True)
    df = df.set_index("data")[["abertura", "maxima", "minima", "fechamento", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def melhores_pares_usdt(quantidade: int = 50) -> list[str]:
    """Retorna os pares USDT com maior volume nas últimas 24h."""
    tickers = _get("ticker/24hr")
    pares = [
        t for t in tickers
        if t["symbol"].endswith("USDT")
        and t["symbol"] not in _IGNORAR_PARES
        and not t["symbol"].endswith(_IGNORAR_SUFIXOS)
    ]
    pares.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in pares[:quantidade]]


def preco_atual(simbolo: str) -> float:
    """Último preço negociado do par."""
    return float(_get("ticker/price", {"symbol": simbolo.upper()})["price"])
