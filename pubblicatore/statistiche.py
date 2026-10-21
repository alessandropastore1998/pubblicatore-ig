"""Statistiche dei post (risultati.json) e dati aggregati del pubblico (demografia), solo API ufficiali.

- Ogni giorno: per ogni post pubblicato da almeno 48 ore (e poi da 7 giorni) legge gli Insights del post e li
  scrive in /post/<cartella>/risultati.json (cartella dell'app Dropbox).
- La domenica (o con --demografia): dati aggregati dell'account in /pubblico/AAAA-MM-GG-demografia.json.
Serve il permesso instagram_business_manage_insights; senza, i file vengono scritti con "stato": "non_disponibile".
Solo dati aggregati: nessun dato su singoli utenti.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

from . import avvisi
from .dropbox_client import Dropbox, DropboxNonTrovato
from .instagram import ErroreInstagram, Instagram
from .main import ROMA, adesso, ora_iso

METRICHE_POST = ["reach", "views", "likes", "comments", "saved", "shares", "total_interactions", "profile_visits", "follows"]
METRICHE_STORIA = ["reach", "views", "shares", "total_interactions", "profile_visits", "follows"]
TAPPE = {"48h": timedelta(hours=48), "7g": timedelta(days=7)}
SUDDIVISIONI = ["age", "gender", "country", "city"]
PERMESSO_MANCANTE = ("permission", "permesso", "(#10)", "codice 10/", "codice 200/", "insufficient", "manage_insights")


def permesso_mancante(errore):
    testo = str(errore).lower()
    return any(p in testo for p in PERMESSO_MANCANTE)


def metriche_media(ig, media_id, nomi):
    """Chiede le metriche una alla volta: quelle non disponibili per quel tipo di media vengono saltate."""
    valori, mancanti = {}, {}
    for nome in nomi:
        try:
            dati = ig.chiama("GET", f"{media_id}/insights", metric=nome)
            voce = (dati.get("data") or [{}])[0]
            valore = (voce.get("values") or [{}])[0].get("value")
            if valore is None:
                valore = (voce.get("total_value") or {}).get("value")
            valori[nome] = valore
        except ErroreInstagram as e:
            if permesso_mancante(e):
                raise
            mancanti[nome] = str(e)[:150]
    return valori, mancanti


def risultati_post(dbx, ig):
    """Aggiorna risultati.json dei post pubblicati. Restituisce l'elenco delle cartelle aggiornate."""
    aggiornati = []
    for voce in dbx.elenca("/post"):
        if voce.get(".tag") != "folder":
            continue
        cartella = f"/post/{voce['name']}"
        try:
            pubblicato = dbx.leggi_json(f"{cartella}/pubblicato.json")
        except (DropboxNonTrovato, ValueError):
            continue
        if pubblicato.get("esito") != "pubblicato" or not pubblicato.get("id"):
            continue
        uscito = datetime.fromisoformat(pubblicato["data_ora"]).astimezone(ROMA)
        try:
            risultati = dbx.leggi_json(f"{cartella}/risultati.json")
        except (DropboxNonTrovato, ValueError):
            risultati = {"media_id": pubblicato["id"], "link": pubblicato.get("link", ""), "pubblicato": pubblicato["data_ora"]}
        cambiato = False
        for tappa, eta in TAPPE.items():
            if tappa in risultati and risultati[tappa].get("stato") != "non_disponibile":
                continue
            if adesso() - uscito < eta:
                continue
            try:
                valori, mancanti = metriche_media(ig, pubblicato["id"], METRICHE_POST)
                risultati[tappa] = {"letto_il": ora_iso(), "metriche": valori, "non_disponibili": mancanti}
                storia = (pubblicato.get("storia") or {}).get("id")
                if storia and tappa == "48h":
                    # Le Storie scadono dopo 24 ore: i dati restano leggibili solo per poco, ci proviamo.
                    try:
                        v, _ = metriche_media(ig, storia, METRICHE_STORIA)
                        risultati[tappa]["storia"] = v
                    except ErroreInstagram:
                        pass
            except ErroreInstagram as e:
                risultati[tappa] = {"letto_il": ora_iso(), "stato": "non_disponibile",
                                    "motivo": "permesso instagram_business_manage_insights mancante" if permesso_mancante(e) else str(e)[:200]}
            cambiato = True
        if cambiato:
            dbx.scrivi_json(f"{cartella}/risultati.json", risultati)
            aggiornati.append(voce["name"])
    return aggiornati


