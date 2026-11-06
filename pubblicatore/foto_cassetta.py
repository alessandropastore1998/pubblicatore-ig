"""Cassetta delle foto (richiesta 18): fa VEDERE le candidate alla redazione.

La redazione gira in un ambiente che non raggiunge Dropbox, Pexels né Unsplash per scaricare file: raggiunge solo
raw.githubusercontent.com. Quindi:
1. la redazione scrive /foto-richieste/<cartella>.json (luogo, ricerche, link);
2. questo job, acceso durante le redazioni, cerca e scarica le candidate, fa il controllo tecnico, salva i JPEG in
   /candidate/<cartella>/foto/ con l'elenco candidate.json;
3. mette le anteprime sul ramo "anteprime" del repository: la redazione le scarica da lì, le apre e le GUARDA.
Sul ramo ci sono solo foto con licenza libera in una cartella dal nome casuale; ogni invio sostituisce il precedente.
"""
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import time as ora
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image

from . import foto_controllo, foto_visione
from .dropbox_client import Dropbox, DropboxNonTrovato
from .main import adesso, carica_foto, ora_iso, si

RICHIESTE = "/foto-richieste"
CANDIDATE = "/candidate"
MAX_CANDIDATE = 30
LATO_ANTEPRIMA = 1400
LATO_FOTO = 2160
HOST_AMMESSI = ("images.unsplash.com", "images.pexels.com", "upload.wikimedia.org")
UA = {"User-Agent": "pubblicatore/1.0 (foto con licenza libera)"}
FINESTRE = ((ora(4, 30), ora(7, 20)), (ora(16, 30), ora(20, 10)))
ATTESA = 20          # secondi tra un controllo e l'altro
FINE_LAVORO = 900    # dopo una richiesta evasa, secondi di silenzio prima di chiudere il job
RE_NOME = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9-]{1,60}")
LICENZE_COMMONS = re.compile(r"^(cc0|cc[- ]by(-sa)?[- ]?\d|public domain|pd)", re.I)


# ───────────────────────────── ricerca ─────────────────────────────

def cerca_pexels(ricerca, chiave):
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": chiave}, timeout=30,
                     params={"query": ricerca, "per_page": 15})
    r.raise_for_status()
    return [{"fonte": "Pexels", "licenza": "Pexels License", "url": foto_controllo.url_originale(f["src"]["original"]),
             "pagina": f["url"], "autore": f["photographer"], "cosa_mostra": f.get("alt") or "",
             "larghezza": f["width"], "altezza": f["height"]} for f in r.json().get("photos", [])]


def cerca_unsplash(ricerca, chiave):
    r = requests.get("https://api.unsplash.com/search/photos", headers={"Authorization": f"Client-ID {chiave}"}, timeout=30,
                     params={"query": ricerca, "per_page": 15})
    r.raise_for_status()
    trovate = []
    for f in r.json().get("results", []):
        if f.get("premium") or f.get("plus"):  # mai Unsplash+
            continue
        trovate.append({"fonte": "Unsplash", "licenza": "Unsplash License", "url": foto_controllo.url_originale(f["urls"]["raw"]),
                        "pagina": f["links"]["html"], "autore": f["user"]["name"], "cosa_mostra": f.get("alt_description") or "",
                        "larghezza": f["width"], "altezza": f["height"], "traccia": f["links"].get("download_location", "")})
    return trovate


def cerca_commons(ricerca):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": ricerca, "gsrnamespace": 6, "gsrlimit": 15,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiextmetadatafilter": "LicenseShortName|Artist"})
    r.raise_for_status()
    trovate = []
    for pagina in (r.json().get("query", {}).get("pages") or {}).values():
        info = (pagina.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        licenza = (meta.get("LicenseShortName") or {}).get("value", "")
        if info.get("mime") != "image/jpeg" or not LICENZE_COMMONS.match(licenza):
            continue
        autore = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
        trovate.append({"fonte": "Wikimedia Commons", "licenza": licenza, "url": info["url"],
                        "pagina": info.get("descriptionurl", ""), "autore": autore, "cosa_mostra": pagina.get("title", ""),
                        "larghezza": info.get("width", 0), "altezza": info.get("height", 0)})
    return trovate


def cerca_clip(ricerca, chiave):
    """Clip Pexels che reggono il verticale: già verticali larghe almeno 1080 px, o 4K orizzontali."""
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": chiave}, timeout=30,
                     params={"query": ricerca, "per_page": 15})
    r.raise_for_status()
    clip = []
    for v in r.json().get("videos", []):
        adatti = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")
                  and min(f["width"], round(f["height"] * 9 / 16)) >= 1080]
        if adatti:
            f = min(adatti, key=lambda x: x["width"] * x["height"])
            clip.append({"fonte": "Pexels Video", "licenza": "Pexels License", "video": f["link"], "pagina": v["url"],
                         "autore": (v.get("user") or {}).get("name", ""), "larghezza": f["width"], "altezza": f["height"],
                         "durata": v.get("duration")})
    return clip


