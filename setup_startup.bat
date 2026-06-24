@echo off
REM ============================================================
REM  setup_startup.bat — Installe le bot au démarrage Windows
REM  À exécuter UNE SEULE FOIS en tant qu'administrateur
REM ============================================================

cd /d "%~dp0"

echo ============================================================
echo   INSTALLATION DU TRADING SYSTEM AU DÉMARRAGE WINDOWS
echo ============================================================
echo.

REM 1. Installer les dépendances Python
echo [1/3] Installation des dépendances Python...
pip install yfinance pandas numpy requests pytz tzdata
echo.

REM 2. Créer le dossier dashboard
echo [2/3] Création du dossier dashboard...
if not exist "dashboard" mkdir dashboard
echo.

REM 3. Créer le raccourci dans le dossier Démarrage Windows
echo [3/3] Ajout au démarrage Windows...

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SCRIPT_DIR=%~dp0
set BAT_FILE=%SCRIPT_DIR%launch.bat

REM Créer un fichier .vbs pour lancer sans fenêtre noire
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%STARTUP%\TradingSystem.vbs"
echo sFile = "%BAT_FILE%" >> "%STARTUP%\TradingSystem.vbs"
echo oWS.Run sFile, 0, False >> "%STARTUP%\TradingSystem.vbs"

echo.
echo ============================================================
echo   INSTALLATION TERMINÉE
echo ============================================================
echo.
echo Le Trading System démarrera automatiquement au prochain
echo démarrage du PC.
echo.
echo Pour démarrer maintenant : double-clic sur launch.bat
echo Pour piloter depuis Telegram : /help
echo.
pause
