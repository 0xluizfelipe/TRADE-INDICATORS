"""Coleta de dados de mercado pela API pública da Binance (não precisa de conta nem chave)."""

import time

import pandas as pd
import requests

BASE_URL = "https://api.binance.com/api/v3"
FUTUROS_URL = "https://fapi.binance.com/fapi/v1"

# Tokens alavancados e stablecoins que não fazem sentido analisar
_IGNORAR_SUFIXOS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
_IGNORAR_PARES = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
    "EURUSDT", "USDPUSDT", "AEURUSDT", "PAXGUSDT", "EURIUSDT", "XUSDUSDT",
    "USD1USDT", "RLUSDUSDT", "USDEUSDT", "USDSUSDT",
}

_sessao = requests.Session()

_MINUTOS_INTERVALO = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080,
}


def _get(caminho: str, params: dict | None = None, base: str = BASE_URL) -> object:
    """Faz uma requisição GET com até 3 tentativas."""
    ultimo_erro = None
    for tentativa in range(3):
        try:
            resposta = _sessao.get(f"{base}/{caminho}", params=params, timeout=20)
            if resposta.status_code == 429:  # limite de requisições: espera e tenta de novo
                time.sleep(5 * (tentativa + 1))
                continue
            resposta.raise_for_status()
            return resposta.json()
        except requests.RequestException as erro:
            ultimo_erro = erro
            time.sleep(1 + tentativa)
    raise ConnectionError(f"Falha ao acessar a Binance ({caminho}): {ultimo_erro}")


def buscar_candles(simbolo: str, intervalo: str = "4h", limite: int = 500,
                   apenas_fechados: bool = False) -> pd.DataFrame:
    """Baixa candles (OHLCV) de um par. Pagina automaticamente se limite > 1000.

    Retorna DataFrame indexado pela data de abertura do candle, com colunas:
    abertura, maxima, minima, fechamento, volume.

    `apenas_fechados=True` descarta o candle em formação: análise e backtest devem
    olhar apenas candles fechados (o sinal do candle aberto ainda pode mudar —
    "repaint"). O gráfico ao vivo usa False para mostrar o candle atual.
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
    # trades e taker_base (volume de compra agressiva) alimentam a análise de FLUXO
    # de ordens — dados que a Binance já entrega em cada candle.
    df = df.set_index("data")[[
        "abertura", "maxima", "minima", "fechamento", "volume", "trades", "taker_base",
    ]].astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()

    if apenas_fechados and len(df):
        minutos = _MINUTOS_INTERVALO.get(intervalo)
        if minutos:
            fim_ultimo = df.index[-1] + pd.Timedelta(minutes=minutos)
            if fim_ultimo > pd.Timestamp.now(tz="UTC"):
                df = df.iloc[:-1]
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


# Cache curto do preço: várias posições/consultas no mesmo segundo reutilizam a
# mesma cotação em vez de ir à Binance a cada chamada (o simulador consulta a
# conta a cada poucos segundos).
_cache_precos: dict[str, tuple[float, float]] = {}
_VALIDADE_PRECO = 2.0  # segundos


def buscar_funding(simbolo: str, inicio_ms: int) -> pd.Series:
    """Histórico do funding rate do contrato perpétuo (um registro a cada 8h).

    Funding é dado de POSICIONAMENTO (quanto os alavancados pagam para manter a
    posição), independente do preço — extremos indicam multidão de um lado só.
    Devolve uma Série indexada pelo horário do funding, com folga de 40 dias antes
    de `inicio_ms` para o aquecimento do percentil móvel. Levanta ValueError se o
    par não tem perpétuo.
    """
    registros: list[dict] = []
    fim = None
    for _ in range(30):  # teto de segurança (~30k registros = 27 anos)
        params: dict = {"symbol": simbolo.upper(), "limit": 1000}
        if fim is not None:
            params["endTime"] = fim
        lote = _get("fundingRate", params, base=FUTUROS_URL)
        if not lote:
            break
        registros = lote + registros
        primeiro = int(lote[0]["fundingTime"])
        if primeiro <= inicio_ms - 40 * 86_400_000 or len(lote) < 1000:
            break
        fim = primeiro - 1
    if not registros:
        raise ValueError(f"{simbolo}: sem histórico de funding (par sem perpétuo?)")
    serie = pd.Series(
        {pd.to_datetime(int(r["fundingTime"]), unit="ms", utc=True): float(r["fundingRate"])
         for r in registros}).sort_index()
    corte = pd.to_datetime(inicio_ms, unit="ms", utc=True) - pd.Timedelta(days=40)
    return serie[serie.index >= corte]


def preco_atual(simbolo: str) -> float:
    """Último preço negociado do par (cache de 2 s)."""
    simbolo = simbolo.upper()
    agora = time.time()
    em_cache = _cache_precos.get(simbolo)
    if em_cache and agora - em_cache[0] < _VALIDADE_PRECO:
        return em_cache[1]
    preco = float(_get("ticker/price", {"symbol": simbolo})["price"])
    _cache_precos[simbolo] = (agora, preco)
    return preco
