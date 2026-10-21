"""Agente GUIDA: legge i DM (e i commenti) e
- a chi chiede consigli di viaggio propone UN'idea di viaggio presa dai nostri post, poi si ferma e rimanda al titolare;
- per appuntamenti e messaggi commerciali di qualsiasi tipo (collaborazioni, sponsor, hotel/ristoranti, preventivi,
  chiamate…) NON tratta: risponde solo "ti ricontatta il titolare" e manda un'email all'indirizzo dedicato.

Per ora le risposte sono BOZZE su Telegram (il titolare le incolla), come il ponte DM. Con la variabile GUIDA_INVIO = automatico
le risposte turistiche partono da sole (API ufficiale, entro 24 ore dal messaggio); quelle commerciali restano sempre al titolare.
Il nome del titolare nei messaggi viene dal secret REFERENTE.
Dati personali: solo nome utente e testo, salvati in /guida/<id>.json per non rispondere due volte. Log muti.
Email: secret EMAIL_MITTENTE, EMAIL_PASSWORD_APP (password per le app di Gmail), EMAIL_DESTINATARIO.
"""
import json
import os
import re
import smtplib
import sys
import unicodedata
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import main
from .commenti import post_recenti, telegram
from .dropbox_client import Dropbox, DropboxNonTrovato
from .instagram import ErroreInstagram, Instagram

CARTELLA = "/guida"
GIORNI = 7

# Parole che indicano un appuntamento o un messaggio commerciale (italiano e inglese). Nel dubbio si va al titolare.
RE_COMMERCIALE = re.compile(
    r"collabora|partnership|partner\b|sponsor|pubblicit|promozion|promo\b|\badv\b|campagna|influencer|\bugc\b|"
    r"media ?kit|preventiv|tariff|listino|quanto (costa|chiedete|prendete)|prezz[oi] (per|di) (un |una )?(post|reel|storia)|"
    r"appuntament|incontr(o|arci|arvi)|chiamat|\bcall\b|telefon|meeting|riunione|consulenz|"
    r"prenot|tavolo per|riserv(are|azione)|\bbook(ing)?\b|reserv(e|ation)|table for|"
    r"(il|la|nostr[oa]|mi[oa]) (hotel|albergo|ristorante|struttura|agriturismo|b&b|locale|agenzia|azienda|cantina|tour)|"
    r"ospitar(vi|ti)|invitar(vi|ti)|soggiorno (gratuito|omaggio)|omaggio|gift|ambassador|brand|business|"
    r"collab\b|paid|rate card|your rates|pricing|appointment|book a call|work (together|with you)|"
    r"(our|my) (hotel|restaurant|property|agency|company|winery|tour)|host you|invite you|feature (us|our)",
    re.I,
)
# Domande di viaggio
RE_VIAGGIO = re.compile(
    r"consigl|itinerar|cosa (vedere|fare|visitare)|dove (andare|mangiare|dormire)|quando (andare|conviene)|"
    r"quanti giorni|weekend|viaggio|vacanz|organizz|\bgiorni\b|"
    r"recommend|itinerary|what to (see|do)|where to (go|eat|stay)|when to go|how many days|trip|visit|travel|tips?\b",
    re.I,
)
RE_INGLESE = re.compile(r"\b(the|and|thanks|please|where|how|what|when|we|our|my|you|trip|visit)\b", re.I)
RE_ITALIANO = re.compile(r"\b(ciao|grazie|dove|come|quando|vorrei|consigli|siamo|andare|viaggio|che)\b", re.I)


def _norm(testo):
    return unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode().lower()


def lingua(testo):
    return "en" if RE_INGLESE.search(testo) and not RE_ITALIANO.search(testo) else "it"


def classifica(testo):
    if RE_COMMERCIALE.search(testo):
        return "commerciale"
    if RE_VIAGGIO.search(testo) or "?" in testo:
        return "viaggio"
    return "altro"


def contatto(lng):
    email = os.environ.get("EMAIL_MITTENTE", "").strip()
    nome = os.environ.get("REFERENTE", "").strip()
    if lng == "en":
        chi = nome or "us"
        return f"write to {chi} at {email}" if email else f"{nome or 'we'} will get back to you personally"
    if email:
        return f"scrivi a {nome} a {email}" if nome else f"scrivici a {email}"
    return f"ti ricontatta {nome} personalmente" if nome else "ti ricontattiamo personalmente"


