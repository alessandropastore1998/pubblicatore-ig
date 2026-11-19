"""Pulizia automatica dopo ogni pubblicazione (carosello o Reel): niente materiale inutile nell'archivio.

Va nel cestino di Google Drive (recuperabile per 30 giorni, poi Drive lo svuota da solo):
- nelle cartelle /post/ dei giorni passati: le foto esportate (carosello/), il video e la copertina del Reel e i
  risultati delle prove a vuoto (prova.json, reel-prova.json). Restano post.json, reel.json, pubblicato.json,
  risultati*.json e tutti gli altri file che servono allo storico e all'Analista;
- le candidate della cassetta foto (/candidate/<cartella>/) e le richieste (/foto-richieste/) dei giorni passati:
  sia le foto scartate sia quelle usate, che ormai sono su Instagram;
- le istruzioni delle prove a vuoto del Reel (/prove/) più vecchie di un giorno;
- in /test/: le foto, i video e i risultati delle prove più vecchi di 6 ore (i post.json di prova restano).

Non tocca mai: musica, riserve, storico-dm, avvisi, pubblico, ispirazione, videoteca e la cartella ToTravelInItaly.
Con Dropbox non fa niente (Dropbox non si tocca finché il titolare non conferma il passaggio).
Un errore qui non ferma mai la pubblicazione: arriva solo un avviso nel log.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .dropbox_client import DropboxNonTrovato

ROMA = ZoneInfo("Europe/Rome")
STATO = "/pulizia.json"
GENERATI = {"carosello", "reel.mp4", "reel-copertina.jpg", "prova.json", "reel-prova.json"}
RE_DATA = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _giorno(nome):
    m = RE_DATA.match(nome)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _modificato(voce):
    try:
        return datetime.fromisoformat(str(voce.get("server_modified")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _elenca(dbx, percorso):
    try:
        return dbx.elenca(percorso)
    except DropboxNonTrovato:
        return []


def dopo_pubblicazione(dbx):
    if getattr(dbx, "nome", "") != "Google Drive":
        return 0
    try:
        return _pulisci(dbx)
    except Exception as e:  # noqa: BLE001 — la pulizia non deve mai far fallire una pubblicazione
        print(f"Pulizia non completata ({type(e).__name__}).")
        return 0


def _pulisci(dbx):
    oggi = datetime.now(ROMA).date()
    ora_utc = datetime.now(timezone.utc)
    try:
        stato = dbx.leggi_json(STATO)
    except (DropboxNonTrovato, ValueError):
        stato = {}
    pulite = set(stato.get("pulite", []))
    cestinati = 0

    for v in _elenca(dbx, "/post"):
        giorno = _giorno(v["name"])
        if v.get(".tag") != "folder" or not giorno or giorno >= oggi or v["name"] in pulite:
            continue
        for f in _elenca(dbx, f"/post/{v['name']}"):
            if f["name"] in GENERATI and dbx.cancella(f"/post/{v['name']}/{f['name']}"):
                cestinati += 1
        pulite.add(v["name"])

    for cartella in ("/candidate", "/foto-richieste"):
        for v in _elenca(dbx, cartella):
            giorno = _giorno(v["name"])
            if giorno and giorno < oggi and dbx.cancella(f"{cartella}/{v['name']}"):
                cestinati += 1

    for v in _elenca(dbx, "/prove"):
        quando = _modificato(v)
        if v.get(".tag") == "file" and quando and ora_utc - quando > timedelta(days=1) and dbx.cancella(f"/prove/{v['name']}"):
            cestinati += 1

    for v in _elenca(dbx, "/test"):
        if v.get(".tag") != "folder":
            continue
        for f in _elenca(dbx, f"/test/{v['name']}"):
            quando = _modificato(f)
            if f["name"] in GENERATI and quando and ora_utc - quando > timedelta(hours=6) \
                    and dbx.cancella(f"/test/{v['name']}/{f['name']}"):
                cestinati += 1

    dbx.scrivi_json(STATO, {"ultima": datetime.now(ROMA).isoformat(timespec="seconds"), "cestinati_ultima_volta": cestinati,
                            "pulite": sorted(pulite)[-500:]})
    print(f"Pulizia: {cestinati} elementi nel cestino.")
    return cestinati


if __name__ == "__main__":  # avvio a mano: python -m pubblicatore.pulizia
    from . import archivio
    dopo_pubblicazione(archivio.apri())
