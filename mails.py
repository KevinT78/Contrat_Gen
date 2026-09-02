"""Mails : SMTP, chemin unique, sous l'identite de la boite RH du client.

Le texte de chaque mail est un template `config/mails/<nom>.txt` : premiere
ligne `Objet: ...`, le reste est le corps. Pas d'ecran d'edition.

Mode `console` (defaut de la demo) : rien ne part, chaque mail est ecrit dans
data/mails/ et affiche. Passer `"mode": "smtp"` dans instance.json pour
envoyer reellement.
"""
import smtplib
import ssl
from email.message import EmailMessage

import config
import store


class _Tolerant(dict):
    def __missing__(self, cle):
        return "{" + cle + "}"


def rendre(modele, vals):
    texte = (config.CLIENT / "mails" / f"{modele}.txt").read_text(encoding="utf-8")
    objet, _, corps = texte.partition("\n")
    vals = _Tolerant(vals)
    return objet.removeprefix("Objet:").strip().format_map(vals), corps.strip().format_map(vals)


def envoyer(modele, a, **vals):
    """-> (True, None) ou (False, raison). Un echec n'annule jamais une transition."""
    conf = config.instance()["mails"]
    destinataires = [d for d in (a if isinstance(a, list) else [a]) if d]
    if not destinataires:
        return False, "aucun destinataire configure"
    objet, corps = rendre(modele, vals)

    msg = EmailMessage()
    msg["From"] = conf["expediteur"]
    msg["To"] = ", ".join(destinataires)
    msg["Subject"] = objet
    msg.set_content(corps)

    if conf.get("mode", "console") == "console":
        d = config.DONNEES / "mails"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{store.maintenant().replace(':', '-')}-{modele}.eml").write_bytes(bytes(msg))
        print(f"\n[MAIL {modele}] -> {msg['To']}\nObjet: {objet}\n{corps}\n", flush=True)
        return True, None

    try:
        with smtplib.SMTP(conf["hote"], conf["port"], timeout=20) as s:
            s.starttls(context=ssl.create_default_context())   # TLS verifie, non negociable
            s.login(conf["utilisateur"], conf["mot_de_passe"])
            s.send_message(msg)
        return True, None
    except Exception as e:                       # noqa: BLE001 -- rapporte, jamais fatal
        return False, f"{type(e).__name__}: {e}"
