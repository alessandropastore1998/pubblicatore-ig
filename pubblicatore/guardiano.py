"""Guardiano: avvia foto, carosello e Reel all'ora esatta, senza PC e senza servizi esterni.

Gli orari programmati di GitHub possono arrivare con ore di ritardo, gli avvii manuali (workflow_dispatch) partono
subito. Il guardiano resta acceso su GitHub, all'ora giusta avvia gli altri workflow con un avvio manuale e, prima del
limite di 6 ore di GitHub, avvia il suo sostituto. Se la catena si spezza la fanno ripartire le partenze programmate.

Solo libreria standard: niente pip, parte in pochi secondi. Log muti: solo orari e nomi dei workflow.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")
API = "https://api.github.com"

# Ora di Roma → workflow e scelte. Stessi avvii che il 07/11/2026 partivano dal PC.
AGENDA = [
    ("06:00", "foto.yml", {"modalita": "attendi"}),
    ("07:20", "pubblica.yml", {"modalita": "auto"}),
    ("07:32", "reel.yml", {"modalita": "auto"}),
    ("18:00", "foto.yml", {"modalita": "attendi"}),
    ("19:20", "pubblica.yml", {"modalita": "auto"}),
    ("19:32", "reel.yml", {"modalita": "auto"}),
]
RECUPERO = timedelta(minutes=25)    # un avvio saltato (cambio di turno, ritardo) si recupera entro 25 minuti
GIA_AVVIATO = timedelta(minutes=10)  # se qualcuno l'ha già avviato da poco (PC, a mano) non si ripete
TURNO = timedelta(hours=5, minutes=20)
MARGINE_CAMBIO = timedelta(minutes=15)  # il cambio di turno non cade a ridosso di un avvio
PAUSA_S = 20
CONTROLLO_DOPO = timedelta(minutes=5)  # dopo un avvio controlla che il lavoro sia partito davvero


def adesso():
    return datetime.now(ROMA)


def chiama(metodo, percorso, corpo=None):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    richiesta = urllib.request.Request(API + percorso, data=dati, method=metodo, headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "guardiano",
    })
    with urllib.request.urlopen(richiesta, timeout=30) as risposta:
        testo = risposta.read()
    return json.loads(testo) if testo else None


def repo():
    return os.environ["GITHUB_REPOSITORY"]


def ramo():
    return os.environ.get("RAMO") or "main"


def avvia(workflow, scelte):
    chiama("POST", f"/repos/{repo()}/actions/workflows/{workflow}/dispatches", {"ref": ramo(), "inputs": scelte})


def gia_avviato(workflow, da):
    """Vero se dopo 'da' esiste già un avvio manuale di quel workflow (dal PC, a mano o da un altro guardiano)."""
    creato = da.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.urlencode({"event": "workflow_dispatch", "created": f">={creato}", "per_page": 5})
    dati = chiama("GET", f"/repos/{repo()}/actions/workflows/{workflow}/runs?{q}")
    return bool(dati and dati.get("total_count"))


def avviso(testo):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        dati = urllib.parse.urlencode({"chat_id": chat, "text": testo}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dati, timeout=20).read()
    except Exception:
        print("Avviso Telegram non inviato")


def orario(giorno, hhmm):
    ore, minuti = map(int, hhmm.split(":"))
    return datetime(giorno.year, giorno.month, giorno.day, ore, minuti, tzinfo=ROMA)


def scadenze(ora):
    """Gli avvii di ieri, oggi e domani come (momento, workflow, scelte), in ordine."""
    giorni = [ora.date() - timedelta(days=1), ora.date(), ora.date() + timedelta(days=1)]
    return sorted((orario(g, h), wf, scelte) for g in giorni for h, wf, scelte in AGENDA)


def da_fare(ora, fatti):
    """Avvii il cui momento è arrivato da meno di RECUPERO e non ancora gestiti da questo guardiano."""
    return [(m, wf, s) for m, wf, s in scadenze(ora) if m <= ora < m + RECUPERO and (m, wf) not in fatti]


def prossimo(ora):
    return next(m for m, _, _ in scadenze(ora) if m > ora)


def puo_cambiare_turno(ora, inizio):
    """Il turno è finito e il prossimo avvio non è a ridosso (altrimenti si aspetta di averlo fatto)."""
    if ora - inizio < TURNO:
        return False
    vicino = prossimo(ora) - ora < MARGINE_CAMBIO
    appena_passato = any(m <= ora < m + timedelta(minutes=2) for m, _, _ in scadenze(ora))
    return not vicino and not appena_passato


def gestisci(momento, workflow, scelte, prova):
    """Vero se ha avviato il lavoro (da controllare dopo qualche minuto)."""
    etichetta = f"{momento:%H:%M} {workflow}"
    if gia_avviato(workflow, momento - GIA_AVVIATO):
        print(f"{etichetta}: già avviato, non lo ripeto")
        return False
    if prova:
        print(f"{etichetta}: in prova non avvio niente")
        return False
    try:
        avvia(workflow, scelte)
        print(f"{etichetta}: avviato")
        return True
    except urllib.error.HTTPError as e:
        print(f"{etichetta}: avvio non riuscito (HTTP {e.code})")
        avviso(f"⚠️ Guardiano: l'avvio delle {momento:%H:%M} di {workflow} non è riuscito (HTTP {e.code}).")
        return False


def controlla_partiti(ora, da_controllare):
    """Dopo CONTROLLO_DOPO verifica che i lavori avviati esistano davvero; se no avvisa su Telegram."""
    for momento, workflow in list(da_controllare):
        if ora < momento + CONTROLLO_DOPO:
            continue
        da_controllare.discard((momento, workflow))
        try:
            partito = gia_avviato(workflow, momento - GIA_AVVIATO)
        except Exception:
            continue
        print(f"{momento:%H:%M} {workflow}: {'partito' if partito else 'NON partito'}")
        if not partito:
            avviso(f"⚠️ Guardiano: il lavoro delle {momento:%H:%M} ({workflow}) non risulta partito.")


def cambio_turno():
    try:
        avvia("guardiano.yml", {"modalita": "vero"})
        print("Cambio di turno: sostituto avviato")
    except urllib.error.HTTPError as e:
        print(f"Cambio di turno non riuscito (HTTP {e.code}): ripartirà con le partenze programmate")
        avviso(f"⚠️ Guardiano: il cambio di turno non è riuscito (HTTP {e.code}). Riparte con le partenze programmate.")


def prova_avvio():
    """Prova a vuoto: controlla di poter leggere gli avvii e avvia un guardiano 'eco' che si chiude subito."""
    ora = adesso()
    print(f"Prova del guardiano alle {ora:%H:%M} di Roma")
    for m, wf, _ in scadenze(ora):
        if ora.date() == m.date():
            print(f"  {m:%H:%M} {wf}")
    print(f"Prossimo avvio: {prossimo(ora):%d/%m %H:%M}")
    for _, wf, _ in AGENDA[:3]:
        gia_avviato(wf, ora - timedelta(hours=24))
    print("Lettura degli avvii: ok")
    avvia("guardiano.yml", {"modalita": "eco"})
    print("Avvio manuale da GitHub: ok (parte un guardiano 'eco' che si chiude subito)")


def turno(prova=False):
    inizio, fatti, da_controllare = adesso(), set(), set()
    print(f"Guardiano acceso alle {inizio:%H:%M} di Roma; prossimo avvio {prossimo(inizio):%d/%m %H:%M}")
    while True:
        ora = adesso()
        for momento, workflow, scelte in da_fare(ora, fatti):
            fatti.add((momento, workflow))
            try:
                if gestisci(momento, workflow, scelte, prova):
                    da_controllare.add((momento, workflow))
            except Exception as e:
                print(f"{momento:%H:%M} {workflow}: errore ({type(e).__name__})")
                avviso(f"⚠️ Guardiano: errore all'avvio delle {momento:%H:%M} di {workflow} ({type(e).__name__}).")
        controlla_partiti(ora, da_controllare)
        if puo_cambiare_turno(ora, inizio) and not da_controllare:
            if not prova:
                cambio_turno()
            return
        time.sleep(PAUSA_S)


def main():
    modalita = os.environ.get("MODALITA") or "vero"
    if modalita == "eco":
        print("Guardiano eco: avvio manuale ricevuto, mi chiudo")
    elif modalita == "prova":
        prova_avvio()
    else:
        turno()


if __name__ == "__main__":
    sys.exit(main())
