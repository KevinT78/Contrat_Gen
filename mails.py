"""Mails : SMTP, chemin unique, sous l'identite de la boite RH du client.

Le texte de chaque mail est un template `config/mails/<nom>.txt` : premiere
ligne `Objet: ...`, le reste est le corps. Pas d'ecran d'edition.

Mode `console` (defaut de la demo) : rien ne part, chaque mail est ecrit dans
data/mails/ et affiche. Passer `"mode": "smtp"` dans instance.json pour
envoyer reellement ; la variable d'env `MAILS_MODE` surclasse le fichier
(le .bat MailHog s'en sert pour ne pas modifier instance.json).

Sans `utilisateur` configure, l'envoi SMTP part en clair sans login : c'est
le cas d'un catcher local (MailHog). Des qu'un identifiant est present, TLS
verifie + login sont imposes.
"""
import os
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

    # MAILS_MODE surclasse le fichier : le .bat MailHog met "smtp" sans toucher a
    # instance.json, qui reste sur "console" pour la suite de tests.
    mode = (os.environ.get("MAILS_MODE") or conf.get("mode", "console")).strip().lower()
    try:                                         # tout est rapporte, jamais fatal :
        objet, corps = rendre(modele, vals)      # un .txt disparu ne bloque pas la transition
        msg = EmailMessage()
        msg["From"] = conf["expediteur"]
        msg["To"] = ", ".join(destinataires)
        msg["Subject"] = objet
        msg.set_content(corps)

        if mode == "console":
            d = config.DONNEES / "mails"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{store.maintenant().replace(':', '-')}-{modele}.eml").write_bytes(bytes(msg))
            print(f"\n[MAIL {modele}] -> {msg['To']}\nObjet: {objet}\n{corps}\n", flush=True)
            return True, None

        with smtplib.SMTP(conf["hote"], conf["port"], timeout=20) as s:
            if conf["utilisateur"]:
                # Relais authentifie (prod) : TLS verifie obligatoire, non negociable.
                # Sans identifiant = catcher local type MailHog -> envoi en clair.
                # simplification volontaire : un relais prod sans auth (IP allowlist)
                # partirait aussi en clair, a revoir si ce cas se presente.
                s.starttls(context=ssl.create_default_context())
                s.login(conf["utilisateur"], conf["mot_de_passe"])
            s.send_message(msg)
        return True, None
    except Exception as e:                       # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
