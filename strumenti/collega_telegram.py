"""Collega il bot Telegram degli avvisi e salva token e chat su GitHub.

Da lanciare UNA volta sul PC:  .venv\\Scripts\\python.exe strumenti\\collega_telegram.py
Prima: crea il bot con @BotFather e scrivigli /start dal tuo Telegram.
"""
import getpass
import re
import subprocess
import sys

import requests

REPO = __import__("os").environ.get("REPO") or input("Repository GitHub (utente/nome): ").strip()
GH = r"C:\Program Files\GitHub CLI\gh.exe"


def gh(*argomenti, input=None):
    r = subprocess.run([GH, *argomenti, "--repo", REPO], input=input, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Errore GitHub: {r.stderr.strip()}")


def main():
    print("\n=== Collegamento Telegram ===")
    print("Per incollare usa il TASTO DESTRO del mouse (non Ctrl+V), una sola volta.\n")
    while True:
        grezzo = getpass.getpass("1) Incolla il token del bot (non si vedrà) e premi Invio: ")
        trovati = set(re.findall(r"\d{6,12}:[A-Za-z0-9_-]{30,50}", grezzo))
        if len(trovati) == 1:
            token = trovati.pop()
            break
        print("   ✗ Non sembra un token di BotFather. Riprova.")

    io = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
    if not io.get("ok"):
        sys.exit("Telegram non riconosce questo token: ricontrolla su @BotFather e rilancia.")
    nome_bot = io["result"]["username"]

    chat = None
    while not chat:
        aggiornamenti = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
        for u in reversed(aggiornamenti.get("result", [])):
            msg = u.get("message") or {}
            if msg.get("chat", {}).get("type") == "private":
                chat = str(msg["chat"]["id"])
                break
        if not chat:
            input(f"2) Apri Telegram, cerca @{nome_bot}, premi AVVIA (o scrivi /start), poi torna qui e premi Invio… ")

    gh("secret", "set", "TELEGRAM_BOT_TOKEN", input=token)
    print("  ✓ TELEGRAM_BOT_TOKEN salvato su GitHub")
    gh("variable", "set", "TELEGRAM_CHAT_ID", "--body", chat)
    print("  ✓ TELEGRAM_CHAT_ID salvato su GitHub")

    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": "👋 Ciao! Da qui ti avviserò sulle pubblicazioni."},
        timeout=30,
    )
    print("\n✅ Fatto! Dovresti aver ricevuto un messaggio su Telegram. Scrivi «fatto» a Claude.\n")


if __name__ == "__main__":
    main()
