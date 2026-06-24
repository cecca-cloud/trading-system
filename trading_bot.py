"""
=============================================================
  BOT D'ALERTES TRADING — Tendance Haussière Multi-Timeframe
  Version finale complète
=============================================================
Séquence complète :
  [1H] MACD < 0 croise signal ↑           → SIGNAL
  [1H] RSI(HLCC/4) croise MME40 ↑         → GO
  [1H] RSI recroise MME40 ↓ (RSI < 50)    → STOP (faux départ)
  [1H] RSI > 50                            → TENDANCE CONFIRMÉE
  [4H] Stoch(34,3,3) croise signal ↑ <70  → SUPPORT
  [1D] RSI(HLCC/4) croise MME40 ↓         → SORTIE TOTALE
  [1D] RSI(HLCC/4) < 40 (toute watchlist) → ALERTE SURVENTE

RSI : période 34, source HLCC/4 = (H+L+C+C)/4
Scan toutes les 30 minutes.
=============================================================
"""

import yfinance as yf
import pandas as pd
import numpy as np
import time
import logging
import json
import os
import requests
import ctypes
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
#  CONFIGURATION — tokens dans config.py
# ─────────────────────────────────────────────
try:
    from config import TELEGRAM_BOT_TOKEN_TRADING, TELEGRAM_CHAT_ID as _CHAT_ID
except ImportError:
    raise SystemExit("Fichier config.py manquant — crée-le avec tes tokens.")

# ─────────────────────────────────────────────
#  WATCHLIST — 99 tickers
# ─────────────────────────────────────────────
TICKERS = [
    # Ta watchlist originale complète
    "LUNR", "RCAT", "OKLO", "ONDS", "RGTI",
    "CRWV", "SOFI", "INFQ", "QBTS", "HPQ",
    "NBIS", "IONQ", "QS", "TE", "PLTR",
    "FLNC", "SMCI", "INTC", "AAL", "EOSE",
    "RDW", "UUUU", "PL", "RKLB", "ASTS",
    "MRVL", "FCEL", "SERV", "AXTI", "RIVN",
    "SMR", "ENPH", "NVDA", "TSLA", "AMD",
    "ARM", "AVGO", "META", "AMZN", "GOOGL",
    "VELO", "MSFT", "NFLX", "COIN", "MSTR",
    "HOOD", "PYPL", "CRWD", "SNOW", "DDOG",
    "ZS", "MU", "QCOM", "MRNA", "CVNA",
    "DKNG", "RBLX", "APP", "NU", "BABA",
    "SE", "SHOP", "MELI", "ABNB", "UBER",
    "DASH", "CIEN", "MNST", "SPCX", "OSS",
    # Ajouts Nasdaq 100 pertinents
    "AAPL", "ADBE", "CRM", "ORCL", "PANW",
    "WDAY", "TTD", "CDNS", "SNPS", "LRCX",
    "AMAT", "ASML", "ON", "NXPI", "TXN",
    "ISRG", "REGN", "GILD", "PDD", "JD",
    "GRAB", "CELH", "DUOL", "HIMS", "RXRX",
    "SOUN", "BBAI", "ACHR", "JOBY",
]

SCAN_INTERVAL_MINUTES = 30
NY = ZoneInfo("America/New_York")

TELEGRAM_CONFIG = {
    "enabled":   True,
    "bot_token": TELEGRAM_BOT_TOKEN_TRADING,
    "chat_id":   _CHAT_ID,
}

GITHUB_CONFIG = {
    "enabled":   True,
    "repo_path": "C:/Users/cecca/trading-system",
    "branch":    "main",
}

LOG_FILE      = "trading_bot.log"
STATE_FILE    = "bot_state.json"
SIGNALS_FILE  = "dashboard/signals.json"
POSITIONS_FILE= "dashboard/positions.json"

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
#  ANTI-VEILLE WINDOWS
# ─────────────────────────────────────────────
def prevent_sleep():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        log.info("Anti-veille Windows active.")
    except Exception:
        pass

# ─────────────────────────────────────────────
#  INDICATEURS TECHNIQUES
# ─────────────────────────────────────────────
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_macd(close, fast=12, slow=26, signal=9):
    macd_line   = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def compute_rsi_hlcc(high, low, close, period=34):
    hlcc4  = (high + low + close + close) / 4
    delta  = hlcc4.diff()
    gain   = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss   = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss))

def compute_mme(series, period=40):
    return ema(series, period)

def compute_stochastic(high, low, close, k_period=34, k_smooth=3, d_smooth=3):
    lowest  = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    raw_k   = 100 * (close - lowest) / (highest - lowest + 1e-10)
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d

