"""Prove a vuoto dei Reel (richieste 19 e 20): monta più varianti dello STESSO luogo con lo STESSO brano e le manda
su Telegram al titolare. Non pubblica niente, non crea contenitori su Instagram e non scrive nulla su Dropbox.

Le istruzioni stanno in un file di Dropbox (/prove/<nome>.json nella cartella dell'app), così nel repository pubblico
non compaiono nomi né luoghi. Esempio:
{
  "sorgente": "riserve/montepulciano",          # cartella con post.json (riserve/… o post/…)
  "varianti": ["fermo", "lento", "gancio", "ricetta"],
  "brano": "id-del-brano",                       # facoltativo: altrimenti uno adatto dalla libreria
  "gancio": "Frase di massimo sette parole",     # per la variante "gancio"
  "ricetta": {"nome": "...", "scheda": "...", "scene": [{"tipo": "panorama", "secondi": 1.5}, ...],
              "genere_musica": "acustico", "gancio": "..." }
}
Varianti: fermo = foto ferme senza scritte · lento = avvicinamento lentissimo (3,5%) senza scritte ·
gancio = foto ferme + frase sulla scena 1 · ricetta = scaletta copiata da un video di successo, con le NOSTRE foto.
"""
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

from . import avvisi, reel, reel_montaggio_v2, reel_musica
from .dropbox_client import Dropbox

TIPI = {  # tipo di inquadratura della ricetta → parole che lo riconoscono nelle nostre foto (ruolo o cosa_mostra)
    "panorama": r"copertina|panoram|veduta|rupe|borgo|skyline|lago|valle|dall'alto|tramonto|alba",
    "drone": r"dall'alto|aere|panoram|veduta",
    "dettaglio": r"dettagl|portone|finestr|vicol|insegn|scalin|fiori",
    "camminata": r"vicol|calle|via |strada|vicolo|scalinat|persona|di spalle",
    "persone": r"persona|gente|di spalle|coppia",
    "cibo": r"cibo|piatt|pasta|pizza|vino|gelat|dolc",
    "monumento": r"chiesa|duomo|castello|torre|palazzo|fortezza|museo|attrazione",
    "acqua": r"lago|mare|canale|fiume|riflessi|porto",
}


def _scene_da_ricetta(post, sane, ricetta):
    """Le scene della ricetta nell'ordine dei suoi tipi di inquadratura, scegliendo ogni volta la nostra foto sana più
    adatta e mai ripetuta (salvo il loop finale). Restituisce (scene, righe di adattamento)."""
    per_url = {f.get("originale") or f["url"]: f for f in post["foto"]}
    libere = [u for u in sane if u in per_url]
    scene, note = [], []
    passi = [s for s in ricetta.get("scene", []) if isinstance(s, dict)]
    for i, passo in enumerate(passi):
        tipo = str(passo.get("tipo", "")).lower()
        if i == len(passi) - 1 and scene and tipo in ("loop", "uguale alla prima"):
            scene.append(dict(scene[0], secondi=passo.get("secondi", 1.5)))
            continue
        if not libere:
            note.append(f"scena {i + 1} ({tipo}): foto finite, scena tolta")
            continue
        regola = re.compile(TIPI.get(tipo, re.escape(tipo) or "."), re.I)
        scelta = next((u for u in libere if regola.search(f"{per_url[u].get('ruolo', '')} {per_url[u].get('cosa_mostra', '')}")), None)
        if scelta is None:
            scelta = libere[0]
            note.append(f"scena {i + 1}: nessuna nostra foto di tipo «{tipo}», uso «{per_url[scelta].get('cosa_mostra', '')[:40]}»")
        libere.remove(scelta)
        f = per_url[scelta]
        scene.append({"tipo": "foto", "foto": f["url"], "originale": scelta, "fuoco": f.get("fuoco"),
                      "secondi": float(passo.get("secondi", 1.5))})
    if len(scene) > 1 and scene[-1].get("originale") != scene[0].get("originale"):
        scene.append(dict(scene[0], secondi=scene[-1].get("secondi", 1.5)))  # loop, come tutti i nostri Reel
        note.append("aggiunto il finale in loop sulla prima foto")
    if ricetta.get("gancio"):
        scene[0]["gancio"] = ricetta["gancio"]
    return scene, note