def _righe(descrizione, emoji):
    for riga in descrizione.split("— 🇬🇧 —")[0].split("\n"):
        if riga.strip().startswith(emoji):
            return riga.strip()[len(emoji):].strip()
    return ""


def _scheda(descrizione, parola):
    for riga in descrizione.split("\n"):
        pulita = riga.strip()
        if parola.lower() in pulita.lower()[:12] and ":" in pulita:
            return pulita.split(":", 1)[1].strip()
    return ""


def tutti_i_post(dbx):
    """post.json di tutti i luoghi pubblicati (per trovare il luogo di cui si parla)."""
    risultato = []
    try:
        voci = dbx.elenca("/post")
    except DropboxNonTrovato:
        return risultato
    for v in sorted(voci, key=lambda v: v["name"], reverse=True):
        if v.get(".tag") != "folder":
            continue
        try:
            if dbx.leggi_json(f"/post/{v['name']}/pubblicato.json").get("esito") != "pubblicato":
                continue
            risultato.append(dbx.leggi_json(f"/post/{v['name']}/post.json"))
        except (DropboxNonTrovato, ValueError):
            continue
    return risultato


def luogo_citato(testo, posts):
    t = _norm(testo)
    for p in posts:
        nomi = {p.get("luogo", ""), p.get("parola_chiave", "")}
        if any(n and re.search(rf"\b{re.escape(_norm(n))}\b", t) for n in nomi):
            return p
    regioni = [p for p in posts if p.get("regione") and _norm(p["regione"]).split("-")[0] in t]
    return regioni[0] if regioni else None


def idea_di_viaggio(testo, posts):
    """UNA idea di viaggio costruita dai nostri post (niente inventato), poi rimanda al titolare."""
    lng = lingua(testo)
    p = luogo_citato(testo, posts)
    if p:
        d = p["descrizione"]
        vedere, mangiare, dormire = _righe(d, "🏛"), _righe(d, "🍝"), _righe(d, "🛏")
        if lng == "en":
            inglese = d.split("— 🇬🇧 —")[-1]
            quanto, quando, dritta = _scheda(inglese, "How long"), _scheda(inglese, "When"), _scheda(inglese, "Tip")
            voci = [f"Hi! 🇮🇹 Here's a trip idea for {p['luogo']}:", quanto and f"⏳ {quanto}", quando and f"🗓 {quando}",
                    vedere and f"🏛 See: {vedere}", mangiare and f"🍝 Eat: {mangiare}", dormire and f"🛏 Stay: {dormire}",
                    dritta and f"💡 {dritta}"]
            fine = f"For a trip tailored to you, {contatto('en')} 😊"
        else:
            quanto, quando, dritta = _scheda(d, "Quanto"), _scheda(d, "Quando"), _scheda(d, "Dritta")
            voci = [f"Ciao! 🇮🇹 Ecco un'idea di viaggio per {p['luogo']}:", quanto and f"⏳ {quanto}",
                    quando and f"🗓 {quando}", vedere and f"🏛 Da vedere: {vedere}",
                    mangiare and f"🍝 Dove mangiare: {mangiare}", dormire and f"🛏 Dove dormire: {dormire}",
                    dritta and f"💡 {dritta}"]
            fine = f"Per organizzare il viaggio su misura {contatto('it')} 😊"
        return "\n".join(v for v in voci if v) + "\n\n" + fine
    # Nessun luogo riconosciuto: proponiamo i nostri ultimi luoghi e rimandiamo al titolare.
    ultimi = ", ".join(p["luogo"] for p in posts[:3]) or "le nostre ultime mete"
    if lng == "en":
        return (f"Hi! 🇮🇹 Thanks for your message. An idea to start with: {ultimi} — you'll find all the tips on our profile.\n\n"
                f"For a trip tailored to you, {contatto('en')} 😊")
    return (f"Ciao! 🇮🇹 Grazie del messaggio. Un'idea per iniziare: {ultimi}, trovi tutti i consigli sul nostro profilo.\n\n"
            f"Per organizzare il viaggio su misura {contatto('it')} 😊")


def risposta_commerciale(testo):
    if lingua(testo) == "en":
        return f"Hi, thanks for reaching out! 🙏 For bookings, collaborations and appointments {contatto('en')}."
    return f"Ciao, grazie per averci scritto! 🙏 Per prenotazioni, collaborazioni e appuntamenti {contatto('it')}."