# ─────────────────────────────────────────────
#  DONNÉES
# ─────────────────────────────────────────────
def fetch_ohlcv(ticker, interval, period):
    try:
        df = yf.download(ticker, interval=interval, period=period,
                         progress=False, auto_adjust=True)
        df.dropna(inplace=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        return df
    except Exception as exc:
        log.warning(f"[{ticker}] Erreur fetch {interval}: {exc}")
        return pd.DataFrame()

def fetch_ohlcv_4h(ticker, period="90d"):
    df1h = fetch_ohlcv(ticker, "1h", period)
    if df1h.empty or len(df1h) < 20:
        return pd.DataFrame()
    try:
        df4h = df1h.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna()
        return df4h
    except Exception as exc:
        log.warning(f"[{ticker}] Erreur resample 4h: {exc}")
        return pd.DataFrame()

# ─────────────────────────────────────────────
#  PERSISTANCE ÉTAT
# ─────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def load_json(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def ts_now():
    return datetime.now(NY).strftime("%Y-%m-%d %H:%M")

def log_signal(ticker, event, price, details):
    signals = load_json(SIGNALS_FILE)
    if ticker not in signals:
        signals[ticker] = []
    signals[ticker].append({"ts": ts_now(), "event": event,
                             "price": round(float(price), 4), **details})
    save_json(SIGNALS_FILE, signals)

def open_position(ticker, price, macd_val, rsi_val, mme40_val):
    positions = load_json(POSITIONS_FILE)
    positions.setdefault("open", {})
    positions.setdefault("history", [])
    positions["open"][ticker] = {
        "ticker": ticker, "entry_ts": ts_now(),
        "entry_price": round(float(price), 4),
        "macd_val": round(float(macd_val), 6),
        "go_ts": None, "rsi_go": round(float(rsi_val), 2),
        "mme40_go": round(float(mme40_val), 2),
        "confirmed_ts": None, "supports": [],
        "stop_loss": None, "false_starts": 0,
        "exit_ts": None, "exit_price": None,
        "pnl_pct": None, "status": "SIGNAL",
    }
    save_json(POSITIONS_FILE, positions)

def update_position(ticker, **kwargs):
    positions = load_json(POSITIONS_FILE)
    if ticker in positions.get("open", {}):
        positions["open"][ticker].update(kwargs)
        save_json(POSITIONS_FILE, positions)

def add_support(ticker, price, stoch_k, stoch_d):
    positions = load_json(POSITIONS_FILE)
    if ticker in positions.get("open", {}):
        positions["open"][ticker]["supports"].append({
            "ts": ts_now(), "price": round(float(price), 4),
            "stoch_k": round(float(stoch_k), 2),
            "stoch_d": round(float(stoch_d), 2),
        })
        save_json(POSITIONS_FILE, positions)

def close_position(ticker, exit_price, reason):
    positions = load_json(POSITIONS_FILE)
    if ticker not in positions.get("open", {}):
        return
    pos = positions["open"].pop(ticker)
    pos["exit_ts"]     = ts_now()
    pos["exit_price"]  = round(float(exit_price), 4)
    pos["status"]      = "CLOSED"
    pos["exit_reason"] = reason
    if pos["entry_price"]:
        pos["pnl_pct"] = round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
    positions["history"].append(pos)
    save_json(POSITIONS_FILE, positions)

def increment_false_start(ticker):
    positions = load_json(POSITIONS_FILE)
    if ticker in positions.get("open", {}):
        positions["open"][ticker]["false_starts"] += 1
        save_json(POSITIONS_FILE, positions)

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message):
    if not TELEGRAM_CONFIG["enabled"]:
        return
    token   = TELEGRAM_CONFIG["bot_token"]
    chat_id = TELEGRAM_CONFIG["chat_id"]
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id, "text": message, "parse_mode": "HTML",
        }, timeout=10)
        if resp.status_code != 200:
            log.warning(f"Telegram {resp.status_code}: {resp.text}")
    except Exception as exc:
        log.warning(f"Telegram erreur: {exc}")

