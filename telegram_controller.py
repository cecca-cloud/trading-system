"""
=============================================================
  TELEGRAM CONTROLLER — Pilotage à distance des bots
=============================================================
Commandes disponibles :
  /start_trading   → lance le bot tendance haussière
  /start_macro     → lance le bot macro
  /start_daytrading→ lance le bot day trading
  /stop_all        → arrête tous les bots
  /stop <nom>      → arrête un bot spécifique
  /status          → état de tous les bots
  /scan            → force un scan immédiat
=============================================================
"""

import subprocess
import requests
import logging
import json
import os
import sys
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
#  CONFIGURATION — tokens dans config.py
# ─────────────────────────────────────────────
try:
    from config import TELEGRAM_BOT_TOKEN_CONTROLLER, TELEGRAM_CHAT_ID
except ImportError:
    raise SystemExit("❌ Fichier config.py manquant — crée-le avec tes tokens.")

TELEGRAM_TOKEN   = TELEGRAM_BOT_TOKEN_CONTROLLER

# Définition des bots gérés
BOTS = {
    "trading":    {"script": "trading_bot.py",    "label": "📈 Bot Tendance Haussière"},
    "macro":      {"script": "macro_bot.py",       "label": "🌍 Bot Macro"},
    "daytrading": {"script": "daytrading_bot.py",  "label": "⚡ Bot Day Trading"},
}

POLL_INTERVAL = 2    # secondes entre deux polls Telegram
LOG_FILE      = "controller.log"

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  ÉTAT DES PROCESSUS
# ─────────────────────────────────────────────
processes: dict[str, subprocess.Popen] = {}
crash_counts: dict[str, int] = {}
MAX_AUTO_RESTARTS = 3   # au-delà, on abandonne et on prévient

CRASH_LOG_DIR = "crash_logs"
os.makedirs(CRASH_LOG_DIR, exist_ok=True)


def is_running(name: str) -> bool:
    proc = processes.get(name)
    return proc is not None and proc.poll() is None


