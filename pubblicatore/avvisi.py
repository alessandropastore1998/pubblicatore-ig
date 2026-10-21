"""Avvisi sul telefono tramite bot Telegram."""
import os

import requests


def invia(testo):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    print("Avviso Telegram: invio…")  # log muti: il testo dell'avviso non va nel log
    if not token or not chat:
        print("(Telegram non configurato: avviso solo nel log)")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": testo[:4000], "disable_web_page_preview": False},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"Telegram ha risposto {r.status_code}")
    except requests.RequestException as e:
        print(f"Telegram non raggiungibile: {type(e).__name__}")
