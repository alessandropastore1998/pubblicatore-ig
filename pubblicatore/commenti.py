"""Ponte DM: chi commenta la parola chiave riceve il DM a mano dal titolare, finché il DM automatico non c'è.

Ogni giro legge i commenti dei post e dei Reel degli ultimi giorni (solo API ufficiale: GET /{media-id}/comments,
serve il permesso instagram_business_manage_comments). Per ogni commento NUOVO che contiene la parola chiave
del post manda al titolare su Telegram il nome utente, il commento e il DM pronto da incollare (dm.it / dm.en di post.json).

Dati personali: solo nome utente e testo del commento, salvati in /commenti/ponte/<id>.json per non avvisare due volte.
Nei log non compaiono nomi utente, testi o link.
"""
import os
import re
import sys
from datetime import datetime, timedelta

import requests

from . import main
from .dropbox_client import Dropbox, DropboxNonTrovato
from .instagram import ErroreInstagram, Instagram

GIORNI = int(os.environ.get("COMMENTI_GIORNI", "7"))
CARTELLA_PONTE = "/commenti/ponte"
RE_INGLESE = re.compile(r"\b(the|and|thanks|thank|please|where|how|what|love|beautiful|amazing|info)\b", re.I)


def telegram(testo):
    """Come avvisi.invia ma SENZA scrivere il testo nel log (contiene nomi utente e commenti)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("(Telegram non configurato)")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": testo[:4000], "disable_web_page_preview": True}, timeout=30)
        if r.status_code != 200:
            print(f"Telegram ha risposto {r.status_code}")
    except requests.RequestException as e:
        print(f"Telegram non raggiungibile: {type(e).__name__}")


def post_recenti(dbx):
    """(media_id, cartella, post.json, tipo) dei caroselli e dei Reel pubblicati negli ultimi GIORNI."""
    limite = datetime.now(main.ROMA) - timedelta(days=GIORNI)
    try:
        voci = dbx.elenca("/post")
    except DropboxNonTrovato:
        return []
    risultato = []
    for v in voci:
        if v.get(".tag") != "folder":
            continue
        try:
            giorno = datetime.strptime(v["name"][:10], "%Y-%m-%d").replace(tzinfo=main.ROMA)
        except ValueError:
            continue
        if giorno < limite - timedelta(days=1):
            continue
        base = f"/post/{v['name']}"
        try:
            post = dbx.leggi_json(f"{base}/post.json")
        except (DropboxNonTrovato, ValueError):
            continue
        for nome_file, tipo in (("pubblicato.json", "carosello"), ("reel-pubblicato.json", "reel")):
            try:
                stato = dbx.leggi_json(f"{base}/{nome_file}")
            except (DropboxNonTrovato, ValueError):
                continue
            if stato.get("esito") == "pubblicato" and stato.get("id"):
                risultato.append((stato["id"], stato.get("link", ""), post, tipo))
    return risultato


def gia_segnalato(dbx, comment_id):
    try:
        dbx.leggi_json(f"{CARTELLA_PONTE}/{comment_id}.json")
        return True
    except DropboxNonTrovato:
        return False


def principale():
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    ig = Instagram(os.environ["IG_TOKEN"])
    io = ig.chi_sono()
    mio_nome = (io.get("username") or "").lower()
    media = post_recenti(dbx)
    print(f"Post e Reel da controllare: {len(media)}")
    nuovi = 0
    for media_id, link, post, tipo in media:
        parola = (post.get("parola_chiave") or "").strip()
        if not parola:
            continue
        try:
            dati = ig.chiama("GET", f"{media_id}/comments", fields="id,text,username,timestamp", limit="50")
        except ErroreInstagram as e:
            if "permission" in str(e).lower() or "(codice 10/" in str(e) or "(codice 200/" in str(e):
                print("Permesso per leggere i commenti mancante (instagram_business_manage_comments).")
                if os.environ.get("MODALITA") == "prova":
                    telegram("🧪 Lettore commenti: Instagram non permette ancora di leggere i commenti.\n"
                                 "Serve il permesso instagram_business_manage_comments + nuovo token.")
                return 2
            raise
        for c in dati.get("data", []):
            testo = c.get("text") or ""
            utente = c.get("username") or ""
            if utente.lower() == mio_nome or not re.search(rf"\b{re.escape(parola)}\b", testo, re.I):
                continue
            if gia_segnalato(dbx, c["id"]):
                continue
            inglese = bool(RE_INGLESE.search(testo)) and not re.search(r"\b(grazie|ciao|bellissim|dove)\w*", testo, re.I)
            dm = (post.get("dm") or {}).get("en" if inglese else "it", "")
            telegram(
                f"💬 @{utente} ha commentato «{testo[:200]}» sul {tipo} di {post.get('luogo', '')}\n{link}\n\n"
                f"Mandagli in DM questo testo ({'inglese' if inglese else 'italiano'}), poi rispondi al commento con "
                f"«{(post.get('dm') or {}).get('risposta_pubblica', 'Ti abbiamo scritto in DM ✉️')}»:"
            )
            if dm:
                telegram(dm)
            dbx.scrivi_json(f"{CARTELLA_PONTE}/{c['id']}.json", {
                "media_id": media_id, "utente": utente, "testo": testo[:500], "segnalato_il": main.ora_iso(),
            })
            nuovi += 1
    print(f"Commenti nuovi con la parola chiave: {nuovi}")
    if os.environ.get("MODALITA") == "prova":
        telegram(f"🧪 Lettore commenti OK: controllati {len(media)} post/Reel, {nuovi} commenti nuovi con la parola chiave.")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
