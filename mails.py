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
import itertools
import os
import smtplib
import ssl
from email.message import EmailMessage

import config
import store

_SERIE = itertools.count()


class _Tolerant(dict):
    def __missing__(self, cle):
        return "{" + cle + "}"


def rendre(modele, vals):
    texte = (config.CLIENT / "mails" / f"{modele}.txt").read_text(encoding="utf-8")
    objet, _, corps = texte.partition("\n")
    vals = _Tolerant(vals)
    return objet.removeprefix("Objet:").strip().format_map(vals), corps.strip().format_map(vals)


def mode():
    """Mode d'envoi effectif : `MAILS_MODE` surclasse instance.json (le .bat
    MailHog met "smtp" sans toucher a instance.json, qui reste sur "console"
    pour la suite de tests)."""
    conf = config.instance().get("mails") or {}
    return (os.environ.get("MAILS_MODE") or conf.get("mode") or "console").strip().lower()


def envoyer(modele, a, cc=(), **vals):
    """-> (True, None) ou (False, raison). Un echec n'annule jamais une transition."""
    conf = config.instance()["mails"]
    destinataires = [d for d in (a if isinstance(a, list) else [a]) if d]
    if not destinataires:
        return False, "aucun destinataire configure"
    copie = [d for d in cc if d and d not in destinataires]

    mode_envoi = mode()
    try:                                         # tout est rapporte, jamais fatal :
        objet, corps = rendre(modele, vals)      # un .txt disparu ne bloque pas la transition
        msg = EmailMessage()
        msg["From"] = conf["expediteur"]
        msg["To"] = ", ".join(destinataires)
        if copie:
            msg["Cc"] = ", ".join(copie)
        msg["Subject"] = objet
        msg.set_content(corps)

        if mode_envoi == "console":
            d = config.DONNEES / "mails"
            d.mkdir(parents=True, exist_ok=True)
            # Le compteur separe deux mails de la meme seconde (un par cabinet
            # dans recap.py) : le second ecrasait le premier.
            nom = f"{store.maintenant().replace(':', '-')}-{next(_SERIE):03d}-{modele}.eml"
            (d / nom).write_bytes(bytes(msg))
            print(f"\n[MAIL {modele}] -> {msg['To']}"
                  + (f" (cc {msg['Cc']})" if copie else "")
                  + f"\nObjet: {objet}\n{corps}\n", flush=True)
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