TEMPLATES = {
    "SIGNAL": (
        "SIGNAL MACD\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix : {price:.2f}$\n"
        "MACD={macd:.4f}  Signal={sig:.4f}\n"
        "Probabilite retour haussier : {prob}%"
    ),
    "GO": (
        "GO INVESTISSEMENT\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix entree : {price:.2f}$\n"
        "RSI={rsi:.1f} > MME40={mme:.1f}"
    ),
    "FALSE_START": (
        "FAUX DEPART\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix : {price:.2f}$\n"
        "RSI({rsi:.1f}) recroise MME40({mme:.1f}) a la baisse\n"
        "Faux departs : {false_starts}"
    ),
    "CONFIRMED": (
        "TENDANCE HAUSSIERE CONFIRMEE\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix : {price:.2f}$\n"
        "RSI={rsi:.1f} > 50"
    ),
    "SUPPORT": (
        "SUPPORT - Prise de position\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix : {price:.2f}$\n"
        "Stoch K={k:.1f} > D={d:.1f} (sous 70)"
    ),
    "EXIT": (
        "SORTIE TOTALE\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix sortie : {price:.2f}$\n"
        "RSI Daily < MME40\n"
        "Performance : {pnl:+.2f}%"
    ),
    "OVERSOLD": (
        "ALERTE SURVENTE Daily\n"
        "Action : {ticker}  |  {ts}\n"
        "Prix : {price:.2f}$\n"
        "RSI(HLCC/4) Daily = {rsi:.1f} (sous 40)\n"
        "Surveiller un potentiel retournement"
    ),
}

def alert(ticker, level, price, **kwargs):
    ts  = ts_now()
    tpl = TEMPLATES.get(level, "{ticker} - {level}")
    msg = tpl.format(ticker=ticker, ts=ts, price=price, **kwargs)
    log.info(msg.replace("\n", " | "))
    send_telegram(msg)
    try:
        print("\n" + "="*60)
        print(msg)
        print("="*60)
    except UnicodeEncodeError:
        pass

# ─────────────────────────────────────────────
#  GIT PUSH AUTOMATIQUE
# ─────────────────────────────────────────────
def git_push():
    if not GITHUB_CONFIG["enabled"]:
        return
    try:
        repo   = GITHUB_CONFIG["repo_path"]
        branch = GITHUB_CONFIG["branch"]
        ts     = datetime.now(NY).strftime("%Y-%m-%d %H:%M")
        cmds = [
            ["git", "-C", repo, "add", "dashboard/"],
            ["git", "-C", repo, "commit", "-m", f"dashboard update {ts}"],
            ["git", "-C", repo, "push", "origin", branch],
        ]
        for cmd in cmds:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                if "nothing to commit" in result.stdout + result.stderr:
                    return
                log.warning(f"Git: {result.stderr.strip()}")
                return
        log.info("Dashboard pousse sur GitHub Pages.")
    except Exception as exc:
        log.warning(f"Git push echoue: {exc}")

