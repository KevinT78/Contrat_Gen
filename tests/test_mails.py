"""mails.envoyer — mode console, override MAILS_MODE, et TLS conditionne a l'auth.

    python tests/test_mails.py

Aucun reseau : smtplib.SMTP est remplace par un faux qui enregistre ses appels.
  - mode console : rien ne part, un .eml est ecrit.
  - MAILS_MODE surclasse le mode du fichier.
  - SMTP sans `utilisateur` (catcher local type MailHog) : pas de STARTTLS ni login.
  - SMTP avec `utilisateur` : STARTTLS verifie + login, non negociable.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-mails-")

import config          # noqa: E402
import mails           # noqa: E402

MAILS = config.instance()["mails"]
MAILS.update(expediteur="rh@test.local", rh=["rh@test.local"])


class FauxSMTP:
    """Enregistre les appels ; se substitue a smtplib.SMTP."""
    dernier = None

    def __init__(self, hote, port, timeout=None):
        FauxSMTP.dernier = self
        self.hote, self.port = hote, port
        self.appels = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.appels.append(("starttls", context is not None))

    def login(self, u, p):
        self.appels.append(("login", u))

    def send_message(self, msg):
        self.appels.append(("send_message", msg["To"]))


mails.smtplib.SMTP = FauxSMTP


def envoyer():
    return mails.envoyer("nouvelle_soumission", "dest@test.local",
                         nom="Jean Test", id="ABC", lien="http://x/y")


def nb_eml():
    return len(list((config.DONNEES / "mails").glob("*.eml")))


# --- mode console : rien ne part, un .eml est ecrit --------------------------
os.environ.pop("MAILS_MODE", None)
MAILS["mode"] = "console"
avant = nb_eml()
ok, raison = envoyer()
assert ok and raison is None, (ok, raison)
assert nb_eml() == avant + 1, "le .eml n'a pas ete ecrit"
assert FauxSMTP.dernier is None, "SMTP appele alors qu'on est en console"
print("OK  mode console : .eml ecrit, aucun SMTP")

# --- MAILS_MODE=smtp surclasse le fichier reste sur console -----------------
os.environ["MAILS_MODE"] = "smtp"
MAILS.update(mode="console", hote="localhost", port=1025, utilisateur="", mot_de_passe="")
ok, raison = envoyer()
assert ok, raison
s = FauxSMTP.dernier
assert (s.hote, s.port) == ("localhost", 1025)
assert [a[0] for a in s.appels] == ["send_message"], s.appels
print("OK  MAILS_MODE surclasse le fichier ; sans identifiant : ni STARTTLS ni login")

# --- SMTP avec identifiant : STARTTLS verifie + login ----------------------
FauxSMTP.dernier = None
os.environ.pop("MAILS_MODE", None)
MAILS.update(mode="smtp", hote="smtp-relay.brevo.example", port=587,
             utilisateur="u@brevo", mot_de_passe="secret")
ok, raison = envoyer()
assert ok, raison
s = FauxSMTP.dernier
assert s.appels == [("starttls", True), ("login", "u@brevo"),
                    ("send_message", "dest@test.local")], s.appels
print("OK  avec identifiant : STARTTLS(context) + login imposes")

# --- destinataire vide : refus net, jamais de SMTP ------------------------
FauxSMTP.dernier = None
ok, raison = mails.envoyer("nouvelle_soumission", "", nom="X", id="Y", lien="z")
assert not ok and "destinataire" in raison, (ok, raison)
assert FauxSMTP.dernier is None
print("OK  destinataire vide : (False, raison), aucun envoi")

# --- les 5 templates : rendus + envoyes, aucun {placeholder} orphelin ------
# Les kwargs miment exactement chaque site d'appel (app.py / recap.py). Un
# template qui reclame une variable qu'aucun appelant ne passe -> {var} reste
# dans la sortie -> ici on le voit. Un .txt renomme/absent -> (False, raison).
MAILS.update(mode="smtp", hote="localhost", port=1025, utilisateur="", mot_de_passe="")
SITES = {
    "nouvelle_soumission": dict(nom="Jean Test", id="ABC", lien="http://x/d"),
    "rejet":               dict(nom="Jean Test", motif="Autre", commentaire="RIB flou", lien="http://x/c"),
    "rappel_dpae":         dict(nom="Jean Test", debut="2026-10-01", lien="http://x/d"),
    "avis_comptable":      dict(nom="Jean Test", id="ABC", lien="http://x/lot"),
    "recap_hebdo":         dict(periode="2026-08-01 -> 2026-08-08", nombre=2, liste="- A\n- B"),
}
for modele, vals in SITES.items():
    objet, corps = mails.rendre(modele, vals)
    assert objet and corps, modele
    assert "{" not in objet + corps, f"{modele} : variable non fournie -> {objet + corps!r}"
    FauxSMTP.dernier = None
    ok, raison = mails.envoyer(modele, "dest@test.local", **vals)
    assert ok, (modele, raison)
    assert FauxSMTP.dernier.appels[-1] == ("send_message", "dest@test.local"), modele
print(f"OK  les {len(SITES)} templates : rendus sans variable orpheline, envoyes")

# --- .txt absent : rapporte, ne casse pas l'appelant ---------------------
ok, raison = mails.envoyer("modele_inexistant", "dest@test.local", nom="X")
assert not ok and "FileNotFoundError" in raison, (ok, raison)
print("OK  template absent : (False, raison), aucune exception")

print("\nmails.py OK — console, override, TLS conditionnee a l'auth, 5 templates couverts")
