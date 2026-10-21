"""Statistiche dei Reel: risultati-reel.json a 48 ore e a 7 giorni, accanto a risultati.json del carosello.

Stesse regole di statistiche.py (solo dati aggregati del singolo Reel, metriche chieste una alla volta),
ma legge reel-pubblicato.json. Separato da statistiche.py: se qualcosa va storto, le statistiche dei caroselli non cambiano.
"""
import os
import sys
from datetime import datetime

from .dropbox_client import Dropbox, DropboxNonTrovato
from .instagram import ErroreInstagram, Instagram
from .main import ROMA, adesso, ora_iso
from .statistiche import TAPPE, metriche_media, permesso_mancante

METRICHE_REEL = ["reach", "views", "likes", "comments", "saved", "shares", "total_interactions",
                 "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]


def risultati_reel(dbx, ig):
    aggiornati = 0
    for voce in dbx.elenca("/post"):
        if voce.get(".tag") != "folder":
            continue
        cartella = f"/post/{voce['name']}"
        try:
            reel = dbx.leggi_json(f"{cartella}/reel-pubblicato.json")
        except (DropboxNonTrovato, ValueError):
            continue
        if reel.get("esito") != "pubblicato" or not reel.get("id"):
            continue
        uscito = datetime.fromisoformat(reel["data_ora"]).astimezone(ROMA)
        try:
            risultati = dbx.leggi_json(f"{cartella}/risultati-reel.json")
        except (DropboxNonTrovato, ValueError):
            risultati = {"media_id": reel["id"], "link": reel.get("link", ""), "pubblicato": reel["data_ora"], "tipo": "reel"}
        cambiato = False
        for tappa, eta in TAPPE.items():
            if tappa in risultati and risultati[tappa].get("stato") != "non_disponibile":
                continue
            if adesso() - uscito < eta:
                continue
            try:
                valori, mancanti = metriche_media(ig, reel["id"], METRICHE_REEL)
                risultati[tappa] = {"letto_il": ora_iso(), "metriche": valori, "non_disponibili": mancanti}
            except ErroreInstagram as e:
                risultati[tappa] = {"letto_il": ora_iso(), "stato": "non_disponibile",
                                    "motivo": "permesso instagram_business_manage_insights mancante" if permesso_mancante(e) else str(e)[:200]}
            cambiato = True
        if cambiato:
            dbx.scrivi_json(f"{cartella}/risultati-reel.json", risultati)
            aggiornati += 1
    return aggiornati


def main():
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    ig = Instagram(os.environ["IG_TOKEN"])
    print(f"risultati-reel.json aggiornati: {risultati_reel(dbx, ig)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