def _brano(dbx, prova, post, genere=None):
    libreria = reel.leggi_json_o_none(dbx, reel.LIBRERIA_MUSICA) or {}
    brani = [b for b in libreria.get("brani", []) if b.get("id")]
    if not brani:
        return int(hashlib.sha1(post["luogo"].encode()).hexdigest(), 16) % 1000
    scelto = next((b for b in brani if b["id"] == prova.get("brano")), None)
    if not scelto and genere:
        adatti = [b for b in brani if str(b.get("genere", "")).lower() == genere.lower()]
        scelto = adatti[int(hashlib.sha1(post["luogo"].encode()).hexdigest(), 16) % len(adatti)] if adatti else None
    if not scelto:
        usati = (reel.leggi_json_o_none(dbx, reel.MUSICA_USATA) or {}).get("ultimi", [])
        adatti = reel.brani_liberi(brani, usati)
        scelto = adatti[int(hashlib.sha1(post["luogo"].encode()).hexdigest(), 16) % len(adatti)]
    return dict(reel.collega_brano(dbx, scelto), alternative=[])


def principale():
    nome = os.environ.get("FILE_PROVA", "").strip()
    if not re.fullmatch(r"[\w.-]+\.json", nome):
        print("Nome del file di prova non valido.")
        return 1
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    prova = dbx.leggi_json(f"/prove/{nome}")
    sorgente = "/" + str(prova["sorgente"]).strip("/")
    post = dbx.leggi_json(f"{sorgente}/post.json")
    os.environ.setdefault("CONTROLLO_FOTO", "blocca")
    # stesse foto per tutte le varianti: le foto sane e verticali del Reel automatico (nessuna scrittura su Dropbox)
    base = reel.scene_da_post_v2(post)
    base, _, _ = reel.scene_sane(dbx, sorgente, post, base, "automatico", "", scrivi=False)
    scartate = getattr(reel.scene_sane, "bocciate", {})
    musica = _brano(dbx, prova, post)
    righe_scarto = [f"• {u.split('/')[-1][:40]}: {'; '.join(m)}" for u, m in scartate.items()]
    if righe_scarto:
        avvisi.invia(f"🧪 Prova Reel {post['luogo']}: foto escluse dal Reel\n" + "\n".join(righe_scarto[:12]))
    esiti = 0
    for variante in prova.get("varianti", ["fermo", "lento", "gancio"]):
        scene, note, brano, lento, gancio = [dict(s) for s in base], [], musica, False, False
        if variante == "lento":
            lento = True
        elif variante == "gancio":
            gancio = True
            scene[0]["gancio"] = prova.get("gancio") or ""
        elif variante == "ricetta":
            ricetta = prova.get("ricetta") or {}
            sane = [s.get("originale") or s["foto"] for s in base[:-1]]
            sane += [f.get("originale") or f["url"] for f in post["foto"]
                     if (f.get("originale") or f["url"]) not in sane and (f.get("originale") or f["url"]) not in scartate]
            scene, note = _scene_da_ricetta(post, sane, ricetta)
            gancio = bool(ricetta.get("gancio"))
            if ricetta.get("genere_musica"):
                brano = _brano(dbx, {}, post, ricetta["genere_musica"])
        if len(scene) < reel.SCENE_MINIME_DOPO_CONTROLLO:
            avvisi.invia(f"🧪 Prova {variante}: troppo poche foto sane ({len(scene)} scene). Saltata.")
            continue
        uscita = Path(tempfile.mkdtemp()) / f"{variante}.mp4"
        durata = reel_montaggio_v2.monta(scene, uscita, seme_musica=brano, lento=lento, con_gancio=gancio)
        stat = reel_montaggio_v2.monta.statistiche
        usato = reel_musica.brano_usato
        titolo = {"fermo": "1️⃣ FOTO FERME, senza scritte", "lento": "2️⃣ AVVICINAMENTO LENTISSIMO (3,5%), senza scritte",
                  "gancio": "3️⃣ FOTO FERME + frase sulla scena 1", "ricetta": "🍳 REEL CON RICETTA"}.get(variante, variante)
        testo = (f"🧪 PROVA a vuoto, NON pubblicato · {post['luogo']}\n{titolo}\n"
                 f"{durata:.1f} s · {stat['foto']} scene foto · movimento {stat['movimento']} · "
                 f"scena 1 {stat['scena1_secondi']} s · brano: {usato.get('titolo') if usato else 'musica originale'}")
        if variante == "ricetta":
            testo += f"\nRicetta: {(prova.get('ricetta') or {}).get('nome', '')}"
            if note:
                testo += "\nAdattato: " + "; ".join(note)[:400]
        reel.invia_video_telegram(uscita, testo)
        esiti += 1
    avvisi.invia(f"🧪 Prova Reel {post['luogo']}: {esiti} video mandati qui sopra. Niente è stato pubblicato. "
                 "Per scegliere rispondi a Claude (1, 2 o 3).")
    return 0 if esiti else 1


if __name__ == "__main__":
    sys.exit(principale())
