"""Cacciatore di trend (richiesta 20): raccoglie i video di viaggio da oltre 1 milione di visualizzazioni.

Solo fonti ufficiali o pubbliche:
- YouTube Shorts con l'API ufficiale YouTube Data v3 (secret YOUTUBE_API_KEY, gratuita): ricerche sui nostri temi,
  video di massimo 60 s con almeno 1.000.000 di visualizzazioni, preferibilmente degli ultimi 90 giorni;
  per ognuno titolo, canale, durata, data, descrizione, miniatura e i 5 commenti più rilevanti.
- I link YouTube mandati dal titolare con /esempio (file /ispirazione/esempi-telegram.md): entrano se ≥ 1 milione.
- TikTok Creative Center: le pagine dei video di tendenza si caricano solo con JavaScript e mostrano il pulsante di
  accesso; non si leggono in automatico e NON si aggira il blocco (vedi avanzamento della richiesta 20).
Mai login, mai download di video, mai account finti. Il risultato è un file di DATI per il Cacciatore (agente 13):
/videoteca/grezzo-AAAA-MM-GG.json nella cartella dell'app. Le schede con la ricetta le scrive l'agente guardando i dati
e le miniature; quello che non si legge si scrive "non visto".
Si accende con la variabile TREND = si (dopo il sì del titolare); a mano si può sempre lanciare.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

from . import avvisi
from .dropbox_client import Dropbox, DropboxNonTrovato
from .main import ROMA

API = "https://www.googleapis.com/youtube/v3/"
MINIMO = 1_000_000
DURATA_MAX = 60
GIORNI = 90
RICERCHE = [
    "Italy travel", "hidden gems Italy", "Italy travel shorts", "Dolomites", "Lake Como", "Amalfi Coast", "Cinque Terre",
    "Tuscany village", "Italian food", "Sicily sea", "Sardinia beach", "Venice", "Rome travel", "Florence travel",
    "borghi italiani", "viaggio in Italia", "mare Italia", "laghi Italia", "Dolomiti", "cibo italiano",
]
RE_YT = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([\w-]{11})")
CARTELLA = "/videoteca"
ESEMPI = "/ispirazione/esempi-telegram.md"


def _get(chiave, endpoint, **parametri):
    r = requests.get(API + endpoint, params=dict(parametri, key=chiave), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"YouTube {endpoint}: {r.status_code}")
    return r.json()


def _secondi(durata_iso):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", durata_iso or "")
    if not m:
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def cerca_id(chiave, recenti=True):
    """Id dei video corti più visti per ogni ricerca (100 unità di quota per ricerca: 20 ricerche = 2.000 su 10.000)."""
    dopo = (datetime.now(timezone.utc) - timedelta(days=GIORNI)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = []
    for q in RICERCHE:
        parametri = {"part": "id", "q": q, "type": "video", "videoDuration": "short", "order": "viewCount", "maxResults": 25}
        if recenti:
            parametri["publishedAfter"] = dopo
        try:
            dati = _get(chiave, "search", **parametri)
        except RuntimeError as e:
            print(e)
            continue
        ids += [i["id"]["videoId"] for i in dati.get("items", []) if i.get("id", {}).get("videoId")]
    return list(dict.fromkeys(ids))


def dettagli(chiave, ids, origine):
    """Statistiche e dati dei video (1 unità ogni 50 video). Tiene solo ≤ 60 s e ≥ 1 milione."""
    tenuti = []
    for i in range(0, len(ids), 50):
        dati = _get(chiave, "videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]))
        for v in dati.get("items", []):
            vista = int(v.get("statistics", {}).get("viewCount", 0))
            secondi = _secondi(v.get("contentDetails", {}).get("duration"))
            if vista < MINIMO or not secondi or secondi > DURATA_MAX:
                continue
            sn = v["snippet"]
            tenuti.append({
                "link": f"https://www.youtube.com/shorts/{v['id']}", "id": v["id"], "fonte": origine,
                "account": sn.get("channelTitle"), "titolo": sn.get("title"), "pubblicato": sn.get("publishedAt"),
                "visualizzazioni": vista, "like": int(v.get("statistics", {}).get("likeCount", 0) or 0),
                "commenti_n": int(v.get("statistics", {}).get("commentCount", 0) or 0), "durata_s": secondi,
                "descrizione": (sn.get("description") or "")[:600], "tag": (sn.get("tags") or [])[:10],
                "miniatura": ((sn.get("thumbnails") or {}).get("maxres") or (sn.get("thumbnails") or {}).get("high") or {}).get("url"),
                "letto_il": datetime.now(ROMA).isoformat(timespec="seconds"),
            })
    return tenuti


def commenti(chiave, video_id):
    """I 5 commenti più rilevanti (1 unità). Se sono chiusi: lista vuota."""
    try:
        dati = _get(chiave, "commentThreads", part="snippet", videoId=video_id, order="relevance", maxResults=5, textFormat="plainText")
    except RuntimeError:
        return []
    out = []
    for t in dati.get("items", []):
        c = t["snippet"]["topLevelComment"]["snippet"]
        out.append({"testo": (c.get("textDisplay") or "")[:200], "like": c.get("likeCount", 0)})
    return out


def esempi_youtube(dbx):
    try:
        testo = dbx.leggi_testo(ESEMPI)
    except DropboxNonTrovato:
        return []
    return list(dict.fromkeys(RE_YT.findall(testo)))


def principale():
    if os.environ.get("EVENTO") == "schedule" and os.environ.get("TREND", "").strip().lower() not in ("si", "sì"):
        print("Cacciatore di trend spento (variabile TREND). Non faccio niente.")
        return 0
    if os.environ.get("EVENTO") == "schedule" and datetime.now(ROMA).hour != 10:
        print("Partenza programmata fuori dalle 10 di Roma (l'altra copre l'ora legale/solare). Non faccio niente.")
        return 0
    chiave = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not chiave:
        avvisi.invia("🔎 Cacciatore di trend: manca la chiave YouTube (secret YOUTUBE_API_KEY). Guida nell'avanzamento della richiesta 20.")
        return 0
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    video = dettagli(chiave, cerca_id(chiave, recenti=True), "YouTube API, ultimi 90 giorni")
    if len(video) < 10:  # pochi recenti: si allarga a tutti gli anni, segnando la data
        gia = {v["id"] for v in video}
        video += [v for v in dettagli(chiave, cerca_id(chiave, recenti=False), "YouTube API, tutti gli anni") if v["id"] not in gia]
    esempi = esempi_youtube(dbx)
    if esempi:
        gia = {v["id"] for v in video}
        video += [v for v in dettagli(chiave, esempi, "esempio del titolare (/esempio)") if v["id"] not in gia]
    video.sort(key=lambda v: -v["visualizzazioni"])
    video = video[:40]
    for v in video[:20]:
        v["commenti_top"] = commenti(chiave, v["id"])
    oggi = datetime.now(ROMA).strftime("%Y-%m-%d")
    dbx.scrivi_json(f"{CARTELLA}/grezzo-{oggi}.json", {
        "letto_il": datetime.now(ROMA).isoformat(timespec="seconds"), "minimo": MINIMO, "durata_max_s": DURATA_MAX,
        "nota": "Dati letti dall'API ufficiale YouTube. Nessun video scaricato. TikTok Creative Center non leggibile senza login/JavaScript.",
        "video": video})
    righe = [f"• {v['visualizzazioni'] / 1e6:.1f} M · {v['durata_s']} s · {v['titolo'][:60]}\n  {v['link']}" for v in video[:10]]
    avvisi.invia(f"🔎 Cacciatore di trend: {len(video)} video da oltre 1 milione salvati per la videoteca.\n" + "\n".join(righe))
    print(f"Video salvati: {len(video)}")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
