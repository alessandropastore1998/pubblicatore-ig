"""Pubblicatore dei Reel — SEPARATO dal pubblicatore dei caroselli (main.py).

Per ogni fascia (mattina / sera) esce un Reel sullo stesso luogo e con la stessa parola chiave del carosello,
qualche minuto dopo il carosello (07:40 / 19:40). Se il Reel non si monta o Instagram lo rifiuta,
il carosello non ne risente: arriva solo un avviso su Telegram.

Scene del Reel:
- se nella cartella del post c'è reel.json (scritto dalla redazione o a mano) si usano quelle;
- altrimenti il Reel si costruisce da post.json (titolo + 3 consigli della scheda + chiusura con la parola chiave).

Il video montato viene salvato nella cartella del post (reel.mp4 + reel-copertina.jpg) e Instagram lo scarica
da un link temporaneo di Dropbox (4 ore). Se il contenuto non cambia, il video già visto in anteprima è quello pubblicato.

Modalità (variabile MODALITA):
  auto        → avvio programmato: capisce la fascia dall'ora di Roma, monta, manda l'anteprima, aspetta l'orario e pubblica
  prova       → monta, carica, crea il container su Instagram e aspetta che sia pronto, ma NON pubblica
  pubblica-adesso → pubblica subito il Reel del post indicato (data/fascia)
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from datetime import time as ora
from pathlib import Path

import requests

from . import avvisi, main, reel_montaggio, reel_montaggio_v2
from .dropbox_client import CONTENT, Dropbox, DropboxNonTrovato
from .instagram import ErroreInstagram, Instagram

ROMA = main.ROMA
VERSIONE_MONTAGGIO = 1  # cambiala quando cambia la grafica: i video già montati vengono rifatti


def montaggio():
    """v1 (foto ferme con zoom leggero) o v2 (richiesta 14). Si cambia con la variabile MONTAGGIO_REEL: per tornare
    indietro basta rimetterla a v1."""
    return "v2" if os.environ.get("MONTAGGIO_REEL", "").strip().lower() == "v2" else "v1"

# Per ogni fascia: finestra in cui una partenza è valida, ora del Reel, fino a quando aspettare il carosello.
FASCE = {
    "mattina": {"inizio": ora(6, 55), "reel": ora(7, 40), "attendi_carosello": ora(8, 20), "fine": ora(10, 30)},
    "sera": {"inizio": ora(18, 55), "reel": ora(19, 40), "attendi_carosello": ora(20, 20), "fine": ora(22, 30)},
}
DOPO_CAROSELLO = timedelta(minutes=5)  # il Reel esce almeno 5 minuti dopo il carosello
MAX_DIDASCALIA = 2200
RE_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⃣⬀-⯿]")


def adesso():
    return datetime.now(ROMA)


def ora_iso():
    return adesso().isoformat(timespec="seconds")


def senza_emoji(testo):
    return re.sub(r"[ \t]{2,}", " ", RE_EMOJI.sub("", testo)).strip()


# ───────────────────────────── Dropbox (solo per i Reel) ─────────────────────────────

def carica_file(dbx, percorso, file_locale):
    headers = dict(dbx._auth)
    headers["Dropbox-API-Arg"] = json.dumps({"path": percorso, "mode": "overwrite", "mute": True})
    headers["Content-Type"] = "application/octet-stream"
    with open(file_locale, "rb") as f:
        r = requests.post(CONTENT + "files/upload", headers=headers, data=f, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Dropbox: caricamento non riuscito ({r.status_code})")


def link_temporaneo(dbx, percorso):
    """Link diretto pubblico valido 4 ore (non serve il permesso di condivisione)."""
    return dbx._rpc("files/get_temporary_link", {"path": percorso})["link"]


def leggi_json_o_none(dbx, percorso):
    try:
        return dbx.leggi_json(percorso)
    except (DropboxNonTrovato, ValueError):
        return None


# ───────────────────────────── scene e didascalia ─────────────────────────────

def _riga_scheda(descrizione, *parole):
    """Testo dopo i due punti della prima riga della scheda pratica che contiene una delle parole."""
    for riga in descrizione.split("\n"):
        pulita = senza_emoji(riga)
        for parola in parole:
            if pulita.lower().startswith(parola.lower()) and ":" in pulita:
                return pulita.split(":", 1)[1].strip()
            if parola == "⚠" and riga.strip().startswith("⚠"):
                return pulita
    return None


def gancio(post):
    prima = senza_emoji(post["descrizione"].strip().split("\n")[0])
    if len(prima) > 90 and "," in prima[:90]:
        prima = prima[: prima[:90].rfind(",")]
    return prima


def scene_da_post(post):
    """Reel automatico: titolo, 3 consigli presi dalla scheda pratica, chiusura con la parola chiave."""
    foto = [f["url"] for f in post["foto"]]
    descr = post["descrizione"].split("— 🇬🇧 —")[0]
    consigli = []
    for titolo, parole in (("La dritta", ("Dritta",)), ("Quando andare", ("Quando",)), ("Occhio", ("⚠",)),
                           ("Come arrivare", ("Come",)), ("Quanto tempo", ("Quanto",))):
        testo = _riga_scheda(descr, *parole)
        if testo and len(consigli) < 3:
            consigli.append((titolo, testo[:110]))
    scene = [{
        "tipo": "titolo", "foto": foto[0], "etichetta": post.get("regione", ""),
        "testo": post["luogo"], "sottotesto": gancio(post), "secondi": 3.0,
    }]
    posti_foto = [1, 3, 5, 2, 4]
    for i, (titolo, testo) in enumerate(consigli):
        scene.append({
            "tipo": "consiglio", "foto": foto[posti_foto[i] % len(foto)],
            "etichetta": f"{len(consigli)} cose da sapere · {i + 1}", "testo": titolo, "sottotesto": testo, "secondi": 3.3,
        })
    scene.append({
        "tipo": "chiusura", "foto": foto[-1], "etichetta": "Vuoi i link? Commenta",
        "testo": post.get("parola_chiave", "").upper(), "sottotesto": "e ti mandiamo tutto in DM", "secondi": 3.4,
    })
    return scene


def _nomi_riga(descrizione, emoji):
    """Nomi della riga del carosello che inizia con l'emoji (es. "🍝 A · B · C"), parte italiana."""
    for riga in descrizione.split("— 🇬🇧 —")[0].split("\n"):
        if riga.strip().startswith(emoji):
            return [n.strip() for n in riga.strip()[len(emoji):].split("·") if n.strip()]
    return []


