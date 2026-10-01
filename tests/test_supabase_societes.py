"""Sociétés lues depuis Supabase, societes.json en copie de secours.

    python tests/test_supabase_societes.py

Aucun réseau : `_TRANSPORT_SUPABASE` renvoie les lignes. On vérifie que
- sans bloc, le fichier est servi et rien n'est appelé ;
- une lecture réussie reconstruit la forme de societes.json et réécrit le fichier ;
- un échec ou une liste vide ne remplace pas cette copie.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(tempfile.mkdtemp(prefix="contratgen-supabase-"))
CONF = BASE / "config"
CONF.mkdir()
(BASE / "data").mkdir()
os.environ["CONFIG_DIR"] = str(CONF)
os.environ["DONNEES"] = str(BASE / "data")

LOCALE = [{"nom": "ACME", "siren": "111 111 111", "comptable_email": "c@acme.example",
           "mentions": {"RaisonSociale": "ACME SAS"},
           "etablissements": [{"nom": "Siège", "siret": "111 111 111 00011",
                               "manager_email": "local@acme.example",
                               "mentions": {"AdresseEtablissement": "1 rue X"}}]}]
INSTANCE = {
    "config_version": 1, "client": "ACME", "secret": "a" * 64, "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "mot_de_passe": "", "expediteur": "rh@acme.example", "rh": ["rh@acme.example"],
              "comptable_defaut": "c@acme.example"},
    "utilisateurs": {"rh": {"mdp_hash": "x"}},
    "motifs_ko": ["Autre"], "templates": {},
    "supabase": {"url": "https://exemple.supabase.co", "cle": "sb_secret_test"},
}
(CONF / "instance.json").write_text(json.dumps(INSTANCE), encoding="utf-8")
(CONF / "formulaire.json").write_text("{}", encoding="utf-8")
(CONF / "societes.json").write_text(
    json.dumps(LOCALE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

DISTANT = [{
    "nom": "Wing Store Bastille", "siren": "933 534 604",
    "comptable_email": "addy@example.com", "ordre": 1,
    "mentions": {"RaisonSociale": "WING STORE BASTILLE SAS"},
    "etablissements": [{
        "nom": "Bastille", "ordre": 0, "siret": "933 534 604 00014",
        "groupe": "RESTAURANTS", "manager_email": "mark@example.com",
        "contrat": "", "mentions": {"AdresseEtablissement": "61 rue du Faubourg"},
    }, {
        "nom": "Annexe", "ordre": 1, "siret": "933 534 604 00022",
        "groupe": "", "manager_email": "annexe@example.com",
        "contrat": "depose", "mentions": {"AdresseEtablissement": "2 rue Y"},
    }],
}, {
    "nom": "Vide", "siren": "", "comptable_email": "", "ordre": 0,
    "mentions": {}, "etablissements": [],
}]

APPELS = []


def transport(methode, url, headers, corps=None):
    APPELS.append((methode, url))
    assert headers["apikey"] == "sb_secret_test"
    assert "sb_secret_test" not in url
    return 200, json.dumps(DISTANT).encode()


def oublier():
    config._CACHE.pop("societes.json", None)
    config._SUPABASE_ESSAI = None
    config._SUPABASE_OK = False
    APPELS.clear()


def fichier():
    return json.loads((CONF / "societes.json").read_text(encoding="utf-8"))


# --- sans bloc : le fichier, et le transport n'est pas appelé ----------------
config._TRANSPORT_SUPABASE = transport
config.instance().pop("supabase")
oublier()
assert config.societes() == LOCALE, config.societes()
assert APPELS == []
assert fichier() == LOCALE
print("OK  sans bloc supabase : societes.json, aucun appel")

# --- lecture reussie : forme du fichier, manager, contrat depose -------------
config.instance()["supabase"] = INSTANCE["supabase"]
oublier()
lu = config.societes()
assert len(APPELS) == 1 and APPELS[0][0] == "GET"
assert "/rest/v1/societes?select=" in APPELS[0][1]
# « Vide » (ordre 0) passe devant Bastille (ordre 1). Annexe apres Bastille.
assert [s["nom"] for s in lu] == ["Vide", "Wing Store Bastille"], lu
bastille = lu[1]
assert bastille["etablissements"][0]["groupe"] == "RESTAURANTS"
assert "contrat" not in bastille["etablissements"][0]
assert bastille["etablissements"][1]["contrat"] == "depose"
assert "groupe" not in bastille["etablissements"][1]
assert config.manager("Wing Store Bastille / Bastille") == "mark@example.com"
assert config.mode_contrat("Wing Store Bastille / Annexe") == "depose"
assert fichier() == lu
print("OK  lecture supabase : forme societes.json, fichier réécrit")

# --- dans les 2 minutes, pas de second appel ---------------------------------
lu2 = config.societes()
assert lu2 == lu and len(APPELS) == 1
print("OK  cache : pas de second appel dans le délai")

# --- HTTP en échec : la copie déjà écrite reste, et on la ressert ------------
def panne(methode, url, headers, corps=None):
    APPELS.append("panne")
    return 500, b'{"message":"down"}'

config._TRANSPORT_SUPABASE = panne
config._SUPABASE_ESSAI = None
garde = fichier()
lu3 = config.societes()
assert lu3 == garde and fichier() == garde and APPELS[-1] == "panne"
print("OK  panne HTTP : copie locale conservée")

# --- liste vide : même refus d'écraser ---------------------------------------
config._TRANSPORT_SUPABASE = lambda *a: (200, b"[]")
config._SUPABASE_ESSAI = None
config._SUPABASE_OK = False
config._CACHE.pop("societes.json", None)
assert config.societes() == garde and fichier() == garde
print("OK  liste vide : copie locale conservée")

# --- reprise différée : un échec récent ne rappelle pas le réseau ------------
APPELS.clear()
config._TRANSPORT_SUPABASE = panne
assert config.societes() == garde and APPELS == []
print("OK  panne récente : pas de nouvel appel")

# --- sans supabase : l'écriture va dans le fichier --------------------------
config.instance().pop("supabase")
config._oublier_societes()
config.sauver_etablissement(None, "Wing Store Bastille", None, {
    "nom": "Comptoir", "siret": "933 534 604 00030",
    "manager_email": "comptoir@example.com", "groupe": "RESTAURANTS",
    "mentions": {"AdresseEtablissement": "3 rue Z"},
})
assert config.manager("Wing Store Bastille / Comptoir") == "comptoir@example.com"
try:
    config.sauver_etablissement(None, "Wing Store Bastille", None, {
        "nom": "Comptoir", "siret": "1", "manager_email": "x@y.z",
        "mentions": {"AdresseEtablissement": "3 rue Z"},
    })
    raise AssertionError("doublon accepté")
except ValueError as e:
    assert "existe déjà" in str(e)
print("OK  sans supabase : établissement ajouté dans le fichier, doublon refusé")

# --- avec supabase : le mail part en PATCH, puis la copie est relue ----------
config.instance()["supabase"] = INSTANCE["supabase"]
config._oublier_societes()
JOURNAL = []

def ecriture(methode, url, headers, corps=None):
    JOURNAL.append((methode, url, json.loads(corps) if corps else None))
    if methode == "GET" and "etablissements(id,nom,ordre)" in url:
        return 200, json.dumps([{
            "id": 4, "etablissements": [
                {"id": 8, "nom": "Bastille", "ordre": 0},
                {"id": 9, "nom": "Comptoir", "ordre": 1},
            ],
        }]).encode()
    if methode == "PATCH":
        return 200, b'[{"id": 9}]'
    return 200, json.dumps([{
        "nom": "Wing Store Bastille", "siren": "933 534 604",
        "comptable_email": "addy@example.com", "ordre": 0,
        "mentions": {"RaisonSociale": "WING STORE BASTILLE SAS"},
        "etablissements": [{
            "nom": "Comptoir", "ordre": 1, "siret": "933 534 604 00030",
            "groupe": "RESTAURANTS", "manager_email": "nouveau@example.com",
            "contrat": "", "mentions": {"AdresseEtablissement": "3 rue Z"},
        }],
    }]).encode()

config._TRANSPORT_SUPABASE = ecriture
config.sauver_etablissement(("Wing Store Bastille", "Comptoir"), "Wing Store Bastille", {
    "nom": "Wing Store Bastille", "siren": "933 534 604",
    "comptable_email": "addy@example.com",
    "mentions": {"RaisonSociale": "WING STORE BASTILLE SAS"},
}, {
    "nom": "Comptoir", "siret": "933 534 604 00030", "groupe": "RESTAURANTS",
    "manager_email": "nouveau@example.com",
    "mentions": {"AdresseEtablissement": "3 rue Z"},
})
patch = next(corps for methode, url, corps in JOURNAL
             if methode == "PATCH" and "/etablissements?" in url)
assert patch["manager_email"] == "nouveau@example.com", patch
assert config.manager("Wing Store Bastille / Comptoir") == "nouveau@example.com"
print("OK  supabase : le mail du manager est écrit, puis relu")