def raccogli(richiesta):
    """Candidate da API e link della redazione, senza doppioni, prima quelle abbastanza grandi."""
    chiave_pexels = os.environ.get("PEXELS_API_KEY", "").strip()
    chiave_unsplash = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    trovate, clip = [], []
    for link in richiesta.get("link") or []:
        url = str(link.get("url") or "")
        if urlsplit(url).hostname in HOST_AMMESSI:
            trovate.append(dict(link, url=foto_controllo.url_originale(url)))
    n_link = len(trovate)
    for ricerca in [str(r)[:80] for r in (richiesta.get("ricerche") or [])[:6]]:
        for nome, cerca in (("Pexels", (lambda q: cerca_pexels(q, chiave_pexels)) if chiave_pexels else None),
                            ("Unsplash", (lambda q: cerca_unsplash(q, chiave_unsplash)) if chiave_unsplash else None),
                            ("Commons", cerca_commons)):
            if not cerca:
                continue
            try:
                trovate += cerca(ricerca)
            except (requests.RequestException, ValueError, KeyError) as e:
                print(f"Ricerca {nome} non riuscita ({type(e).__name__}).")
        if chiave_pexels:
            try:
                clip += cerca_clip(ricerca, chiave_pexels)
            except (requests.RequestException, ValueError, KeyError) as e:
                print(f"Ricerca clip non riuscita ({type(e).__name__}).")
    # prima i link della redazione, poi il resto dal lato corto maggiore (sotto soglia non serve nemmeno scaricarle)
    grandi = [f for f in trovate if min(f.get("larghezza", 0), f.get("altezza", 0)) >= foto_controllo.SOGLIE["lato_corto"]]
    tutte = trovate[:n_link] + sorted(grandi, key=lambda f: -min(f["larghezza"], f["altezza"]))
    uniche = list({f["url"]: f for f in tutte}.values())
    return uniche[:MAX_CANDIDATE], list({c["video"]: c for c in clip}.values())[:12]


# ───────────────────────────── lavorazione ─────────────────────────────

def _jpeg(img, lato, qualita):
    copia = img.copy()
    copia.thumbnail((lato, lato), Image.LANCZOS)
    uscita = io.BytesIO()
    copia.save(uscita, "JPEG", quality=qualita, optimize=True)
    return uscita.getvalue()


def lavora_foto(dbx, cartella, candidate, tmp):
    """Scarica, misura, salva. Restituisce le schede delle foto (senza difetti e con difetti, tutte: decide chi guarda)."""
    schede = []
    for n, f in enumerate(candidate, 1):
        try:
            dati = foto_controllo.scarica(f["url"])
            valori = foto_controllo.misura(dati)
            img = foto_controllo.apri(dati)
        except Exception as e:  # noqa: BLE001 — una foto che non si apre non ferma le altre
            print(f"Candidata {n} non aperta ({type(e).__name__}).")
            continue
        nome = f"{n:02d}.jpg"
        (tmp / nome).write_bytes(_jpeg(img, LATO_ANTEPRIMA, 85))
        locale = tmp / f"grande-{nome}"
        locale.write_bytes(_jpeg(img, LATO_FOTO, 90))
        carica_foto(dbx, f"{CANDIDATE}/{cartella}/foto/{nome}", locale)
        locale.unlink()
        schede.append(dict(f, n=n, file=nome, originale=f["url"], valori=valori,
                           larghezza=valori["larghezza"], altezza=valori["altezza"],
                           difetti=foto_controllo.difetti(valori), difetti_reel=foto_controllo.difetti(valori, reel=True)))
    return schede


def anteprima_clip(clip, tmp):
    """Un fotogramma di ogni clip, per guardarla. Le clip si scaricano nel montaggio, qui basta il fotogramma."""
    from .reel_montaggio import ffmpeg_exe

    schede = []
    for n, c in enumerate(clip, 1):
        nome = f"clip-{n:02d}.jpg"
        try:
            subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", "1", "-i", c["video"], "-frames:v", "1",
                            "-vf", f"scale=-2:{LATO_ANTEPRIMA}:force_original_aspect_ratio=decrease", "-q:v", "4", str(tmp / nome)],
                           check=True, timeout=90)
            schede.append(dict(c, file=nome))
        except (subprocess.SubprocessError, OSError) as e:
            print(f"Clip {n} non aperta ({type(e).__name__}).")
    return schede