def demografia(dbx, ig, user_id):
    """Dati aggregati dell'account (follower e pubblico che interagisce), mai dati di singoli utenti."""
    oggi = adesso().strftime("%Y-%m-%d")
    dati = {"data": oggi, "letto_il": ora_iso(), "periodo": "this_month",
            "nota": "Meta non offre più 'ultimi 30 giorni' (dalla v20): si usa il mese in corso. Solo dati aggregati."}
    try:
        conto = ig.chiama("GET", user_id, fields="followers_count,media_count")
        dati["follower"] = conto.get("followers_count")
        dati["post_totali"] = conto.get("media_count")
    except ErroreInstagram as e:
        dati["follower_errore"] = str(e)[:200]
    stato_generale = "ok"
    for metrica in ("follower_demographics", "engaged_audience_demographics"):
        dati[metrica] = {}
        for suddivisione in SUDDIVISIONI:
            try:
                r = ig.chiama("GET", f"{user_id}/insights", metric=metrica, period="lifetime", timeframe="this_month",
                              metric_type="total_value", breakdown=suddivisione)
                voce = (r.get("data") or [{}])[0]
                righe = (((voce.get("total_value") or {}).get("breakdowns") or [{}])[0].get("results")) or []
                if not righe:
                    dati[metrica][suddivisione] = {"stato": "non_disponibile",
                                                   "motivo": "Meta non fornisce questi dati sotto i 100 follower / 100 interazioni"}
                else:
                    dati[metrica][suddivisione] = {"|".join(x.get("dimension_values", [])): x.get("value") for x in righe}
            except ErroreInstagram as e:
                motivo = "permesso instagram_business_manage_insights mancante" if permesso_mancante(e) else str(e)[:200]
                dati[metrica][suddivisione] = {"stato": "non_disponibile", "motivo": motivo}
                stato_generale = "non_disponibile"
    try:
        r = ig.chiama("GET", f"{user_id}/insights", metric="online_followers", period="lifetime")
        dati["online_followers"] = ((r.get("data") or [{}])[0].get("values") or [{}])[0].get("value")
    except ErroreInstagram as e:
        dati["online_followers"] = {"stato": "non_disponibile", "motivo": str(e)[:200]}
    tutte = [v for m in ("follower_demographics", "engaged_audience_demographics") for v in dati[m].values()]
    if all(isinstance(v, dict) and v.get("stato") == "non_disponibile" for v in tutte):
        stato_generale = "non_disponibile"
        dati["motivo"] = tutte[0].get("motivo", "") if tutte else ""
    dati["stato"] = stato_generale
    dbx.scrivi_json(f"/pubblico/{oggi}-demografia.json", dati)
    return dati


def prova_media(ig, user_id):
    """Solo diagnostica: legge le metriche dell'ultimo post pubblicato, senza scrivere niente."""
    ultimo = ig.ultimi_post(user_id, quanti=1)
    if not ultimo:
        return "nessun post"
    valori, mancanti = metriche_media(ig, ultimo[0]["id"], METRICHE_POST)
    return f"ultimo post {ultimo[0].get('permalink')}: {valori} · non disponibili: {list(mancanti)}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--demografia", action="store_true", help="legge anche i dati del pubblico (di solito solo la domenica)")
    opz = p.parse_args()
    dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
    ig = Instagram(os.environ["IG_TOKEN"])
    user_id = ig.chi_sono()["user_id"]
    try:
        try:
            prova_media(ig, user_id)
            print("Diagnostica Insights: disponibili ✅")
        except ErroreInstagram as e:
            print("Diagnostica Insights: NON disponibili →",
                  "permesso instagram_business_manage_insights mancante" if permesso_mancante(e) else "errore")
        aggiornati = risultati_post(dbx, ig)
        print(f"risultati.json aggiornati: {len(aggiornati) if aggiornati else 0}")
        if opz.demografia or adesso().weekday() == 6:
            d = demografia(dbx, ig, user_id)
            print(f"Demografia: stato {d['stato']}")
    except Exception as e:  # noqa: BLE001
        avvisi.invia(f"📊❌ Statistiche non lette: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
