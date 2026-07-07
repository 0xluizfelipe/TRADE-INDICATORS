"""Pacote de análise técnica de criptomoedas."""

# Timeframe de CONTEXTO usado por cada timeframe operado (análise multi-timeframe).
# Definido uma única vez aqui — CLI, laboratório, simulador e scripts importam daqui.
TIMEFRAME_CONTEXTO = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
