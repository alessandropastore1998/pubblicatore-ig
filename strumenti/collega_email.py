"""Collega l'email dedicata (Gmail) dell'agente Guida e salva indirizzo e "password per le app" su GitHub.

Da lanciare UNA volta sul PC:  .venv\\Scripts\\python.exe strumenti\\collega_email.py
Prima: account Google dedicato con la verifica in 2 passaggi attiva e una "password per le app"
(myaccount.google.com/apppasswords). NON è la password dell'account: è un codice di 16 lettere che serve solo a inviare email.
"""
import getpass
import re
import smtplib
import subprocess
import sys
from email.message import EmailMessage

REPO = __import__("os").environ.get("REPO") or input("Repository GitHub (utente/nome): ").strip()
GH = r"C:\Program Files\GitHub CLI\gh.exe"


def gh(*argomenti, input=None):
    r = subprocess.run([GH, *argomenti, "--repo", REPO], input=input, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"Errore GitHub: {r.stderr.strip()}")


def main():
    print("\n=== Collegamento email dell'agente Guida ===")
    print("Per incollare usa il TASTO DESTRO del mouse (non Ctrl+V), una sola volta.\n")
    while True:
        indirizzo = input("1) Scrivi l'indirizzo Gmail dedicato (es. info.tuoprofilo@gmail.com) e premi Invio: ").strip().lower()
        if re.fullmatch(r"[a-z0-9._%+-]+@gmail\.com", indirizzo):
            break
        print("   ✗ Non sembra un indirizzo @gmail.com. Riprova.")
    while True:
        grezzo = getpass.getpass("2) Incolla la password per le app (16 lettere, non si vedrà) e premi Invio: ")
        lettere = re.findall(r"[a-zA-Z]{4}", grezzo.replace(" ", ""))
        codice = "".join(lettere)[:16].lower() if len("".join(lettere)) >= 16 else ""
        if len(codice) == 16:
            break
        print("   ✗ Servono 16 lettere (Google le mostra in 4 gruppi da 4). Riprova.")

    print("   Provo a mandare un'email di prova…")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(indirizzo, codice)
            m = EmailMessage()
            m["From"], m["To"], m["Subject"] = indirizzo, indirizzo, "✅ Agente Guida collegato"
            m.set_content("Da qui ti arriveranno le richieste di appuntamento e i messaggi commerciali del profilo.")
            s.send_message(m)
    except smtplib.SMTPAuthenticationError:
        sys.exit("Gmail non accetta indirizzo e password per le app: ricontrolla (serve la verifica in 2 passaggi) e rilancia.")

    gh("secret", "set", "EMAIL_MITTENTE", input=indirizzo)
    gh("secret", "set", "EMAIL_PASSWORD_APP", input=codice)
    gh("secret", "set", "EMAIL_DESTINATARIO", input=indirizzo)
    print("  ✓ EMAIL_MITTENTE, EMAIL_PASSWORD_APP ed EMAIL_DESTINATARIO salvati su GitHub")
    print("\n✅ Fatto! Controlla la casella: dovresti aver ricevuto «Agente Guida collegato». Scrivi «fatto» a Claude.\n")


if __name__ == "__main__":
    main()
