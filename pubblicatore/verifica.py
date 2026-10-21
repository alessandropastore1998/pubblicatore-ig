"""Controlla i collegamenti (Instagram, Dropbox, Telegram) senza pubblicare niente."""
import os
import re
import sys

from . import avvisi
from .dropbox_client import Dropbox
from .instagram import Instagram


def main():
    righe, ok = [], True

    if os.environ.get("IG_TOKEN"):
        try:
            ig = Instagram(os.environ["IG_TOKEN"])
            io = ig.chi_sono()
            usati, totale = ig.quota(io["user_id"])
            righe.append(f"✓ Instagram: @{io.get('username')} (post via API nelle ultime 24 ore: {usati}/{totale})")
            ultimi = ig.ultimi_post(io["user_id"], quanti=1)
            if ultimi:
                didascalia = ultimi[0].get("caption") or ""
                n = len(re.findall(r"(?<![\w&])#\w+", didascalia))
                righe.append(f"  ultimo post: {n} hashtag nella descrizione pubblicata, {len(didascalia)} caratteri")
            try:  # serve all'agente Guida: la chiave legge anche i DM? (solo il numero, niente nomi né testi)
                conv = ig.chiama("GET", f"{io['user_id']}/conversations", platform="instagram", fields="id,participants",
                                 limit="25")
                righe.append(f"  DM: lettura OK ({len(conv.get('data', []))} conversazioni recenti)")
                # Richiesta 15: l'API legge anche le RICHIESTE di messaggio? Conto le chat con chi non ci segue.
                altri = {str(p.get("id")) for c in conv.get("data", []) for p in (c.get("participants") or {}).get("data", [])
                         if str(p.get("id")) != str(io["user_id"]) and p.get("username", "").lower() != (io.get("username") or "").lower()}
                non_seguono, ignoti = 0, 0
                for igsid in altri:
                    try:
                        if not ig.chiama("GET", igsid, fields="is_user_follow_business").get("is_user_follow_business", True):
                            non_seguono += 1
                    except Exception:  # noqa: BLE001
                        ignoti += 1
                righe.append(f"  DM da chi NON ci segue (richieste di messaggio) letti dall'API: {non_seguono}"
                             + (f" ({ignoti} persone non controllabili)" if ignoti else ""))
            except Exception as e:  # noqa: BLE001
                righe.append(f"  DM: lettura NON permessa con questa chiave ({str(e)[:120]})")
        except Exception as e:  # noqa: BLE001
            ok = False
            righe.append(f"✗ Instagram: {e}")
    else:
        righe.append("– Instagram: secret IG_TOKEN non ancora inserito")

    if os.environ.get("DROPBOX_REFRESH_TOKEN"):
        try:
            dbx = Dropbox(os.environ["DROPBOX_APP_KEY"], os.environ["DROPBOX_APP_SECRET"], os.environ["DROPBOX_REFRESH_TOKEN"])
            voci = dbx.elenca("")
            righe.append(f"✓ Dropbox: cartella dell'app raggiungibile ({len(voci)} elementi)")
        except Exception as e:  # noqa: BLE001
            ok = False
            righe.append(f"✗ Dropbox: {e}")
    else:
        righe.append("– Dropbox: secret non ancora inseriti")

    testo = "🔧 Verifica collegamenti\n" + "\n".join(righe)
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        righe.append("✓ Telegram: messaggio di prova inviato (controlla il telefono)")
        testo = "🔧 Verifica collegamenti\n" + "\n".join(righe)
        avvisi.invia(testo)
    else:
        print("Verifica eseguita (Telegram non configurato: i dettagli non vanno nel log)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