def locali_da_post(post):
    """I locali del carosello (regola commerciale, richiesta 14): (ristoranti, hotel), ognuno {nome, perche}.
    I nomi sono quelli esatti del carosello; il "perché" c'è solo se la redazione lo scrive in post.json → "locali"
    (formato v5). Niente di inventato: senza "perché" resta solo il nome."""
    perche = {}
    for loc in post.get("locali") or []:
        if isinstance(loc, dict) and loc.get("nome"):
            perche[loc["nome"].strip().lower()] = senza_emoji(str(loc.get("perche") or ""))[:110]

    def elenco(emoji):
        return [{"nome": n, "perche": perche.get(n.lower(), "")} for n in _nomi_riga(post["descrizione"], emoji)][:3]

    return elenco("🍝"), elenco("🛏")


def _elenco(nomi, e="e"):
    """"A, B e C" (nomi esatti del carosello)."""
    return nomi[0] if len(nomi) == 1 else ", ".join(nomi[:-1]) + f" {e} " + nomi[-1]


def _consiglio_locali(locali):
    """Nomi, con il "perché" tra parentesi solo se la redazione l'ha scritto (campo locali di post.json)."""
    return [l["nome"] + (f" ({l['perche']})" if l["perche"] else "") for l in locali]


def didascalia_da_post(post):
    """Gancio → atmosfera + 📍 → dove ci siederemmo / dormiremmo noi (i locali del carosello, come consiglio da amico)
    → salva + parola chiave → invito discreto a farsi prenotare da noi → 🇬🇧 con i nomi → crediti → hashtag."""
    descr = post["descrizione"]
    parola = post.get("parola_chiave", "").upper()
    hashtag = " ".join(main.RE_HASHTAG.findall(descr)[: main.MAX_HASHTAG])
    crediti = next((r.strip() for r in descr.split("\n") if r.strip().startswith("📸")), "")
    luogo = f"📍 {post['luogo']}" + (f", {post['regione']}" if post.get("regione") else "")
    consigli = sum(1 for s in scene_da_post(post) if s["tipo"] == "consiglio")
    ristoranti, hotel = locali_da_post(post)
    # La prima riga deve essere diversa da quella del carosello (i controlli "già pubblicato?" confrontano l'inizio).
    righe = [f"{post['luogo']}: {consigli} cose da sapere prima di andare 👇", "", descr.strip().split("\n")[0], luogo]
    if ristoranti or hotel:
        righe.append("")
    if ristoranti:
        righe.append(f"🍝 Se ci vai, noi ci siederemmo qui: {_elenco(_consiglio_locali(ristoranti))}.")
    if hotel:
        righe.append(f"🛏 E per la notte: {_elenco(_consiglio_locali(hotel), 'o')}.")
    righe += [
        "",
        f"Salvalo per il viaggio 📌 e commenta {parola}: ti mandiamo tutti i link in DM 💌",
        "Vuoi il tavolo giusto o un'esperienza su misura? Scrivici in DM, ci pensiamo noi ✨",
        "",
        f"🇬🇧 {post['luogo']}: {consigli} things to know before you go.",
    ]
    if ristoranti:
        righe.append(f"🍝 Where we'd eat: {_elenco([l['nome'] for l in ristoranti], 'and')}.")
    if hotel:
        righe.append(f"🛏 Where we'd stay: {_elenco([l['nome'] for l in hotel], 'or')}.")
    righe += [f"Comment {parola} for all the links, or DM us and we'll take care of the booking ✨", "", "🎵 Musica originale"]
    if crediti:
        righe.append(crediti)
    righe += ["", hashtag]
    return "\n".join(righe).strip()


