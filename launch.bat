@echo off
REM ============================================================
REM  launch.bat — Lance le controller Telegram en arrière-plan
REM  Double-clic ou appelé au démarrage Windows
REM ============================================================

cd /d "%~dp0"

echo Lancement du Trading System...

REM Lance le controller Telegram (gère le start/stop des bots)
start "" pythonw telegram_controller.py

echo Controller Telegram démarré.
echo Pilote tes bots depuis Telegram avec /help
timeout /t 3 >nul
