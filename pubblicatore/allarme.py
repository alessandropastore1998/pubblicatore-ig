"""Modalità allarme: guarda gli ultimi 6 Reel ogni sera, dopo le statistiche.

Stato in /allarme.json (lo legge la redazione). Avvisi Telegram quando scatta o si spegne, e ogni 3 giorni tre numeri
più l'esperimento in corso (/post/<cartella>/esperimento.txt, scritto dalla redazione).
Scatta con mediana di visione < 3 s o raggiunti medi < 150; si spegne con mediana >= 4 s e raggiunti medi >= 300.
"""
import os
import statistics
import sys
from datetime import datetime, timedelta

from . import avvisi
from .dropbox_client import Dropbox, DropboxNonTrovato
from .instagram import Instagram
from .main import adesso, ora_iso

QUANTI = 6
PERCORSO = "/allarme.json"
OGNI = timedelta(days=3)
RICORDA_ESEMPI = 5


def ultimi_reel(dbx, quanti=QUANTI):
    """Dagli ultimi post: (quando, visione in secondi, raggiunti) dei Reel con i numeri a 48 ore."""
    cartelle = sorted((v["name"] for v in dbx.elenca("/post") if v.get(".tag") == "folder"), reverse=True)
    trovati = []
    for nome in cartelle:
        try:
            r = dbx.leggi_json(f"/post/{nome}/risultati-reel.json")
        except (DropboxNonTrovato, ValueError):
            continue
        m = (r.get("48h") or {}).get("metriche") or {}
        if "ig_reels_avg_watch_time" in m and "reach" in m:
            trovati.append((r.get("pubblicato", nome), m["ig_reels_avg_watch_time"] / 1000, m["reach"]))
        if len(trovati) >= quanti * 2:
            break
    return sorted(trovati, reverse=True)[:quanti]


def valuta(reel, stato_prima):
    """Restituisce (stato, mediana di visione, raggiunti medi). Tra le due soglie lo stato non cambia."""
    mediana = statistics.median(r[1] for r in reel)
    media = sum(r[2] for r in reel) / len(reel)
    if mediana < 3 or media < 150:
        return "ON", mediana, media
    if len(reel) >= QUANTI and mediana >= 4 and media >= 300:
        return "OFF", mediana, media
    return stato_prima or "ON", mediana, media


def esperimento_in_corso(dbx):
    for nome in sorted((v["name"] for v in dbx.elenca("/post") if v.get(".tag") == "folder"), reverse=True)[:6]:
        try:
            return dbx.leggi_testo(f"/post/{nome}/esperimento.txt").strip()[:300]
        except DropboxNonTrovato:
            continue
    return "nessuno segnato"


def quanti_esempi(dbx):
    try:
        return dbx.leggi_testo("/ispirazione/esempi-telegram.md").count("http")
    except DropboxNonTrovato:
        return 0


def follower():
    ig = Instagram(os.environ["IG_TOKEN"])
    return ig.chiama("GET", "me", fields="followers_count").get("followers_count", "?")


def ricontrolla(dbx):
    try:
        prima = dbx.leggi_json(PERCORSO)
    except (DropboxNonTrovato, ValueError):
        prima = {}
    reel = ultimi_reel(dbx)
    if not reel:
        print("Nessun Reel con i numeri a 48 ore.")
        return 0
    stato, mediana, media = valuta(reel, prima.get("stato"))
    seguito = follower()
    numeri = f"👁 Mediana di visione {mediana:.1f} s · 📣 raggiunti medi {media:.0f} · 👥 follower {seguito}"
    ultimo = prima.get("ultimo_riepilogo")
    dovuto = not ultimo or adesso() - datetime.fromisoformat(ultimo) >= OGNI
    if stato != prima.get("stato"):
        titolo = "🚨 ALLARME NUMERI acceso" if stato == "ON" else "✅ Allarme numeri spento: 6 Reel sopra soglia"
        avvisi.invia(f"{titolo}\n{numeri}\nEsperimento in corso: {esperimento_in_corso(dbx)}")
        dovuto = False
        ultimo = ora_iso()
    elif dovuto:
        testo = f"📊 Numeri degli ultimi {len(reel)} Reel\n{numeri}\nEsperimento in corso: {esperimento_in_corso(dbx)}"
        esempi = quanti_esempi(dbx)
        if esempi < RICORDA_ESEMPI:
            testo += f"\nEsempi ricevuti: {esempi}. Mandane altri con /esempio LINK perché."
        avvisi.invia(testo)
        ultimo = ora_iso()
    dbx.scrivi_json(PERCORSO, {"stato": stato, "mediana_visione_s": round(mediana, 1), "raggiunti_medi": round(media),
                               "follower": seguito, "reel_contati": len(reel), "aggiornato": ora_iso(), "ultimo_riepilogo": ultimo})
    print(f"Stato: {stato}.")
    return 0


def main():
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    return ricontrolla(dbx)


if __name__ == "__main__":
    sys.exit(main())