def mancano_locali(post, didascalia):
    """Nomi dei locali del carosello che non compaiono nella descrizione del Reel (regola commerciale)."""
    ristoranti, hotel = locali_da_post(post)
    return [l["nome"] for l in ristoranti + hotel if l["nome"] not in didascalia]


def prepara_contenuto(dbx, cartella, post):
    """Restituisce (scene, didascalia, seme_musica, origine)."""
    personalizzato = leggi_json_o_none(dbx, f"{cartella}/reel.json")
    if personalizzato and personalizzato.get("scene"):
        scene = personalizzato["scene"]
        didascalia = personalizzato.get("didascalia") or didascalia_da_post(post)
        if mancano_locali(post, didascalia):
            # regola commerciale: senza i locali del carosello (con gli stessi nomi) si usa la descrizione automatica
            print(f"La descrizione di reel.json non nomina {len(mancano_locali(post, didascalia))} locali del carosello: uso quella automatica.")
            didascalia = didascalia_da_post(post)
        seme = int((personalizzato.get("musica") or {}).get("seme", 1))
        return scene, didascalia, seme, "reel.json"
    seme = int(hashlib.sha1(post["luogo"].encode()).hexdigest(), 16) % 1000
    if montaggio() == "v2":
        return scene_da_post_v2(post), didascalia_da_post(post), seme, "automatico v2 da post.json"
    return scene_da_post(post), didascalia_da_post(post), seme, "automatico da post.json"


