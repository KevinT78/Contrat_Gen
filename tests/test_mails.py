"""mails.envoyer, et la facon dont les routes rapportent un envoi rate.

    python tests/test_mails.py

Aucun reseau reel : smtplib.SMTP est remplace par un faux qui enregistre ses
appels, et le dernier bloc remplace mails.envoyer lui-meme.
  - mode console : rien ne part, un .eml est ecrit.
  - MAILS_MODE surclasse le mode du fichier.
  - SMTP sans `utilisateur` (catcher local type MailHog) : pas de STARTTLS ni login.
  - SMTP avec `utilisateur` : STARTTLS verifie + login, non negociable.
  - un envoi rate est journalise ET dit a l'ecran -- verifie sur la ROUTE
    /dossier/<uid>/rejeter, parce que tester mails.envoyer seul ne prouve pas
    que son appelant regarde ce qu'il renvoie.
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
        self.msg = msg
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

# --- les 4 templates : rendus + envoyes, aucun {placeholder} orphelin ------
# Les kwargs miment exactement chaque site d'appel (app.py / recap.py). Un
# template qui reclame une variable qu'aucun appelant ne passe -> {var} reste
# dans la sortie -> ici on le voit. Un .txt renomme/absent -> (False, raison).
MAILS.update(mode="smtp", hote="localhost", port=1025, utilisateur="", mot_de_passe="")
SITES = {
    "nouvelle_soumission": dict(nom="Jean Test", id="ABC", lien="http://x/d"),
    "rejet":               dict(nom="Jean Test", motif="Autre", commentaire="RIB flou", lien="http://x/c"),
    "rappel_dpae":         dict(nom="Jean Test", debut="1er octobre 2026", poste="Manager",
                                societe="S", etablissement="E", siret="123", lien="http://x/d"),
    "recap_hebdo":         dict(periode="2026-08-01 -> 2026-08-08", nombre=2, liste="- A\n- B"),
}
# La RH est en copie du mail hebdo au cabinet : Cc pose, jamais le meme destinataire deux fois.
ok, raison = mails.envoyer("recap_hebdo", "cab@test.local", cc=["rh@test.local", "cab@test.local"],
                           **SITES["recap_hebdo"])
assert ok, raison
_msg = FauxSMTP.dernier.msg
assert (_msg["To"], _msg["Cc"]) == ("cab@test.local", "rh@test.local"), (_msg["To"], _msg["Cc"])
print("OK  cc : la RH en copie du mail au cabinet")
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


# --- l'APPELANT rapporte l'echec : tester envoyer() seul ne le prouve pas ----
# Un mail perdu portait le lien de correction du manager, et l'ecran affirmait
# quand meme « lien de correction envoye ». Le controle porte donc sur la ROUTE,
# pas sur mails.envoyer : retirer le store.noter d'app._mail doit faire rougir
# ce bloc.
import re                                                          # noqa: E402
import store                                                       # noqa: E402
import app as module                                               # noqa: E402
from app import app                                                # noqa: E402

app.config["PROPAGATE_EXCEPTIONS"] = True
for _z in ("soumissions", "documents"):
    (config.DONNEES / _z).mkdir(parents=True, exist_ok=True)

_MSG = re.compile(r'<p class="msg[^"]*">([^<]*)</p>')


def _rejet_avec_smtp(en_panne):
    """-> (dernier message affiche, entrees mail_echoue du journal)"""
    champs = {config.role("email"): "manager@test.local",
              config.role("nom"): "Martin", config.role("prenom"): "Camille",
              config.role("etablissement"): config.etablissements()[0][0],
              config.role("poste"): list(config.instance()["templates"])[0]}
    uid = store.creer_soumission(champs, {})
    c = app.test_client()
    c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
    vrai = module.mails.envoyer
    module.mails.envoyer = ((lambda *a, **k: (False, "SMTPServerDisconnected: simule"))
                            if en_panne else (lambda *a, **k: (True, None)))
    try:
        r = c.post("/dossier/%s/rejeter" % uid,
                   data={"motif": config.instance()["motifs_ko"][0],
                         "commentaire": "piece floue"}, follow_redirects=True)
    finally:
        module.mails.envoyer = vrai
    ecran = [m.strip() for m in _MSG.findall(r.get_data(as_text=True))]
    journal = [e for e in store.lire(uid)["journal"] if e.get("type") == "mail_echoue"]
    return (ecran[-1] if ecran else ""), journal


_ecran, _journal = _rejet_avec_smtp(en_panne=False)
assert "envoy" in _ecran, _ecran
assert not _journal, _journal

_ecran, _journal = _rejet_avec_smtp(en_panne=True)
assert _journal, "echec d'envoi non journalise : la RH n'a aucune trace"
assert _journal[0]["modele"] == "rejet", _journal[0]
assert "SMTPServerDisconnected" in _journal[0]["motif"], _journal[0]
assert "PAS re" in _ecran, "l'ecran affirme un envoi qui a echoue : %r" % _ecran
print("OK  echec d'envoi : journalise ET dit a l'ecran (route /rejeter)")

print("\nmails.py OK — console, override, TLS conditionnee a l'auth, 4 templates, cc,")
print("               echec d'envoi remonte par la route /rejeter")