def start_bot(name: str, manual: bool = True) -> str:
    if name not in BOTS:
        return f"❌ Bot inconnu : {name}"
    if is_running(name):
        return f"⚠️ {BOTS[name]['label']} tourne déjà."

    script = BOTS[name]["script"]
    if not os.path.exists(script):
        return f"❌ Fichier introuvable : {script}"

    if manual:
        crash_counts[name] = 0   # reset le compteur sur une demande manuelle

    try:
        err_path = os.path.join(CRASH_LOG_DIR, f"{name}_stderr.log")
        err_file = open(err_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", script],
            stdout=err_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes[name] = proc
        log.info(f"Bot '{name}' démarré (PID {proc.pid})")
        return f"✅ {BOTS[name]['label']} démarré (PID {proc.pid})"
    except Exception as exc:
        return f"❌ Erreur démarrage {name}: {exc}"


def stop_bot(name: str) -> str:
    if name not in BOTS:
        return f"❌ Bot inconnu : {name}"
    proc = processes.get(name)
    if proc is None or proc.poll() is not None:
        return f"⚠️ {BOTS[name]['label']} n'est pas actif."
    try:
        proc.terminate()
        proc.wait(timeout=5)
        log.info(f"Bot '{name}' arrêté.")
        return f"⏹ {BOTS[name]['label']} arrêté."
    except Exception as exc:
        proc.kill()
        return f"⏹ {BOTS[name]['label']} tué de force ({exc})"


def stop_all() -> str:
    if not processes:
        return "⚠️ Aucun bot actif."
    results = [stop_bot(name) for name in list(processes.keys())]
    return "\n".join(results)


def status_all() -> str:
    now  = datetime.now(NY).strftime("%Y-%m-%d %H:%M")
    lines = [f"📊 <b>Status des bots</b> — {now}"]
    for name, info in BOTS.items():
        icon = "🟢" if is_running(name) else "🔴"
        proc = processes.get(name)
        pid  = f" (PID {proc.pid})" if proc and is_running(name) else ""
        lines.append(f"{icon} {info['label']}{pid}")
    return "\n".join(lines)


def force_scan() -> str:
    """Envoie SIGUSR1 ou crée un fichier trigger pour forcer un scan."""
    # Solution simple : créer un fichier trigger que le bot détecte
    trigger = "force_scan.trigger"
    with open(trigger, "w") as f:
        f.write(datetime.now(NY).isoformat())
    return "🔄 Scan forcé déclenché — résultats dans quelques instants."


def help_message() -> str:
    return (
        "🤖 <b>Commandes disponibles</b>\n\n"
        "/start_trading    — Lance le bot tendance haussière\n"
        "/start_macro      — Lance le bot macro\n"
        "/start_daytrading — Lance le bot day trading\n"
        "/start_all        — Lance tous les bots\n"
        "/stop_all         — Arrête tous les bots\n"
        "/stop trading     — Arrête un bot spécifique\n"
        "/status           — État de tous les bots\n"
        "/scan             — Force un scan immédiat\n"
        "/logs <bot>       — Voir les dernières erreurs d'un bot\n"
        "/help             — Cette aide"
    )


# ─────────────────────────────────────────────
#  TELEGRAM API
# ─────────────────────────────────────────────
def send(text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        log.warning(f"Send error: {exc}")


def get_updates(offset: int) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        return resp.json().get("result", [])
    except Exception:
        return []


def get_logs(name: str) -> str:
    if name not in BOTS:
        return f"❌ Bot inconnu : {name}"
    err_path = os.path.join(CRASH_LOG_DIR, f"{name}_stderr.log")
    if not os.path.exists(err_path):
        return f"📄 Aucun log pour {BOTS[name]['label']} pour l'instant."
    try:
        with open(err_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-20:]) or "(vide)"
        return f"📄 <b>Logs — {BOTS[name]['label']}</b>\n<code>{tail[-800:]}</code>"
    except Exception as exc:
        return f"❌ Erreur lecture log: {exc}"


# ─────────────────────────────────────────────
#  ROUTEUR DE COMMANDES
# ─────────────────────────────────────────────
def handle_command(text: str) -> str:
    text = text.strip().lower()

    if text in ("/start", "/help"):
        return help_message()

    elif text == "/status":
        return status_all()

    elif text == "/start_trading":
        return start_bot("trading")

    elif text == "/start_macro":
        return start_bot("macro")

    elif text == "/start_daytrading":
        return start_bot("daytrading")

    elif text == "/start_all":
        results = [start_bot(name) for name in BOTS]
        return "\n".join(results)

    elif text == "/stop_all":
        return stop_all()

    elif text.startswith("/stop "):
        name = text.replace("/stop ", "").strip()
        return stop_bot(name)

    elif text == "/scan":
        return force_scan()

    elif text.startswith("/logs"):
        parts = text.split()
        name = parts[1] if len(parts) > 1 else "trading"
        return get_logs(name)

    else:
        return f"❓ Commande inconnue : {text}\nTape /help pour la liste."


# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
def polling_loop():
    """Thread dédié à la lecture des messages Telegram — toujours réactif."""
    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg    = update.get("message", {})
                text   = msg.get("text", "")
                cid    = str(msg.get("chat", {}).get("id", ""))

                if cid != str(TELEGRAM_CHAT_ID):
                    log.warning(f"Message ignoré de chat_id={cid}")
                    continue

                if text:
                    log.info(f"Commande reçue : {text}")
                    reply = handle_command(text)
                    send(reply)
        except Exception as exc:
            log.warning(f"Polling erreur: {exc}")
        time.sleep(POLL_INTERVAL)


def watchdog_loop():
    """Thread dédié à la surveillance et au redémarrage des bots crashés."""
    while True:
        for name in list(processes.keys()):
            try:
                if processes[name].poll() is not None:
                    crash_counts[name] = crash_counts.get(name, 0) + 1

                    if crash_counts[name] > MAX_AUTO_RESTARTS:
                        log.error(f"Bot '{name}' abandonné après {MAX_AUTO_RESTARTS} crashs.")
                        err_path = os.path.join(CRASH_LOG_DIR, f"{name}_stderr.log")
                        tail = ""
                        try:
                            with open(err_path, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                                tail = "".join(lines[-15:])
                        except Exception:
                            pass
                        send(
                            f"🛑 <b>{BOTS[name]['label']} abandonné</b>\n"
                            f"Trop de crashs ({crash_counts[name]}).\n"
                            f"Tape /start_{name} pour réessayer.\n\n"
                            f"<b>Erreur :</b>\n<code>{tail[-500:]}</code>"
                        )
                        del processes[name]
                        continue

                    log.warning(f"Bot '{name}' crashé — tentative {crash_counts[name]}/{MAX_AUTO_RESTARTS}")
                    start_bot(name, manual=False)
                    send(f"⚠️ {BOTS[name]['label']} crashé — redémarrage {crash_counts[name]}/{MAX_AUTO_RESTARTS}")
            except Exception as exc:
                log.warning(f"Watchdog erreur pour {name}: {exc}")
        time.sleep(5)


def main():
    log.info("Controller Telegram démarré.")
    send(
        "🎮 <b>Controller démarré</b>\n"
        "Tape /help pour voir les commandes disponibles."
    )

    # Lancer les deux threads en parallèle
    t_polling  = threading.Thread(target=polling_loop,  daemon=True)
    t_watchdog = threading.Thread(target=watchdog_loop, daemon=True)
    t_polling.start()
    t_watchdog.start()

    # Garder le programme principal en vie
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