def impronta(scene, seme):
    chiave = {"v": VERSIONE_MONTAGGIO, "scene": scene, "seme": seme}
    if montaggio() == "v2":  # i video v1 già montati restano validi con il v1
        chiave["montaggio"] = "v2"
    dati = json.dumps(chiave, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(dati.encode()).hexdigest()[:16]


def monta_o_riusa(dbx, cartella, scene, seme):
    """Monta il video (o riusa quello già montato con lo stesso contenuto). Restituisce (montato, file_locale|None)."""
    firma = impronta(scene, seme)
    gia = leggi_json_o_none(dbx, f"{cartella}/reel-montato.json")
    if gia and gia.get("impronta") == firma and not main.si(os.environ.get("RIMONTA")):
        print("Video già montato con lo stesso contenuto: lo riuso.")
        return gia, None
    tmp = Path(tempfile.mkdtemp())
    video, cover = tmp / "reel.mp4", tmp / "reel-copertina.jpg"
    inizio = time.time()
    modulo = reel_montaggio_v2 if montaggio() == "v2" else reel_montaggio
    durata = modulo.monta(scene, video, seme_musica=seme)
    modulo.copertina(scene[0], cover)
    print(f"Reel montato ({montaggio()}): {durata:.1f} s, {video.stat().st_size // 1024} KB, in {time.time() - inizio:.0f} s.")
    carica_file(dbx, f"{cartella}/reel.mp4", video)
    carica_file(dbx, f"{cartella}/reel-copertina.jpg", cover)
    montato = {"impronta": firma, "durata": round(durata, 1), "byte": video.stat().st_size, "montato_il": ora_iso(),
               "montaggio": montaggio()}
    dbx.scrivi_json(f"{cartella}/reel-montato.json", montato)
    return montato, video


def scarica_video(dbx, cartella):
    tmp = Path(tempfile.mkdtemp()) / "reel.mp4"
    r = requests.get(link_temporaneo(dbx, f"{cartella}/reel.mp4"), timeout=300)
    r.raise_for_status()
    tmp.write_bytes(r.content)
    return tmp


def invia_video_telegram(file_video, testo):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("(Telegram non configurato)")
        return
    try:
        with open(file_video, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendVideo",
                              data={"chat_id": chat, "caption": testo[:1000], "supports_streaming": "true"},
                              files={"video": ("reel.mp4", f, "video/mp4")}, timeout=300)
        if r.status_code != 200:
            print(f"Telegram (video) ha risposto {r.status_code}")
            avvisi.invia(testo + "\n(Non sono riuscito a mandare il video su Telegram.)")
    except requests.RequestException as e:
        print(f"Telegram non raggiungibile: {type(e).__name__}")


# ───────────────────────────── Instagram ─────────────────────────────

def crea_container_reel(ig, user_id, video_url, cover_url, didascalia):
    return ig.chiama("POST", f"{user_id}/media", media_type="REELS", video_url=video_url, cover_url=cover_url,
                     caption=didascalia, share_to_feed="true")["id"]


def attendi_video(ig, container_id, max_secondi=900):
    """I video richiedono più tempo delle foto: controllo ogni 15 secondi fino a 15 minuti."""
    limite = time.time() + max_secondi
    while time.time() < limite:
        time.sleep(15)
        dati = ig.chiama("GET", container_id, fields="status_code,status")
        stato = dati.get("status_code")
        if stato == "FINISHED":
            return
        if stato in ("ERROR", "EXPIRED"):
            raise ErroreInstagram(f"Instagram ha rifiutato il video: {stato} {dati.get('status', '')}")
    raise ErroreInstagram(f"video non pronto dopo {max_secondi // 60} minuti")


# ───────────────────────────── scelta del post ─────────────────────────────

def post_della_fascia(dbx, data, fascia):
    """Cartella del carosello di questa fascia (quello pubblicato se c'è) e se è già pubblicato."""
    cartella = main.trova_cartella(dbx, data, fascia)
    if not cartella:
        return None, None
    return cartella, main.quando_pubblicato(dbx, cartella)


def reel_gia_pubblicato(dbx, data, fascia):
    """Mai due Reel nella stessa fascia: controlla tutte le cartelle di quella data e fascia."""
    try:
        voci = dbx.elenca(main.CARTELLA_POST)
    except DropboxNonTrovato:
        return None
    for v in voci:
        if v.get(".tag") != "folder" or not v["name"].startswith(f"{data}-"):
            continue
        base = f"{main.CARTELLA_POST}/{v['name']}"
        stato = leggi_json_o_none(dbx, f"{base}/reel-pubblicato.json")
        if stato and stato.get("esito") in ("pubblicato", "in_corso"):
            post = leggi_json_o_none(dbx, f"{base}/post.json") or {}
            if str(post.get("fascia") or main.FASCIA_PREDEFINITA).lower() == fascia:
                return stato
    return None


def fascia_di_adesso():
    t = adesso().time()
    for nome, f in FASCE.items():
        if f["inizio"] <= t < f["fine"]:
            return nome
    return None


def aspetta_fino(obiettivo):
    secondi = (obiettivo - adesso()).total_seconds()
    if secondi > 0:
        print(f"Aspetto le {obiettivo:%H:%M} ({int(secondi)} s).")
        time.sleep(secondi)


# ───────────────────────────── principale ─────────────────────────────

def principale():
    modalita = os.environ.get("MODALITA", "").strip() or "auto"
    if os.environ.get("CARTELLA", "post") == "test":
        if modalita != "prova":
            print("La cartella test/ si usa solo con la modalità prova.")
            return 1
        main.CARTELLA_POST = "/test"
    if modalita == "auto" and main.si(os.environ.get("PAUSA_REEL")):
        print("Reel in PAUSA (variabile PAUSA_REEL). Non faccio niente.")
        return 0
    if modalita == "auto" and main.si(os.environ.get("PAUSA")):
        print("Pubblicazione in PAUSA (variabile PAUSA). Non faccio niente.")
        return 0

    fascia = os.environ.get("FASCIA", "")
    fascia = fascia if fascia in FASCE else None
    if modalita == "auto":
        fascia = fascia_di_adesso()
        if not fascia:
            print(f"Sono le {adesso():%H:%M} a Roma: fuori dalle finestre dei Reel. Mi fermo.")
            return 0
    data = os.environ.get("DATA_POST", "").strip() or adesso().strftime("%Y-%m-%d")
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])

    if modalita != "prova":
        gia = reel_gia_pubblicato(dbx, data, fascia or "")
        if gia and fascia:
            print(f"Reel della {fascia} del {data} già pubblicato o in corso. Non faccio niente.")
            return 0

    if fascia:
        cartella, pubblicato = post_della_fascia(dbx, data, fascia)
        # Redazione in ritardo (20/10: post salvato alle 19:51): aspetto che il post compaia, come per il carosello.
        while modalita == "auto" and not cartella and adesso().time() < FASCE[fascia]["attendi_carosello"]:
            print(f"{adesso():%H:%M} il post della {fascia} non c'è ancora, ricontrollo tra 1 minuto…")
            time.sleep(60)
            cartella, pubblicato = post_della_fascia(dbx, data, fascia)
    else:
        cartella, pubblicato = main.trova_cartella(dbx, data, None), None
    if not cartella:
        if modalita == "auto":
            print(f"Nessun post della {fascia} per il {data}: niente Reel per ora.")
            return 0
        avvisi.invia(f"❌ Reel del {data}: non trovo il post ({fascia or 'qualsiasi fascia'}).")
        return 1
    percorso = f"{main.CARTELLA_POST}/{cartella}"
    print("Post trovato ✅")
    try:
        return lavora(dbx, percorso, data, fascia, modalita, pubblicato)
    except Exception as e:  # noqa: BLE001 — il Reel non deve mai fermare nient'altro: avviso e basta
        motivo = str(e) or type(e).__name__
        print("Errore (dettagli su Telegram)")
        if modalita != "prova":
            try:
                dbx.scrivi_json(f"{percorso}/reel-pubblicato.json", {"esito": "errore", "data_ora": ora_iso(), "errore": motivo[:300]})
            except Exception:  # noqa: BLE001
                pass
        titolo = "❌ 🧪 PROVA del Reel fallita" if modalita == "prova" else "❌ Reel NON pubblicato"
        avvisi.invia(f"{titolo} ({cartella})\n{motivo[:500]}\nIl carosello non è toccato.")
        return 1


