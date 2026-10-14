"""Écran RH des établissements : liste, modification du mail, ajout.

    python tests/test_etablissements.py

Config temporaire, aucun réseau. Connexion `rh` / `fixture`.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(tempfile.mkdtemp(prefix="contratgen-etab-"))
CONF = BASE / "config"
CONF.mkdir()
(BASE / "data").mkdir()
os.environ["CONFIG_DIR"] = str(CONF)
os.environ["DONNEES"] = str(BASE / "data")

RACINE = Path(__file__).resolve().parent.parent
(CONF / "instance.json").write_text(json.dumps({
    "config_version": 1, "client": "ACME", "secret": "a" * 64, "url": "http://localhost",
    "signature": {"mode": "manuel"},
    "mails": {"mode": "console", "hote": "", "port": 587, "utilisateur": "",
              "mot_de_passe": "", "expediteur": "rh@acme.example", "rh": ["rh@acme.example"],
              "comptable_defaut": "c@acme.example"},
    "motifs_ko": ["Autre"], "templates": {},
}), encoding="utf-8")
(CONF / "formulaire.json").write_text(
    json.dumps({"roles": {}, "champs": []}), encoding="utf-8")
(CONF / "societes.amorce.json").write_text(json.dumps([{
    "nom": "ACME", "siren": "111 111 111", "comptable_email": "c@acme.example",
    "mentions": {"RaisonSociale": "ACME SAS", "FormeCapital": "SAS",
                 "SiegeSocial": "1 rue X", "Representant": "A",
                 "VilleSignature": "Paris", "ConventionCollective": "CCN"},
    "etablissements": [{
        "nom": "Siège", "siret": "111 111 111 00011", "groupe": "RESTAURANTS",
        "manager_email": "local@acme.example",
        "mentions": {"AdresseEtablissement": "1 rue X"},
    }],
}], ensure_ascii=False), encoding="utf-8")

sys.path.insert(0, str(RACINE))
from _outils import base_mongo  # noqa: E402
base_mongo("etablissements")
import config  # noqa: E402
from app import app  # noqa: E402
from _outils import compte  # noqa: E402
compte("rh", "fixture")

app.config["PROPAGATE_EXCEPTIONS"] = True
c = app.test_client()
c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})

page = c.get("/etablissements")
assert page.status_code == 200, page.status_code
assert "Siège" in page.text and "local@acme.example" in page.text, page.text
assert 'href="/etablissements"' in page.text
print("OK  liste : établissement et mail du manager")

fiche = c.get("/etablissements/modifier", query_string={"cle": "ACME / Siège"})
assert fiche.status_code == 200 and "local@acme.example" in fiche.text
print("OK  formulaire de modification")

mentions = {
    "mention_RaisonSociale": "ACME SAS", "mention_FormeCapital": "SAS",
    "mention_SiegeSocial": "1 rue X", "mention_Representant": "A",
    "mention_VilleSignature": "Paris", "mention_ConventionCollective": "CCN",
    "mention_GreffeRCS": "", "mention_RCS": "", "mention_RegionMobilite": "",
}
r = c.post("/etablissements/modifier", data={
    "cle": "ACME / Siège", "societe": "ACME", "siren": "111 111 111",
    "comptable_email": "c@acme.example", "nom": "Siège", "siret": "111 111 111 00011",
    "groupe": "RESTAURANTS", "adresse": "1 rue X",
    "manager_email": "chef@acme.example", "contrat": "genere", **mentions,
}, follow_redirects=True)
assert r.status_code == 200 and "chef@acme.example" in r.text, r.status_code
assert config.manager("ACME / Siège") == "chef@acme.example"
print("OK  modification : le mail du manager est enregistré")

r = c.post("/etablissements/nouveau", data={
    "societe": "ACME", "nom": "Annexe", "siret": "111 111 111 00022",
    "groupe": "DARK KITCHENS", "adresse": "2 rue Y",
    "manager_email": "annexe@acme.example", "contrat": "depose",
}, follow_redirects=True)
assert r.status_code == 200 and "annexe@acme.example" in r.text, r.text
assert config.manager("ACME / Annexe") == "annexe@acme.example"
assert config.mode_contrat("ACME / Annexe") == "depose"
print("OK  ajout rattaché à une société existante")

r = c.post("/etablissements/nouveau", data={
    "societe": "__nouvelle__", "societe_nom": "ACME Sud", "siren": "222 222 222",
    "comptable_email": "sud@acme.example", "nom": "Marseille",
    "siret": "222 222 222 00011", "groupe": "RESTAURANTS", "adresse": "3 rue Z",
    "manager_email": "marseille@acme.example", "contrat": "genere",
    "mention_RaisonSociale": "ACME SUD", "mention_FormeCapital": "SARL",
    "mention_SiegeSocial": "3 rue Z", "mention_Representant": "B",
    "mention_VilleSignature": "Marseille", "mention_ConventionCollective": "CCN",
}, follow_redirects=True)
assert "marseille@acme.example" in r.text, r.text
assert config.manager("ACME Sud / Marseille") == "marseille@acme.example"
print("OK  ajout d'une société et de son établissement")

r = c.post("/etablissements/nouveau", data={
    "societe": "ACME", "nom": "", "siret": "", "adresse": "",
    "manager_email": "pas-un-mail",
})
assert r.status_code == 422 and "mail de l'établissement" in r.text, r.status_code
print("OK  saisie incomplète : la page redit ce qui manque")

modif = {
    "cle": "ACME / Siège", "siren": "111 111 111", "comptable_email": "c@acme.example",
    "nom": "Siège", "siret": "111 111 111 00011", "groupe": "RESTAURANTS",
    "adresse": "1 rue X", "manager_email": "chef@acme.example", "contrat": "genere",
    **mentions,
}
r = c.post("/etablissements/modifier", data={**modif, "societe": ""})
assert r.status_code == 422 and "nom de la société est obligatoire" in r.text, r.status_code
assert [s["nom"] for s in config.societes()] == ["ACME", "ACME Sud"]
print("OK  modification : nom de société vide refusé")

r = c.post("/etablissements/modifier", data={**modif, "societe": "ACME Sud"})
assert r.status_code == 422 and "Cette société existe déjà" in r.text, r.status_code
assert [s["nom"] for s in config.societes()] == ["ACME", "ACME Sud"]
print("OK  modification : renommage vers une société existante refusé")

r = c.post("/etablissements/nouveau", data={
    "societe": "ACME", "nom": "Kiosque", "siret": "111 111 111 00033",
    "adresse": "4 rue W", "manager_email": "", "contrat": "genere",
}, follow_redirects=True)
assert r.status_code == 200 and "Établissement enregistré" in r.text, r.status_code
assert config.manager("ACME / Kiosque") == ""
print("OK  ajout sans mail : accepté, les mails iront à la RH")

r = c.post("/etablissements/nouveau", data={
    "societe": "ACME", "nom": "Kiosque", "siret": "111 111 111 00034",
    "adresse": "5 rue W", "manager_email": "", "contrat": "genere",
})
assert r.status_code == 422 and "Cet établissement existe déjà" in r.text, r.status_code
import store  # noqa: E402
acme = store.db().societes.find_one({"nom": "ACME"})
assert [e["nom"] for e in acme["etablissements"]] == ["Siège", "Annexe", "Kiosque"], acme
assert acme["etablissements"][0]["manager_email"] == "chef@acme.example", acme
assert store.db().societes.count_documents({}) == 2
print("OK  doublon d'établissement refusé ; tout est écrit dans la collection `societes`")

# Course : une autre écriture pose « Intrus » en base après la lecture du cache.
# Le contrôle en Python ne la voit pas, l'index unique `nom` la refuse.
config.societes()
store.db().societes.insert_one({"nom": "Intrus", "ordre": 9, "etablissements": []})
try:
    config.sauver_etablissement(("ACME", "Siège"), "ACME",
                                {"nom": "Intrus", "siren": "1", "comptable_email": "c@x.fr",
                                 "mentions": {}}, acme["etablissements"][0])
    raise AssertionError("renommage vers un nom pris en base accepté")
except ValueError as e:
    assert "Cette société existe déjà" in str(e), e
assert store.db().societes.find_one({"nom": "ACME"}), "ACME renommée malgré le refus"
print("OK  course sur le nom de société : refus lisible, rien d'écrit")
