@echo off
title Simulador Brother MFC-L8610CDW - Painel de Controle
cd /d "%~dp0"

where poetry >nul 2>nul
if %ERRORLEVEL% equ 0 (
    poetry run python simulador_brother.py
) else (
    python simulador_brother.py
)
pause