def prepara(dbx, percorso):
    """Legge il post, monta (o riusa) il video. Restituisce un dict con tutto quello che serve per pubblicare."""
    post = dbx.leggi_json(f"{percorso}/post.json")
    scene, didascalia, seme, origine = prepara_contenuto(dbx, percorso, post)
    if len(didascalia) > MAX_DIDASCALIA:
        raise main.Stop(f"descrizione del Reel troppo lunga ({len(didascalia)} caratteri)")
    print(f"Scene: {len(scene)} ({origine}); descrizione {len(didascalia)} caratteri; musica originale n. {seme}.")
    montato, video_locale = monta_o_riusa(dbx, percorso, scene, seme)
    return {"percorso": percorso, "post": post, "scene": scene, "didascalia": didascalia, "origine": origine,
            "montato": montato, "video_locale": video_locale}


def invia_foto_telegram(file_foto, testo):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        with open(file_foto, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto", data={"chat_id": chat, "caption": testo[:1000]},
                              files={"photo": ("carosello.jpg", f, "image/jpeg")}, timeout=120)
        if r.status_code != 200:
            print(f"Telegram (foto) ha risposto {r.status_code}")
    except requests.RequestException as e:
        print(f"Telegram non raggiungibile: {type(e).__name__}")


def manda_collage(post, quando):
    """Anteprima del carosello (A5): collage delle foto. Se non riesce, pazienza: non blocca niente."""
    try:
        tmp = Path(tempfile.mkdtemp()) / "carosello.jpg"
        reel_montaggio.collage_carosello([main.immagini.normalizza_url(f["url"]) for f in post["foto"]], tmp)
        invia_foto_telegram(tmp, f"🖼 Anteprima carosello: {post['luogo']} ({len(post['foto'])} foto) · {quando}")
    except Exception as e:  # noqa: BLE001
        print(f"Collage del carosello non riuscito ({type(e).__name__})")


