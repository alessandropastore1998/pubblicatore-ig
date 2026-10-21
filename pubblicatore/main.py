"""Pubblica i post del profilo (due fasce: mattina 07:30 e sera 19:30, ora di Roma).

Uso (di solito lo lancia GitHub Actions):
  python -m pubblicatore.main                 → automatico: capisce la fascia dall'ora, aspetta l'orario e pubblica
  python -m pubblicatore.main --prova         → prova a vuoto: controlla tutto e crea i container, NON pubblica
  python -m pubblicatore.main --subito        → pubblica adesso, senza aspettare l'orario
  python -m pubblicatore.main --data 2026-10-15 --fascia sera   → usa il post di quella data e fascia
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from datetime import time as ora
from zoneinfo import ZoneInfo

from . import avvisi, immagini
from .dropbox_client import Dropbox, DropboxNonTrovato
from .instagram import ErroreInstagram, Instagram

ROMA = ZoneInfo("Europe/Rome")
# Per ogni fascia: da quando una partenza di GitHub è valida (prima si ferma: è la partenza dell'altro fuso
# legale/solare), l'ora di pubblicazione, fino a quando aspettare un post in ritardo, e l'ultima ora utile.
FASCE = {
    "mattina": {"inizio": ora(6, 30), "pubblica": ora(7, 30), "attendi_post": ora(8, 0), "fine": ora(10, 0)},
    "sera": {"inizio": ora(18, 30), "pubblica": ora(19, 30), "attendi_post": ora(20, 0), "fine": ora(22, 0)},
}
FASCIA_PREDEFINITA = "sera"  # i post.json senza campo "fascia" (formato v3) sono della sera
CARTELLA_POST = "/post"

MAX_DIDASCALIA = 2200
# Instagram accetta al massimo 5 hashtag per post (limite annunciato il 18/12/2025; la pagina dell'API
# a ottobre 2026 dice ancora 30). Se sono di più li riduciamo noi invece di rischiare un rifiuto o un taglio.
MAX_HASHTAG = 5
HASHTAG_DA_TENERE = ("#" + os.environ.get("PROFILO", "").lstrip("@").lower()) if os.environ.get("PROFILO") else "#"
MAX_MENZIONI = 20
RE_HASHTAG = re.compile(r"(?<![\w&])#\w+")
MIN_FOTO, MAX_FOTO = 2, 10


class Stop(Exception):
    """Interruzione voluta (post da approvare, controlli non superati…): avvisa ma non è un bug."""


def adesso():
    return datetime.now(ROMA)


def si(valore, predefinito=False):
    if valore is None or valore == "":
        return predefinito
    return valore.strip().lower() in ("si", "sì", "true", "1", "yes", "on")


def ora_iso():
    return adesso().isoformat(timespec="seconds")


# ───────────────────────────── controlli sul testo ─────────────────────────────

def riduci_hashtag(testo):
    """Se ci sono più di MAX_HASHTAG hashtag tiene l'hashtag del profilo (variabile PROFILO) + i primi diversi fino a 5 e toglie gli altri
    (anche i doppioni). Restituisce (testo, quanti_erano). Il resto del testo non cambia."""
    trovati = RE_HASHTAG.findall(testo)
    if len(trovati) <= MAX_HASHTAG:
        return testo, len(trovati)
    tenuti = []
    if any(h.lower() == HASHTAG_DA_TENERE for h in trovati):
        tenuti.append(HASHTAG_DA_TENERE)
    for h in trovati:
        if len(tenuti) >= MAX_HASHTAG:
            break
        if h.lower() not in tenuti:
            tenuti.append(h.lower())
    gia_messi = set()

    def sostituisci(m):
        h = m.group(0).lower()
        if h in tenuti and h not in gia_messi:
            gia_messi.add(h)
            return m.group(0)
        return ""

    nuovo = RE_HASHTAG.sub(sostituisci, testo)
    righe = [re.sub(r"[ \t]{2,}", " ", r).rstrip() for r in nuovo.split("\n")]
    nuovo = re.sub(r"\n{3,}", "\n\n", "\n".join(righe)).strip()
    return nuovo, len(trovati)


def controlla_didascalia(testo):
    problemi = []
    if not testo.strip():
        problemi.append("la descrizione è vuota")
    if len(testo) > MAX_DIDASCALIA:
        problemi.append(f"descrizione troppo lunga: {len(testo)} caratteri (max {MAX_DIDASCALIA})")
    hashtag = RE_HASHTAG.findall(testo)
    if len(hashtag) > MAX_HASHTAG:
        problemi.append(f"troppi hashtag: {len(hashtag)} (max {MAX_HASHTAG})")
    menzioni = re.findall(r"(?<![\w.])@[\w.]+", testo)
    if len(menzioni) > MAX_MENZIONI:
        problemi.append(f"troppe menzioni @: {len(menzioni)} (max {MAX_MENZIONI})")
    return problemi


def valida_post(post, data):
    errori = []
    for campo in ("data", "luogo", "stato", "descrizione", "foto"):
        if campo not in post:
            errori.append(f"manca il campo '{campo}'")
    if errori:
        return errori
    if post["data"] != data:
        errori.append(f"post.json ha data {post['data']} invece di {data}")
    if post["stato"] not in ("pronto", "da_approvare"):
        errori.append(f"stato sconosciuto: {post['stato']!r}")
    if not isinstance(post["foto"], list) or not all(isinstance(f, dict) and f.get("url") for f in post["foto"]):
        errori.append("'foto' deve essere un elenco di foto, ognuna con 'url'")
    elif not MIN_FOTO <= len(post["foto"]) <= MAX_FOTO:
        errori.append(f"numero di foto non valido: {len(post['foto'])} (da {MIN_FOTO} a {MAX_FOTO})")
    return errori


def scegli_foto(post):
    """Controlla ogni foto; se una non va la sostituisce con la prima riserva valida.
    Restituisce (lista di (nome, url), note). Solleva Stop se manca ancora qualcosa."""
    riserve = list(post.get("riserve") or [])
    scelte, note = [], []
    for foto in post["foto"]:
        nome = foto.get("file") or foto.get("ruolo") or "foto"
        url, problema = immagini.controlla(foto["url"])
        if not problema:
            scelte.append((nome, url))
            print(f"  ✓ {nome}")
            continue
        print(f"  ✗ {nome}: {problema}")
        sostituita = False
        while riserve:
            riserva = riserve.pop(0)
            nome_r = riserva.get("file") or "riserva"
            url_r, problema_r = immagini.controlla(riserva.get("url", ""))
            if not problema_r:
                scelte.append((nome_r, url_r))
                note.append(f"{nome} sostituita con {nome_r} ({problema})")
                print(f"    → uso {nome_r}")
                sostituita = True
                break
            print(f"    ✗ anche {nome_r}: {problema_r}")
        if not sostituita:
            raise Stop(f"la foto {nome} non va ({problema}) e non ci sono riserve valide")
    return scelte, note


# ───────────────────────────── Dropbox ─────────────────────────────

def leggi_intestazione(dbx, nome):
    """(fascia, riserva) dal post.json: fascia "mattina"/"sera", None se post.json manca, "?" se è illeggibile
    (va comunque scelto, così l'errore di formato arriva su Telegram). riserva=True per i post di scorta,
    che si pubblicano solo se la redazione non ha preparato un post per quella fascia."""
    try:
        post = dbx.leggi_json(f"{CARTELLA_POST}/{nome}/post.json")
    except DropboxNonTrovato:
        return None, False
    except ValueError:
        return "?", False
    return str(post.get("fascia") or FASCIA_PREDEFINITA).strip().lower(), bool(post.get("riserva"))


def leggi_priorita(dbx, nome):
    try:
        return bool(dbx.leggi_json(f"{CARTELLA_POST}/{nome}/post.json").get("priorita"))
    except (DropboxNonTrovato, ValueError):
        return False


def quando_pubblicato(dbx, nome):
    """Data e ora (Roma) della pubblicazione se il post risulta pubblicato, altrimenti None."""
    try:
        dati = dbx.leggi_json(f"{CARTELLA_POST}/{nome}/pubblicato.json")
    except (DropboxNonTrovato, ValueError):
        return None
    if dati.get("esito") != "pubblicato":
        return None
    try:
        return datetime.fromisoformat(dati.get("data_ora", "")).astimezone(ROMA)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=ROMA)


def gia_pubblicato(dbx, nome):
    return quando_pubblicato(dbx, nome) is not None


CARTELLA_RISERVE = "/riserve"


def prendi_riserva(dbx, data, fascia):
    """Scorta senza data: /riserve/<nome>/post.json. La prima non ancora usata viene copiata in /post/<data>-<nome>/
    (con data e fascia di oggi) e segnata come usata. Restituisce il nome della nuova cartella in /post, o None."""
    try:
        voci = dbx.elenca(CARTELLA_RISERVE)
    except DropboxNonTrovato:
        return None
    for nome in sorted(v["name"] for v in voci if v.get(".tag") == "folder"):
        base = f"{CARTELLA_RISERVE}/{nome}"
        try:
            dbx.leggi_json(f"{base}/usata.json")
            continue  # già usata
        except DropboxNonTrovato:
            pass
        try:
            post = dbx.leggi_json(f"{base}/post.json")
        except (DropboxNonTrovato, ValueError):
            continue
        cartella = f"{data}-{nome}"
        post.update({"data": data, "fascia": fascia, "riserva": True})
        dbx.scrivi_json(f"{CARTELLA_POST}/{cartella}/post.json", post)
        dbx.scrivi_json(f"{base}/usata.json", {"data": data, "fascia": fascia, "cartella": cartella, "data_ora": ora_iso()})
        print("Nessun post della redazione: uso una riserva.")
        return cartella
    return None


def trova_cartella(dbx, data, fascia, attendi_fino=None, limite_riserva=None):
    """Cerca /post/<data>-…/ con post.json della fascia richiesta (fascia None = avvio manuale: il primo post
    di quella data non ancora pubblicato). Con 'attendi_fino' riprova ogni 2 minuti fino a quell'ora.
    Un post di riserva ("riserva": true) si usa solo se la redazione non ha un post per la fascia
    entro 'limite_riserva'."""
    while True:
        try:
            voci = dbx.elenca(CARTELLA_POST)
        except DropboxNonTrovato:
            voci = []
        cartelle = sorted(v["name"] for v in voci if v.get(".tag") == "folder" and v["name"].startswith(f"{data}-"))
        intestazioni = {nome: leggi_intestazione(dbx, nome) for nome in cartelle}
        candidate = [(nome, f) for nome, (f, _) in intestazioni.items() if f is not None]
        if fascia:
            scelte = [nome for nome, f in candidate if f in (fascia, "?")]
            if scelte:
                pubblicati = {nome: quando_pubblicato(dbx, nome) for nome in scelte}
                # Fascia già coperta oggi (un post uscito dopo l'inizio della fascia): niente secondo post.
                inizio = FASCE[fascia]["inizio"]
                coperta = [n for n, q in pubblicati.items() if q and q.strftime("%Y-%m-%d") == data and q.time() >= inizio]
                if coperta:
                    return coperta[-1]
                da_fare = [nome for nome in scelte if not pubblicati[nome]]
                redazione = [nome for nome in da_fare if not intestazioni[nome][1]]
                if redazione:
                    # Un post con "priorita": true (es. preparato apposta dal titolare) passa davanti agli altri della fascia.
                    prioritari = [n for n in redazione if leggi_priorita(dbx, n)]
                    return (prioritari or redazione)[-1]
                # Solo riserve: si aspetta un post della redazione fino a 'limite_riserva', poi si usa la riserva.
                if da_fare and (not limite_riserva or adesso().time() >= limite_riserva):
                    return da_fare[-1]
                # Solo post già usciti fuori fascia (es. pubblicati a mano di notte): come se non ci fosse niente.
        elif candidate:
            da_fare = [nome for nome, _ in candidate if not gia_pubblicato(dbx, nome)]
            return (da_fare or [nome for nome, _ in candidate])[0]
        if not attendi_fino or adesso().time() >= attendi_fino:
            return None
        print(f"{adesso():%H:%M} post.json di {data} ({fascia}) non ancora presente, riprovo tra 2 minuti…")
        time.sleep(120)


# ───────────────────────────── principale ─────────────────────────────

def leggi_opzioni():
    p = argparse.ArgumentParser()
    p.add_argument("--prova", action="store_true")
    p.add_argument("--subito", action="store_true")
    p.add_argument("--data")
    p.add_argument("--fascia", choices=list(FASCE))
    a = p.parse_args()
    # Avvio manuale da GitHub (pulsante "Run workflow"): le scelte arrivano come variabili d'ambiente.
    modalita = os.environ.get("MODALITA", "")
    if modalita == "prova":
        a.prova = True
    elif modalita == "pubblica-adesso":
        a.subito = True
    if not a.data and os.environ.get("DATA_POST", "").strip():
        a.data = os.environ["DATA_POST"].strip()
    if not a.fascia and os.environ.get("FASCIA", "") in FASCE:
        a.fascia = os.environ["FASCIA"]
    return a


def attendi_orario():
    """Avvio automatico: restituisce la fascia di questa partenza (dopo aver aspettato l'orario), o None."""
    ora_roma = adesso()
    for nome, f in FASCE.items():
        if f["inizio"] <= ora_roma.time() < f["fine"]:
            obiettivo = ora_roma.replace(hour=f["pubblica"].hour, minute=f["pubblica"].minute, second=0, microsecond=0)
            if ora_roma < obiettivo:
                secondi = (obiettivo - ora_roma).total_seconds()
                print(f"Sono le {ora_roma:%H:%M:%S} a Roma: fascia {nome}, aspetto le {f['pubblica']:%H:%M} ({int(secondi)} s).")
                time.sleep(secondi)
            return nome
    print(f"Sono le {ora_roma:%H:%M} a Roma: fuori dalle fasce di pubblicazione (partenza dell'altro fuso "
          "legale/solare o troppo tardi). Mi fermo.")
    return None


def main():
    global CARTELLA_POST
    opz = leggi_opzioni()
    if os.environ.get("CARTELLA", "post") == "test":
        # I post di prova stanno in /test/ e si possono solo provare, mai pubblicare.
        if not opz.prova:
            print("La cartella test/ si usa solo con la modalità prova.")
            return 1
        CARTELLA_POST = "/test"
    if si(os.environ.get("PAUSA")) and not (opz.prova or opz.subito):
        print("Pubblicazione automatica in PAUSA (variabile PAUSA). Non faccio niente.")
        return 0
    automatico = not (opz.prova or opz.subito)
    fascia = opz.fascia
    partenza = adesso().time()
    if automatico:
        fascia = attendi_orario()
        if not fascia:
            return 0

    data = opz.data or adesso().strftime("%Y-%m-%d")
    etichetta = "🧪 PROVA" if opz.prova else "Post"
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])

    if automatico:
        # La riserva si usa solo alla fine dell'attesa (08:00 / 20:00), così la redazione in ritardo ha sempre la precedenza.
        limite = FASCE[fascia]["attendi_post"]
        cartella = trova_cartella(dbx, data, fascia, attendi_fino=limite, limite_riserva=limite)
        if not cartella:
            cartella = prendi_riserva(dbx, data, fascia)
    else:
        cartella = trova_cartella(dbx, data, fascia)
    if not cartella:
        if automatico and partenza >= FASCE[fascia]["attendi_post"]:
            # Partenza di riserva: l'avviso "manca il post" l'ha già mandato la partenza principale.
            print(f"Nessun post della {fascia} per il {data}: già segnalato dalla partenza principale.")
            return 0
        quale = f"della {fascia} " if fascia else ""
        avvisi.invia(
            f"❌ {etichetta} {quale}del {data} NON pubblicato\n"
            f"Non trovo post.json {quale}nella cartella post/{data}-…/ dell'app su Dropbox.\n"
            "La redazione non l'ha salvato (o è in ritardo). Vedi README → «Se arriva un errore»."
        )
        return 1
    percorso = f"{CARTELLA_POST}/{cartella}"
    print("Post trovato ✅")

    try:
        return pubblica(dbx, percorso, data, opz, etichetta)
    except Exception as e:  # noqa: BLE001 — qualunque errore va scritto e notificato
        motivo = str(e) or type(e).__name__
        tipo = "Controllo non superato" if isinstance(e, Stop) else "Errore"
        if not opz.prova:
            try:
                dbx.scrivi_json(f"{percorso}/pubblicato.json", {"esito": "errore", "data_ora": ora_iso(), "errore": motivo})
            except Exception:  # noqa: BLE001
                print("Non riesco a scrivere pubblicato.json")
        titolo = f"❌ 🧪 PROVA del {data} fallita" if opz.prova else f"❌ Post del {data} NON pubblicato"
        avvisi.invia(f"{titolo} ({cartella})\n{tipo}: {motivo}\nVedi README → «Se arriva un errore».")
        return 1


