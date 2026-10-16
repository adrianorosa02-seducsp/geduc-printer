@echo off
title Agente de Impressao - EE Antonio Reginato
cd /d "%~dp0"

where poetry >nul 2>nul
if %ERRORLEVEL% equ 0 (
    poetry run python agente_fila_impressao.py --loop %*
) else (
    python agente_fila_impressao.py --loop %*
)
pause