def manda_anteprima(dbx, r, fascia):
    if r["video_locale"] or not leggi_json_o_none(dbx, f"{r['percorso']}/reel-anteprima.json"):
        if not main.quando_pubblicato(dbx, r["percorso"].split("/")[-1]):
            manda_collage(r["post"], f"esce alle {main.FASCE[fascia]['pubblica']:%H:%M}")
        f = r["video_locale"] or scarica_video(dbx, r["percorso"])
        invia_video_telegram(f, f"🎬 Anteprima Reel: {r['post']['luogo']} ({fascia}). Esce verso le "
                                f"{FASCE[fascia]['reel']:%H:%M}, dopo il carosello.\n"
                                "Per fermarlo: GitHub → Actions → «Pubblica il Reel» → apri l'esecuzione in corso → Cancel run.")
        dbx.scrivi_json(f"{r['percorso']}/reel-anteprima.json", {"inviata": ora_iso()})


def cerca_reel_recente(ig, user_id, didascalia):
    """Il Reel risulta già uscito (es. risposta persa dopo media_publish)? Cerca negli ultimi media delle ultime 3 ore."""
    inizio = didascalia[:80].strip()
    limite = adesso() - timedelta(hours=3)
    dati = ig.chiama("GET", f"{user_id}/media", fields="id,caption,timestamp,permalink,media_product_type", limit="6")
    for m in dati.get("data", []):
        quando = datetime.strptime(m.get("timestamp", "1970-01-01T00:00:00+0000"), "%Y-%m-%dT%H:%M:%S%z")
        if (m.get("media_product_type") == "REELS" and quando >= limite
                and (m.get("caption") or "").strip().startswith(inizio)):
            return m
    return None


