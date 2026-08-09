"""
Bot de signal Or (XAU) -> Alerte Telegram
==========================================
Ce script :
  1. Récupère l'historique quotidien du cours de l'or + le cours en direct
  2. Calcule les moyennes mobiles (SMA9/SMA21) et le RSI(14)
  3. Détermine un signal : ACHETER / VENDRE / ATTENDRE
  4. Si le signal vient de changer vers ACHETER ou VENDRE, envoie un message Telegram
  5. Sauvegarde l'état dans state.json pour se souvenir du dernier signal envoyé

Prévu pour tourner toutes les 5 minutes via GitHub Actions (voir le fichier
.github/workflows/gold-signal.yml). Peut aussi tourner en local ou sur un
serveur avec `cron` (crontab -e -> */5 * * * * python3 gold_signal_bot.py).

Variables d'environnement requises :
  TELEGRAM_BOT_TOKEN  -> token de ton bot (via @BotFather)
  TELEGRAM_CHAT_ID    -> ton chat_id (voir instructions de mise en place)
"""

import json
import os
import sys
from pathlib import Path

import requests

STATE_FILE = Path(__file__).parent / "state.json"
HISTORY_URL = "https://freegoldapi.com/data/latest.json"
LIVE_URL = "https://api.gold-api.com/price/XAU/USD"


def fetch_closes():
    """Récupère l'historique quotidien + le cours en direct, combinés en une seule série."""
    hist_resp = requests.get(HISTORY_URL, timeout=15)
    hist_resp.raise_for_status()
    hist_json = hist_resp.json()
    history = [d["price"] for d in hist_json if isinstance(d.get("price"), (int, float))][-150:]

    live_resp = requests.get(LIVE_URL, timeout=15)
    live_resp.raise_for_status()
    live_price = live_resp.json()["price"]

    return history + [live_price], live_price


def sma(values, period):
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        out[i] = sum(window) / period
    return out


def rsi(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def compute_signal(closes):
    n = len(closes)
    s9 = sma(closes, 9)
    s21 = sma(closes, 21)
    r14 = rsi(closes, 14)

    if n < 22 or s9[-1] is None or s21[-1] is None or r14[-1] is None:
        return {"label": "ANALYSE", "tone": "neutral", "reason": "Pas encore assez de données."}

    last, prev = n - 1, n - 2
    cross_up = s9[prev] is not None and s21[prev] is not None and s9[prev] <= s21[prev] and s9[last] > s21[last]
    cross_down = s9[prev] is not None and s21[prev] is not None and s9[prev] >= s21[prev] and s9[last] < s21[last]
    r = r14[last]

    if cross_up:
        return {"label": "ACHETER", "tone": "buy",
                "reason": "Croisement haussier : SMA9 vient de dépasser SMA21."}
    if cross_down:
        return {"label": "VENDRE", "tone": "sell",
                "reason": "Croisement baissier : SMA9 vient de passer sous SMA21."}
    if s9[last] > s21[last] and r < 70:
        return {"label": "ACHETER", "tone": "buy",
                "reason": f"Tendance haussière (SMA9 > SMA21), RSI à {r:.0f}."}
    if s9[last] < s21[last] and r > 30:
        return {"label": "VENDRE", "tone": "sell",
                "reason": f"Tendance baissière (SMA9 < SMA21), RSI à {r:.0f}."}
    if r >= 70:
        return {"label": "ATTENDRE", "tone": "neutral", "reason": f"RSI à {r:.0f} : zone de surachat."}
    if r <= 30:
        return {"label": "ATTENDRE", "tone": "neutral", "reason": f"RSI à {r:.0f} : zone de survente."}
    return {"label": "ATTENDRE", "tone": "neutral", "reason": "Pas de signal net."}


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_signal": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
    resp.raise_for_status()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Erreur : TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID doivent être définis.", file=sys.stderr)
        sys.exit(1)

    closes, live_price = fetch_closes()
    signal = compute_signal(closes)
    state = load_state()

    print(f"Cours en direct : ${live_price:.2f} — Signal : {signal['label']} ({signal['reason']})")

    if signal["tone"] in ("buy", "sell") and signal["label"] != state.get("last_signal"):
        emoji = "🟢" if signal["tone"] == "buy" else "🔴"
        verb = "ACHETER" if signal["tone"] == "buy" else "VENDRE"
        price_label = "Prix d'achat conseillé" if signal["tone"] == "buy" else "Prix de vente conseillé"
        text = (
            f"{emoji} <b>{verb} — OR (XAU)</b>\n"
            f"{price_label} : <b>${live_price:,.2f}</b> / once troy\n"
            f"{signal['reason']}"
        )
        send_telegram(token, chat_id, text)
        print("-> Alerte Telegram envoyée.")
    else:
        print("-> Pas de nouveau signal, aucune alerte envoyée.")

    state["last_signal"] = signal["label"]
    save_state(state)


if __name__ == "__main__":
    main()