def pubblica(dbx, percorso, data, opz, etichetta):
    # 1) Già pubblicato? (niente doppioni, anche se il job parte due volte)
    try:
        precedente = dbx.leggi_json(f"{percorso}/pubblicato.json")
    except DropboxNonTrovato:
        precedente = {}
    if precedente.get("esito") == "pubblicato" and not opz.prova:
        print("Già pubblicato. Non faccio niente.")
        return 0

    # 2) Leggi e valida post.json
    post = dbx.leggi_json(f"{percorso}/post.json")
    errori = valida_post(post, data)
    if errori:
        raise Stop("post.json non valido: " + "; ".join(errori))

    avvisi_redazione = [str(a) for a in (post.get("avvisi") or [])]
    motivi = [str(m) for m in (post.get("motivi_da_approvare") or [])]
    if post["stato"] == "da_approvare" and si(os.environ.get("RICHIEDI_APPROVAZIONE")) and not opz.subito:
        if not opz.prova:
            dbx.scrivi_json(f"{percorso}/pubblicato.json", {"esito": "da_approvare", "data_ora": ora_iso(), "motivi": motivi})
        avvisi.invia(
            f"⏸ {post['luogo']} ({data}) NON pubblicato: la redazione l'ha segnato «da_approvare».\n"
            + "\n".join(f"• {m}" for m in motivi)
            + "\nPer pubblicarlo lo stesso: README → «Pubblicare a mano un post da_approvare»."
        )
        return 0

    # 3) Controlli su testo e foto
    originale = post["descrizione"].strip()
    didascalia, n_hashtag = riduci_hashtag(originale)
    problemi = controlla_didascalia(didascalia)
    if problemi:
        raise Stop("; ".join(problemi) + " — il post resta per la pubblicazione manuale")
    print(f"Descrizione: {len(didascalia)} caratteri, hashtag {min(n_hashtag, MAX_HASHTAG)}"
          + (f" (ridotti da {n_hashtag})" if n_hashtag > MAX_HASHTAG else "") + ". Controllo le foto:")
    foto, note_foto = scegli_foto(post)
    if n_hashtag > MAX_HASHTAG:
        note_foto.insert(0, f"hashtag ridotti da {n_hashtag} a {MAX_HASHTAG}")

    ig = Instagram(os.environ["IG_TOKEN"])
    io = ig.chi_sono()
    user_id = io["user_id"]
    print("Account Instagram collegato ✅")

    # 4) Già uscito ma esito non salvato? (es. errore di rete subito dopo media_publish in un giro precedente)
    if not opz.prova:
        trovato = cerca_post_recente(ig, user_id, didascalia)
        if trovato:
            salva_pubblicato(dbx, percorso, trovato["id"], trovato.get("permalink", ""), {"esito": "non tentata"}, note_foto)
            avvisi.invia(f"✅ {post['luogo']} risultava già pubblicato: {trovato.get('permalink', '')}")
            return 0

    usati, totale = ig.quota(user_id)
    print(f"Post via API nelle ultime 24 ore: {usati}/{totale}")
    if totale and usati is not None and usati >= totale:
        raise Stop(f"limite di Instagram raggiunto ({usati}/{totale} post via API nelle ultime 24 ore)")

    # 5) Crea i container e aspetta che siano pronti (status_code = FINISHED)
    figli = []
    for nome, url in foto:
        cid = ig.crea_foto(user_id, url, carosello=True)
        ig.attendi_pronto(cid)
        print(f"  container {nome}: pronto")
        figli.append(cid)
    carosello = ig.crea_carosello(user_id, figli, didascalia)
    ig.attendi_pronto(carosello)
    print("Carosello pronto ✅")

    vuole_storia = si(os.environ.get("PUBBLICA_STORIA"), predefinito=True)

    if opz.prova:
        storia = "non provata (disattivata)"
        if vuole_storia:
            try:
                sid = ig.crea_foto(user_id, foto[0][1], storia=True)
                ig.attendi_pronto(sid)
                storia = "container della Storia accettato ✓"
            except ErroreInstagram as e:
                storia = f"Instagram rifiuta la Storia: {e}"
        # Solo in prova: come reagisce l'API alla descrizione ORIGINALE con più di 5 hashtag?
        # Test pulito: due container a foto singola NUOVI (stessa foto), uno con la descrizione ridotta e uno
        # con l'originale, così l'unica differenza è il numero di hashtag. Nessuno dei due viene pubblicato.
        test_hashtag = None
        if n_hashtag > MAX_HASHTAG:
            esiti = {}
            for etichetta_test, testo in ((f"{MAX_HASHTAG} hashtag", didascalia), (f"{n_hashtag} hashtag", originale)):
                try:
                    cid = ig.crea_foto(user_id, foto[0][1], didascalia=testo)
                    ig.attendi_pronto(cid)
                    esiti[etichetta_test] = "accettato (FINISHED)"
                except ErroreInstagram as e:
                    esiti[etichetta_test] = f"rifiutato: {e}"
            test_hashtag = "; ".join(f"con {k}: {v}" for k, v in esiti.items())
            print(f"Test hashtag: {test_hashtag}")
        dbx.scrivi_json(f"{percorso}/prova.json", {
            "esito": "prova_ok", "data_ora": ora_iso(), "carosello": carosello, "foto": [n for n, _ in foto],
            "note_foto": note_foto, "storia": storia, "caratteri": len(didascalia),
            "hashtag_originali": n_hashtag, "hashtag_pubblicati": min(n_hashtag, MAX_HASHTAG), "test_hashtag": test_hashtag,
        })
        avvisi.invia(
            f"🧪 Prova a vuoto OK: {post['luogo']} ({data})\n"
            f"Foto controllate e container creati: {len(foto)}. Descrizione: {len(didascalia)} caratteri, "
            f"{min(n_hashtag, MAX_HASHTAG)} hashtag.\n"
            f"Storia: {storia}\n" + "".join(f"• {n}\n" for n in note_foto)
            + (f"• Test: {test_hashtag}\n" if test_hashtag else "") + "Niente è stato pubblicato."
        )
        return 0

    # 6) Pubblica
    dbx.scrivi_json(f"{percorso}/pubblicato.json", {"esito": "in_corso", "data_ora": ora_iso(), "carosello": carosello})
    try:
        media_id = ig.pubblica(user_id, carosello)
    except ErroreInstagram:
        # Può capitare che Instagram pubblichi ma la risposta si perda: controlliamo prima di dichiarare errore.
        time.sleep(30)
        trovato = cerca_post_recente(ig, user_id, didascalia)
        if not trovato:
            raise
        media_id = trovato["id"]

    # Da qui il post è uscito: niente di quello che segue deve farlo risultare "non pubblicato".
    try:
        link = ig.link(media_id)
    except ErroreInstagram:
        link = ""
    storia = {"esito": "disattivata"}
    if vuole_storia:
        try:
            sid = ig.crea_foto(user_id, foto[0][1], storia=True)
            ig.attendi_pronto(sid)
            storia = {"esito": "pubblicata", "id": ig.pubblica(user_id, sid)}
        except ErroreInstagram as e:
            storia = {"esito": "errore", "errore": str(e)}
    salva_pubblicato(dbx, percorso, media_id, link, storia, note_foto)

    righe = []
    if post.get("riserva"):
        quale = f"della {post.get('fascia')}" if post.get("fascia") else "di oggi"
        righe += [f"🛟 Usata la RISERVA: la redazione non aveva preparato il post {quale}.",
                  f"Copia la cartella {percorso.split('/')[-1]} nello storico dei post e prepara una nuova riserva.", ""]
    righe += [f"✅ Pubblicato: {post['luogo']} ({data})", link or f"(id {media_id})"]
    righe.append("Storia: pubblicata ✓" if storia["esito"] == "pubblicata" else f"Storia: {storia['esito']} {storia.get('errore', '')}".strip())
    righe += [f"• {n}" for n in note_foto]
    if motivi or avvisi_redazione:
        righe.append("⚠️ Avvisi della redazione:")
        righe += [f"• {a}" for a in (motivi + avvisi_redazione)[:8]]
    avvisi.invia("\n".join(righe))
    return 0


def cerca_post_recente(ig, user_id, didascalia):
    inizio = didascalia[:80].strip()
    limite = datetime.now(ROMA) - timedelta(hours=3)
    for m in ig.ultimi_post(user_id):
        quando = datetime.strptime(m.get("timestamp", "1970-01-01T00:00:00+0000"), "%Y-%m-%dT%H:%M:%S%z")
        if quando >= limite and (m.get("caption") or "").strip().startswith(inizio):
            return m
    return None


def salva_pubblicato(dbx, percorso, media_id, link, storia, note_foto):
    dati = {"esito": "pubblicato", "data_ora": ora_iso(), "id": media_id, "link": link, "storia": storia, "note_foto": note_foto}
    try:
        dbx.scrivi_json(f"{percorso}/pubblicato.json", dati)
    except Exception as e:  # noqa: BLE001
        avvisi.invia(f"⚠️ Post pubblicato ({link}) ma non riesco a scrivere pubblicato.json: {e}")


if __name__ == "__main__":
    sys.exit(main())