def lavora(dbx, percorso, data, fascia, modalita, pubblicato):
    r = prepara(dbx, percorso)
    ig = Instagram(os.environ["IG_TOKEN"])
    user_id = ig.chi_sono()["user_id"]

    if modalita == "auto":
        manda_anteprima(dbx, r, fascia)
        # Il Reel segue il carosello: aspetto che esca (può essere anche un post diverso, es. la riserva delle 08:00).
        f = FASCE[fascia]
        while True:
            cartella, pubblicato = post_della_fascia(dbx, data, fascia)
            if pubblicato:
                break
            if adesso().time() >= f["attendi_carosello"]:
                # una sola volta per fascia (le partenze di riserva ricontrollano senza ripetere l'avviso)
                if not leggi_json_o_none(dbx, f"{r['percorso']}/reel-saltato.json"):
                    dbx.scrivi_json(f"{r['percorso']}/reel-saltato.json", {"data_ora": ora_iso()})
                    avvisi.invia(f"⏭ Reel NON pubblicato: il carosello della {fascia} non risulta uscito.")
                return 0
            print(f"{adesso():%H:%M} il carosello non è ancora uscito, ricontrollo tra 1 minuto…")
            time.sleep(60)
        if f"{main.CARTELLA_POST}/{cartella}" != r["percorso"]:
            print("È uscito un altro carosello: preparo il Reel di quello.")
            r = prepara(dbx, f"{main.CARTELLA_POST}/{cartella}")
            percorso = r["percorso"]
            manda_anteprima(dbx, r, fascia)
        obiettivo = adesso().replace(hour=f["reel"].hour, minute=f["reel"].minute, second=0, microsecond=0)
        aspetta_fino(max(obiettivo, pubblicato + DOPO_CAROSELLO))
        if reel_gia_pubblicato(dbx, data, fascia):
            print("Nel frattempo il Reel è già uscito (altra partenza). Non faccio niente.")
            return 0

    if modalita != "prova":
        trovato = cerca_reel_recente(ig, user_id, r["didascalia"])
        if trovato:
            dbx.scrivi_json(f"{percorso}/reel-pubblicato.json", {"esito": "pubblicato", "data_ora": ora_iso(),
                                                                 "id": trovato["id"], "link": trovato.get("permalink", "")})
            print("Il Reel risultava già pubblicato.")
            return 0
        dbx.scrivi_json(f"{percorso}/reel-pubblicato.json", {"esito": "in_corso", "data_ora": ora_iso()})
    video_url = link_temporaneo(dbx, f"{percorso}/reel.mp4")
    cover_url = link_temporaneo(dbx, f"{percorso}/reel-copertina.jpg")
    container = crea_container_reel(ig, user_id, video_url, cover_url, r["didascalia"])
    attendi_video(ig, container)
    print("Container del Reel pronto (FINISHED).")

    post, montato = r["post"], r["montato"]
    if modalita == "prova":
        dbx.scrivi_json(f"{percorso}/reel-prova.json", {
            "esito": "prova_ok", "data_ora": ora_iso(), "container": container, "scene": len(r["scene"]),
            "origine": r["origine"], "durata": montato.get("durata"), "caratteri": len(r["didascalia"]),
        })
        manda_collage(post, "prova a vuoto")
        f = r["video_locale"] or scarica_video(dbx, percorso)
        invia_video_telegram(f, f"🧪 Prova a vuoto del Reel OK: {post['luogo']} ({data})\n"
                                f"Montaggio {montaggio()} · {montato.get('durata')} s · {len(r['scene'])} scene ({r['origine']}) · musica originale.\n"
                                "Instagram ha accettato il video. Niente è stato pubblicato.")
        return 0

    try:
        media_id = ig.pubblica(user_id, container)
    except ErroreInstagram:
        time.sleep(30)
        trovato = cerca_reel_recente(ig, user_id, r["didascalia"])
        if not trovato:
            raise
        media_id = trovato["id"]
    try:
        link = ig.link(media_id)
    except ErroreInstagram:
        link = ""
    dbx.scrivi_json(f"{percorso}/reel-pubblicato.json", {"esito": "pubblicato", "data_ora": ora_iso(), "id": media_id, "link": link})
    avvisi.invia(f"🎬 Reel pubblicato: {post['luogo']} ({data}, {fascia or 'manuale'})\n{link or media_id}")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