def manda_email(oggetto, corpo):
    mittente, codice = os.environ.get("EMAIL_MITTENTE", ""), os.environ.get("EMAIL_PASSWORD_APP", "")
    destinatario = os.environ.get("EMAIL_DESTINATARIO", "") or mittente
    if not mittente or not codice:
        return False
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = mittente, destinatario, oggetto
    m.set_content(corpo)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(mittente, codice)
            s.send_message(m)
        return True
    except (smtplib.SMTPException, OSError) as e:
        print(f"Email non inviata ({type(e).__name__})")
        return False


def gia_visto(dbx, chiave):
    try:
        dbx.leggi_json(f"{CARTELLA}/{chiave}.json")
        return True
    except DropboxNonTrovato:
        return False


def risposto_di_recente(dbx, utente):
    """La Guida risponde una sola volta a settimana alla stessa persona: poi si ferma e lascia fare al titolare."""
    try:
        stato = dbx.leggi_json(f"{CARTELLA}/persone/{utente.lower()}.json")
        return datetime.fromisoformat(stato["ultima"]) > datetime.now(main.ROMA) - timedelta(days=7)
    except (DropboxNonTrovato, ValueError, KeyError):
        return False


def nelle_richieste(ig, igsid, cache={}):  # noqa: B006 — cache voluta, dura un giro
    """True se chi scrive non ci segue: per Instagram la conversazione è nelle RICHIESTE di messaggio.
    L'API legge anche quelle (doc. ufficiale: tranne le richieste ferme da più di 30 giorni) ma non ha un comando per
    "accettarle": la conversazione passa tra i messaggi Generali quando rispondiamo. None se non si sa."""
    if not igsid:
        return None
    if igsid not in cache:
        try:
            cache[igsid] = not ig.chiama("GET", str(igsid), fields="is_user_follow_business").get("is_user_follow_business", True)
        except ErroreInstagram:
            cache[igsid] = None
    return cache[igsid]


AVVISO_RICHIESTA = ("📥 È nelle RICHIESTE di messaggio (non ci segue). Nell'app: ✈️ Messaggi → Richieste → apri la chat "
                    "→ Accetta, poi incolla la risposta: così la chat passa tra i messaggi principali.")


def messaggi_nuovi(ig, user_id):
    """(id, nome utente, id mittente, testo, quando, "dm"/"dm-richiesta") dei DM ricevuti negli ultimi GIORNI,
    anche quelli nelle richieste di messaggio (da chi non ci segue)."""
    limite = datetime.now(main.ROMA) - timedelta(days=GIORNI)
    conv = ig.chiama("GET", f"{user_id}/conversations", platform="instagram", fields="id,updated_time", limit="25")
    for c in conv.get("data", []):
        try:
            if datetime.strptime(c["updated_time"], "%Y-%m-%dT%H:%M:%S%z") < limite:
                continue
        except (KeyError, ValueError):
            pass
        dati = ig.chiama("GET", f"{c['id']}/messages", fields="id,message,from,created_time", limit="10")
        for m in dati.get("data", []):
            mittente = m.get("from") or {}
            if str(mittente.get("id")) == str(user_id) or not m.get("message"):
                continue
            origine = "dm-richiesta" if nelle_richieste(ig, mittente.get("id")) else "dm"
            yield m["id"], mittente.get("username", ""), mittente.get("id", ""), m["message"], m.get("created_time", ""), origine


def commenti_commerciali(ig, dbx):
    """Commenti con richieste commerciali/appuntamenti sotto i post recenti (gli altri li gestisce il ponte DM)."""
    for media_id, link, post, tipo in post_recenti(dbx):
        try:
            dati = ig.chiama("GET", f"{media_id}/comments", fields="id,text,username,timestamp", limit="50")
        except ErroreInstagram:
            continue
        for c in dati.get("data", []):
            if RE_COMMERCIALE.search(c.get("text") or ""):
                yield c["id"], c.get("username", ""), "", c.get("text", ""), c.get("timestamp", ""), f"commento sul {tipo} di {post.get('luogo', '')} {link}"


def invia_dm(ig, user_id, destinatario, testo):
    """Risposta in DM con l'API ufficiale (solo entro 24 ore dall'ultimo messaggio della persona)."""
    ig.chiama("POST", f"{user_id}/messages", recipient=json.dumps({"id": destinatario}),
              message=json.dumps({"text": testo[:1000]}, ensure_ascii=False))


