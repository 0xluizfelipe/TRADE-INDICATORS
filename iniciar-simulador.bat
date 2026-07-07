@echo off
rem Mantem o simulador (e os bots) sempre no ar: se o servidor cair, volta
rem sozinho em 5 segundos. Para PARAR de vez, feche esta janela.
cd /d "%~dp0"
title Analisador Cripto - Simulador
python simulador.py
:reiniciar
echo.
echo Simulador encerrado - reiniciando em 5 segundos...
echo (para parar de vez, feche esta janela)
timeout /t 5 /nobreak >nul
python simulador.py --sem-navegador
goto reiniciar