# ─────────────────────────────────────────────
#  ANALYSE PAR TICKER
# ─────────────────────────────────────────────
def analyse_ticker(ticker, state):
    s = state.setdefault(ticker, {
        "macd_alert":      False,
        "go_active":       False,
        "trend_confirmed": False,
        "oversold_alert":  False,
    })

    # Données 1H
    df1h = fetch_ohlcv(ticker, "1h", "60d")
    if df1h.empty or len(df1h) < 60:
        return

    close1h = df1h["Close"]
    high1h  = df1h["High"]
    low1h   = df1h["Low"]
    price   = float(close1h.iloc[-1])

    macd_line, sig_line = compute_macd(close1h)
    rsi   = compute_rsi_hlcc(high1h, low1h, close1h, period=34)
    mme40 = compute_mme(rsi)

    macd_c = float(macd_line.iloc[-1]); macd_p = float(macd_line.iloc[-2])
    sig_c  = float(sig_line.iloc[-1]);  sig_p  = float(sig_line.iloc[-2])
    rsi_c  = float(rsi.iloc[-1]);       rsi_p  = float(rsi.iloc[-2])
    mme_c  = float(mme40.iloc[-1]);     mme_p  = float(mme40.iloc[-2])

    # Règle 1 : MACD < 0 croise signal ↑
    if (macd_p < sig_p) and (macd_c >= sig_c) and (macd_c < 0) and not s["macd_alert"]:
        s["macd_alert"] = True
        prob = min(95, max(50, int(70 + abs(macd_c) * 10)))
        alert(ticker, "SIGNAL", price, macd=macd_c, sig=sig_c, prob=prob)
        log_signal(ticker, "SIGNAL", price, {"macd": macd_c, "sig": sig_c, "prob": prob})
        open_position(ticker, price, macd_c, rsi_c, mme_c)

    # Règle 2 : RSI croise MME40 ↑
    if (rsi_p <= mme_p) and (rsi_c > mme_c) and s["macd_alert"] and not s["go_active"]:
        s["go_active"] = True
        alert(ticker, "GO", price, rsi=rsi_c, mme=mme_c)
        log_signal(ticker, "GO", price, {"rsi": rsi_c, "mme40": mme_c})
        update_position(ticker, go_ts=ts_now(), status="GO",
                        entry_price=price, stop_loss=round(price * 0.95, 4))

    # Règle 3 : RSI recroise MME40 ↓ sous 50
    if (rsi_p >= mme_p) and (rsi_c < mme_c) and s["go_active"] and rsi_c < 50:
        increment_false_start(ticker)
        pos = load_json(POSITIONS_FILE).get("open", {}).get(ticker, {})
        fs  = pos.get("false_starts", 1)
        alert(ticker, "FALSE_START", price, rsi=rsi_c, mme=mme_c, false_starts=fs)
        log_signal(ticker, "FALSE_START", price, {"rsi": rsi_c, "mme40": mme_c})
        s["go_active"] = False
        s["trend_confirmed"] = False
        update_position(ticker, status="SIGNAL")

    # Règle 4 : RSI > 50
    if (rsi_p <= 50) and (rsi_c > 50) and s["go_active"] and not s["trend_confirmed"]:
        s["trend_confirmed"] = True
        alert(ticker, "CONFIRMED", price, rsi=rsi_c)
        log_signal(ticker, "CONFIRMED", price, {"rsi": rsi_c})
        update_position(ticker, confirmed_ts=ts_now(), status="CONFIRMED")

    # Règle 5 : Stoch 4H croise ↑ sous 70
    if s["trend_confirmed"]:
        df4h = fetch_ohlcv_4h(ticker, "90d")
        if not df4h.empty and len(df4h) >= 50:
            k, d = compute_stochastic(df4h["High"], df4h["Low"], df4h["Close"])
            k_c = float(k.iloc[-1]); k_p = float(k.iloc[-2])
            d_c = float(d.iloc[-1]); d_p = float(d.iloc[-2])
            if (k_p <= d_p) and (k_c > d_c) and k_c < 70:
                alert(ticker, "SUPPORT", price, k=k_c, d=d_c)
                log_signal(ticker, "SUPPORT", price, {"stoch_k": k_c, "stoch_d": d_c})
                add_support(ticker, price, k_c, d_c)

    # Données 1D (sortie + survente)
    df1d = fetch_ohlcv(ticker, "1d", "120d")
    if not df1d.empty and len(df1d) >= 50:
        rsi_d = compute_rsi_hlcc(df1d["High"], df1d["Low"], df1d["Close"], period=34)
        mme_d = compute_mme(rsi_d)
        rd_c = float(rsi_d.iloc[-1]); rd_p = float(rsi_d.iloc[-2])
        md_c = float(mme_d.iloc[-1]); md_p = float(mme_d.iloc[-2])

        # Règle 6 : SORTIE RSI Daily croise MME40 ↓
        if (s["go_active"] or s["trend_confirmed"]) and (rd_p >= md_p) and (rd_c < md_c):
            pos   = load_json(POSITIONS_FILE).get("open", {}).get(ticker, {})
            entry = pos.get("entry_price", price)
            pnl   = (price - entry) / entry * 100 if entry else 0
            alert(ticker, "EXIT", price, pnl=pnl)
            log_signal(ticker, "EXIT", price, {"rsi_daily": rd_c, "pnl_pct": round(pnl, 2)})
            close_position(ticker, price, "RSI_DAILY_CROSS_MME40")
            s["macd_alert"] = s["go_active"] = s["trend_confirmed"] = False

        # Règle 7 : SURVENTE RSI Daily < 40
        if rd_c < 40 and not s["oversold_alert"]:
            s["oversold_alert"] = True
            alert(ticker, "OVERSOLD", price, rsi=rd_c)
            log_signal(ticker, "OVERSOLD", price, {"rsi_daily": rd_c})
        elif rd_c >= 40 and s["oversold_alert"]:
            s["oversold_alert"] = False

# ─────────────────────────────────────────────
#  BOUCLE PRINCIPALE
# ─────────────────────────────────────────────
def run_scan():
    log.info("=" * 60)
    log.info(f"SCAN — {datetime.now(NY).strftime('%Y-%m-%d %H:%M')} NY")
    state = load_state()
    for ticker in TICKERS:
        log.info(f"  > {ticker}")
        try:
            analyse_ticker(ticker, state)
        except Exception as exc:
            log.error(f"[{ticker}] Erreur: {exc}", exc_info=True)
    save_state(state)

    meta = load_json("dashboard/meta.json")
    meta["last_scan"] = ts_now()
    meta["tickers"]   = TICKERS
    save_json("dashboard/meta.json", meta)

    log.info(f"Scan termine. Prochain scan dans {SCAN_INTERVAL_MINUTES} min\n")
    git_push()


def main():
    prevent_sleep()
    log.info("Bot demarre.")
    send_telegram(
        f"Trading Bot demarre\n"
        f"{len(TICKERS)} tickers surveilles\n"
        f"Scan toutes les {SCAN_INTERVAL_MINUTES} minutes"
    )
    while True:
        run_scan()
        time.sleep(SCAN_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
