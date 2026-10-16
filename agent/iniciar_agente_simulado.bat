@echo off
title Agente de Impressao (Modo Simulador) - EE Antonio Reginato
cd /d "%~dp0"

where poetry >nul 2>nul
if %ERRORLEVEL% equ 0 (
    poetry run python agente_fila_impressao.py --loop --simulador
) else (
    python agente_fila_impressao.py --loop --simulador
)
pause