def pubblica_anteprime(tmp, token):
    """Mette le anteprime sul ramo "anteprime" (un solo commit, sostituito ogni volta: il repository non cresce)."""
    lavoro = tmp / "ramo"
    (lavoro / token).mkdir(parents=True)
    for f in tmp.glob("*.jpg"):
        f.rename(lavoro / token / f.name)
    remoto = f"https://x-access-token:{os.environ['GITHUB_TOKEN']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"

    def git(*argomenti):
        subprocess.run(["git", "-c", "user.name=pubblicatore", "-c", "user.email=noreply@users.noreply.github.com", *argomenti],
                       cwd=lavoro, check=True, capture_output=True, timeout=180)

    git("init", "-q", "-b", "anteprime")
    git("add", ".")
    git("commit", "-q", "-m", "Anteprime")
    git("push", "-q", "--force", remoto, "anteprime")
    return f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/anteprime/{token}/"


def evadi(dbx, cartella, richiesta):
    print("Richiesta di foto trovata.")
    tmp = Path(tempfile.mkdtemp())
    candidate, clip = raccogli(richiesta)
    schede = lavora_foto(dbx, cartella, candidate, tmp)
    schede_clip = anteprima_clip(clip, tmp)
    token = secrets.token_hex(8)
    base = pubblica_anteprime(tmp, token) if schede or schede_clip else ""
    esito = {
        "cartella": cartella, "luogo": richiesta.get("luogo"), "preparato_il": ora_iso(),
        "come_guardare": "Scarica ogni anteprima con curl da 'base' + file, poi aprila col tool Read: solo così una foto è vista.",
        "base": base, "foto": schede, "clip": schede_clip,
        "sane": sum(1 for s in schede if not s["difetti"]), "sane_per_reel": sum(1 for s in schede if not s["difetti_reel"]),
        "soglie": foto_controllo.SOGLIE,
    }
    if si(os.environ.get("VISIONE_CLAUDE")):
        foto_visione.valuta(dbx, cartella, esito, base)
    dbx.scrivi_json(f"{CANDIDATE}/{cartella}/candidate.json", esito)
    print(f"Candidate pronte: {len(schede)} foto ({esito['sane']} senza difetti), {len(schede_clip)} clip.")


def giro(dbx):
    """Evade le richieste non ancora evase. Restituisce quante."""
    try:
        voci = dbx.elenca(RICHIESTE)
    except DropboxNonTrovato:
        return 0
    fatte = 0
    for v in voci:
        nome = v["name"].removesuffix(".json")
        if v.get(".tag") != "file" or not v["name"].endswith(".json") or not RE_NOME.fullmatch(nome):
            continue
        try:
            dbx.leggi_json(f"{CANDIDATE}/{nome}/candidate.json")
            continue
        except (DropboxNonTrovato, ValueError):
            pass
        try:
            evadi(dbx, nome, dbx.leggi_json(f"{RICHIESTE}/{v['name']}"))
        except Exception as e:  # noqa: BLE001 — la redazione deve sapere che non arriva niente, e il job deve continuare
            print(f"Richiesta non evasa ({type(e).__name__}).")
            dbx.scrivi_json(f"{CANDIDATE}/{nome}/candidate.json", {"cartella": nome, "preparato_il": ora_iso(), "foto": [], "clip": [],
                                                                   "errore": f"{type(e).__name__}: {str(e)[:200]}"})
        fatte += 1
    return fatte


def in_finestra():
    t = adesso().time()
    return any(da <= t < a for da, a in FINESTRE)


def principale():
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    manuale = os.environ.get("MODALITA", "") == "subito"
    if manuale:
        print(f"Richieste evase: {giro(dbx)}")
        return 0
    if not in_finestra():
        print("Fuori dalle finestre delle redazioni. Mi fermo.")
        return 0
    ultimo = None
    while in_finestra():
        if giro(dbx):
            ultimo = time.time()
        if ultimo and time.time() - ultimo > FINE_LAVORO:
            print("Richieste evase: chiudo.")
            break
        time.sleep(ATTESA)
    return 0


if __name__ == "__main__":
    sys.exit(principale())