def principale():
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    ig = Instagram(os.environ["IG_TOKEN"])
    io = ig.chi_sono()
    user_id, mio_nome = io["user_id"], (io.get("username") or "").lower()
    automatico = os.environ.get("GUIDA_INVIO", "").strip().lower() == "automatico"
    posts = tutti_i_post(dbx)
    contati = {"commerciale": 0, "viaggio": 0, "altro": 0, "richieste": 0}

    try:
        dm = list(messaggi_nuovi(ig, user_id))
    except ErroreInstagram as e:
        print(f"DM non leggibili ({type(e).__name__})")
        dm = []
    for mid, utente, mittente_id, testo, quando, origine in dm + list(commenti_commerciali(ig, dbx)):
        if utente.lower() == mio_nome or gia_visto(dbx, mid):
            continue
        tipo = classifica(testo)
        contati[tipo] += 1
        in_dm = origine.startswith("dm")
        richiesta = origine == "dm-richiesta"
        contati["richieste"] += richiesta
        dove = ("in DM (richiesta di messaggio)" if richiesta else "in DM") if in_dm else origine
        if richiesta and not (tipo == "viaggio" and automatico):
            telegram(AVVISO_RICHIESTA)  # prima del resto: ogni messaggio va accettato e gestito subito
        if tipo == "commerciale":
            risposta = risposta_commerciale(testo)
            inviata = manda_email(f"📩 @{utente}: richiesta di appuntamento / commerciale",
                                  f"Da: @{utente} ({dove})\nQuando: {quando}\n\nMessaggio:\n{testo}\n\n"
                                  f"Risposta breve proposta (la Guida non tratta, decidi tu):\n{risposta}\n")
            telegram(f"📩 Richiesta COMMERCIALE / appuntamento da @{utente} ({dove}):\n«{testo[:500]}»\n\n"
                     f"{'📧 Email mandata.' if inviata else '⚠️ Email non mandata (email dedicata non ancora collegata).'}\n"
                     "La Guida non tratta: rispondi tu. Risposta breve pronta da incollare:")
            telegram(risposta)
        elif tipo == "viaggio" and in_dm:
            if risposto_di_recente(dbx, utente):
                telegram(f"🧭 @{utente} ha scritto ancora in DM: «{testo[:400]}»\nLa Guida ha già risposto questa settimana e si ferma: continua tu.")
            else:
                risposta = idea_di_viaggio(testo, posts)
                spedita = False
                if automatico and mittente_id:
                    try:
                        invia_dm(ig, user_id, mittente_id, risposta)
                        spedita = True
                    except ErroreInstagram as e:
                        print(f"DM automatico non partito ({type(e).__name__})")
                if richiesta and automatico and not spedita:
                    telegram(AVVISO_RICHIESTA)
                telegram(f"🧭 Domanda di viaggio da @{utente} {dove}: «{testo[:400]}»\n"
                         + ("✅ La Guida ha risposto così (la chat ora è tra i messaggi principali):" if spedita
                            else "Risposta della Guida pronta da incollare:"))
                telegram(risposta)
                dbx.scrivi_json(f"{CARTELLA}/persone/{utente.lower()}.json", {"ultima": main.ora_iso()})
        elif in_dm:
            telegram(f"💬 Nuovo DM da @{utente}: «{testo[:400]}»\n(né domanda di viaggio né commerciale: la Guida non risponde)")
        dbx.scrivi_json(f"{CARTELLA}/{mid}.json", {"utente": utente, "testo": testo[:500], "tipo": tipo, "visto_il": main.ora_iso()})
    print(f"Guida: DM letti {len(dm)}; nuovi: {contati}.")
    if os.environ.get("MODALITA") == "prova":
        telegram(f"🧪 Guida OK: {len(dm)} DM recenti, nuovi: {contati['viaggio']} di viaggio, "
                 f"{contati['commerciale']} commerciali, {contati['altro']} altri "
                 f"({contati['richieste']} dalle richieste di messaggio). "
                 f"Email {'collegata' if os.environ.get('EMAIL_PASSWORD_APP') else 'NON ancora collegata'}.")
    return 0


if __name__ == "__main__":
    sys.exit(principale())
