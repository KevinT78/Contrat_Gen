"""Import de salariés déjà en poste (store.importer, bouton « Récupérer les dossiers »).

    python tests/test_import.py

Un salarié dont le dossier était en ordre ailleurs (SharePoint) naît à
RemisComptable, au même chemin qu'un dossier validé, fichiers sous leur nom
d'origine, sans pièce manquante, sans partir au récap hebdo du comptable.
Rejouable, et un échec ne laisse rien. Écrit dans un dossier temporaire.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TEMP = tempfile.mkdtemp(prefix="contratgen-import-")
os.environ["DONNEES"] = TEMP

import config   # noqa: E402
import purger   # noqa: E402
import recap    # noqa: E402
import store    # noqa: E402

ETAB = "ACME Restauration / Lille Grand Place"
ENTREE = {"par": "rh", "origine": "sharepoint"}


def champs(nom, prenom):
    return {"etablissement": ETAB, "poste": "Manager", "nom_naissance": nom,
            "prenom": prenom}


def f(bucket, nom, octets=b"%PDF-1.4 test"):
    return (bucket, nom, lambda: octets)


LONG = "Attestation " + "tres " * 70 + "longue.pdf"
uid = store.importer(champs("Dupont", "Jean"), "sp:DUP", ENTREE, [
    f("pieces", "CNI recto.jpg"), f("pieces", "CNI recto.jpg", b"autre"),
    f("pieces", "page.html", b"<script>"), f("pieces", LONG), f("pieces", 'a:b?.pdf'),
    f("contrat", "Contrat_de_travail.pdf")])
item = store.lire(uid)
d = Path(item["_dir"])

# 1. au bon endroit, au bon état, journal d'import
rel = d.relative_to(config.DONNEES).parts
assert rel[0] == store.DOSSIERS and rel[-3:] == ("Lille Grand Place", "Manager", "DUPONT Jean"), rel
assert store.etat(item) == "RemisComptable"
assert item["journal"][0]["type"] == "import" and item["journal"][0]["par"] == "rh"
assert uid in {i["id"] for i in store.tout()}, "invisible pour /salaries"
print("ok  dossier créé à RemisComptable :", "/".join(rel))

# 2. fichiers sous leur nom d'origine, rien d'écrasé, noms sûrs et raccourcis
pieces, contrat = store.fichiers(item, "pieces"), store.fichiers(item, "contrat")
assert "CNI recto.jpg" in pieces and "CNI recto (2).jpg" in pieces, pieces
assert "page.html" in pieces and "a-b-.pdf" in pieces, pieces
assert contrat == ["Contrat_de_travail.pdf"], contrat
longs = [p for p in pieces if p.startswith("Attestation")]
assert len(longs) == 1 and longs[0].endswith(".pdf"), longs
assert len(str(d / store.BUCKETS["pieces"] / longs[0])) <= 240, len(str(d / longs[0]))
print("ok  fichiers gardés tels quels :", len(pieces), "pièces,", len(contrat), "contrat")

# 3. un dossier importé n'attend rien ; un dossier normal sans pièce, si
assert store.manquantes(item) == [] and not store.signe_manquant(item)
assert store.manquantes({"id": "NORMAL", "champs": {}}), "contrôle : un vrai dossier vide manque de pièces"
print("ok  aucune pièce manquante, aucun « signé manquant »")

# 4. rejouable
assert store.importer(champs("Dupont", "Jean"), "sp:DUP", ENTREE, []) is None
assert sum(1 for i in store.tout() if i.get("import_cle") == "sp:DUP") == 1
print("ok  second import de la même clé : rien")

# 5. hors du récap hebdo du comptable, alors qu'il est daté d'aujourd'hui
seuil = datetime.now(timezone.utc) - timedelta(days=7)
assert uid not in {i["id"] for i in recap.remis_depuis(seuil)}
assert recap._remis_apres({"journal": [{"de": "DpaeFaite", "vers": "RemisComptable",
                                        "le": store.maintenant()}]}, seuil), "contrôle négatif"
print("ok  absent du récap hebdo (une vraie remise du jour y serait)")


# 6. un échec en cours de route ne laisse ni dossier ni chantier
def casse():
    raise OSError("réseau coupé")


avant = len(store.tout())
try:
    store.importer(champs("Martin", "Lea"), "sp:MAR", ENTREE, [f("pieces", "ok.pdf"), ("pieces", "x.pdf", casse)])
    raise AssertionError("l'erreur devait remonter")
except OSError:
    pass
assert len(store.tout()) == avant and not any((config.DONNEES / store.IMPORT).glob("*"))
assert purger.orphelins() == [], purger.orphelins()
assert store.importer(champs("Martin", "Lea"), "sp:MAR", ENTREE, [f("pieces", "ok.pdf")])
print("ok  échec : rien de créé, relance possible")

# 7. un chantier abandonné (arrêt brutal) est vidé
(config.DONNEES / store.IMPORT / "VIEUX").mkdir(parents=True)
store.vider_imports()
assert not (config.DONNEES / store.IMPORT).exists()
print("ok  chantiers d'un import interrompu vidés")

# 8. le cycle de vie normal continue : départ -> LEAVERS
store.archiver(uid, "rh", "2026-10-01", "démission")
parti = store.lire(uid)
assert store.etat(parti) == "Parti"
assert Path(parti["_dir"]).relative_to(config.DONNEES).parts[0] == store.LEAVERS
print("ok  archivable en LEAVERS")

# 9. un fichier importé hors liste blanche se télécharge, jamais inline
import app  # noqa: E402
with app.app.test_request_context():
    cd = lambda nom, b: app._servir(b"x", nom, b).headers["Content-Disposition"]
    assert cd("page.html", "pieces").startswith("attachment"), "html servi inline"
    assert cd("CNI.jpg", "pieces").startswith("inline"), "aperçu des pièces perdu"
print("ok  .html importé servi en téléchargement, les images restent en aperçu")

# --- sharepoint.py : faux SharePoint en mémoire, aucun réseau ------------------
import time  # noqa: E402

import sharepoint as sp  # noqa: E402

D = lambda nom, i: {"name": nom, "id": i, "folder": {}}                        # noqa: E731
F = lambda nom, i: {"name": nom, "id": i, "file": {},                         # noqa: E731
                    "@microsoft.graph.downloadUrl": f"https://dl.test/{i}"}
ARBRE = {
    "ETAB": [D("Direction", "DIR"), D("Seconds Managers", "SM"), D("Équipiers polyvalents", "EQ"),
             D("Leavers", "L"), D("#Ne pas toucher", "NP"), F("planning.xlsx", "PL")],
    "DIR": [D("DIR Paul", "S1")], "SM": [D("SECOND Ana (18h)", "S2")],
    "EQ": [D("EQUI Bob ( 15H)", "S3"), D("EQUI Zoe (ALT)", "S4")], "L": [D("PARTI Max", "S5")],
    "S2": [D("CONTRAT", "C2"), D("FICHE PERSONELLE", "F2"), D("MALADIE", "M2"), F("cni.pdf", "CNI")],
    "C2": [F("Contrat_de_travail.pdf", "CT")], "F2": [D("scan", "F2b")], "F2b": [F("vitale.jpg", "VI")],
    "M2": [F("arret.pdf", "AR")], "R": [D("Zorba Grill Bastille", "ETAB"), D("#Ne pas toucher", "NP")], "S1": [], "S3": [], "S4": [], "S5": [], "NP": [D("COPIE", "S9")],
}
appels = []
deja_429 = set()


def faux_graph(url, entetes):
    appels.append((url, entetes))
    if url.startswith("https://dl.test/"):
        assert "Authorization" not in entetes, "jeton envoyé à l'adresse pré-signée"
        return 200, b"%PDF " + url.encode(), {}
    assert entetes.get("Authorization") == "Bearer J", url
    if url.endswith("/sites/acme.sharepoint.com:/sites/rh"):
        return 200, b'{"id": "SITE"}', {}
    m = re.search(r"/items/([^/]+)/children", url)
    if "/root:/DOSSIERS%20SALARIES:/children" in url:
        return 200, json.dumps({"value": [D("RESTAURANTS", "R")]}).encode(), {}
    if m.group(1) == "SM" and "SM" not in deja_429:            # une limite de débit, une fois
        deja_429.add("SM")
        return 429, b"", {"Retry-After": "0"}
    enfants = ARBRE[m.group(1)]
    if m.group(1) == "ETAB" and "page2" not in url:              # pagination @odata.nextLink
        return 200, json.dumps({"value": enfants[:3], "@odata.nextLink": url + "&page2"}).encode(), {}
    return 200, json.dumps({"value": enfants[3:] if "page2" in url else enfants}).encode(), {}


import json  # noqa: E402
import re    # noqa: E402

sp._TRANSPORT = faux_graph
sp.jeton = lambda: "J"
SP_CONF = {"site": "https://acme.sharepoint.com/sites/rh", "racine": "DOSSIERS SALARIES",
           "ignorer": ["Leavers", "#Ne pas toucher"], "mots_ignores": ["zorba", "grill"],
           "postes": {"Second managers": "Assistant manager", "Direction": ""}}
config.sharepoint = lambda: SP_CONF
enfants = sp._enfants(sp._drive("J"))

# 10. ce que la liste à cocher montre
trouves = {s["id"]: s for s in sp.salaries(enfants, "ETAB")}
assert set(trouves) == {"S1", "S2", "S3", "S4"}, set(trouves)       # ni Leavers ni #Ne pas toucher
assert trouves["S1"]["poste"] == "" and trouves["S2"]["poste"] == "Assistant manager"
assert trouves["S3"]["poste"] == "Équipier polyvalent", trouves["S3"]     # pluriel + accent du formulaire
assert (trouves["S2"]["nom"], trouves["S2"]["heures"]) == ("SECOND Ana", "18H")
assert trouves["S3"]["heures"] == "15H" and (trouves["S4"]["heures"], trouves["S4"]["suffixe"]) == ("", "ALT")
assert sp.poste_de("Inconnu") == ""
config.sharepoint = lambda: {**SP_CONF, "ignorer": []}
assert "S5" in {s["id"] for s in sp.salaries(enfants, "ETAB")}, "dossiers ignorés codés en dur"
config.sharepoint = lambda: SP_CONF
source = (Path(sp.__file__)).read_text(encoding="utf-8").lower()
assert not [m for m in ("zorba", "leavers", "dossiers salaries", "ne pas toucher") if m in source], \
    "vocabulaire d'un client dans sharepoint.py : il va dans instance.json"
assert deja_429 and any("page2" in u for u, _ in appels), "429 ou pagination jamais exercés"
print("ok  liste : Leavers ignorés, postes traduits, heures lues, 429 repris, pagination suivie")

# 11. heures reportées seulement si le formulaire les propose
vrai_champ = config.champ
config.champ = lambda cid: ({"options": ["18H", "35H"]} if cid == "temps_travail" else vrai_champ(cid))
assert sp.champs_de(ETAB, trouves["S2"])["temps_travail"] == "18H"
assert "temps_travail" not in sp.champs_de(ETAB, trouves["S3"])                # 15H hors options
config.champ = vrai_champ
print("ok  heures hors options du formulaire laissées vides")

# 12. fichiers retenus : racine, CONTRAT, FICHE PERSONNELLE (récursif), rien d'autre
pris = {(b, it["name"]) for b, it in sp.fichiers_a_prendre(enfants, "S2")}
assert pris == {("pieces", "cni.pdf"), ("contrat", "Contrat_de_travail.pdf"), ("pieces", "vitale.jpg")}, pris
print("ok  MALADIE jamais récupéré")


# 13. import en tâche de fond : cochés seulement, id forgé ignoré, rejouable
def attendre():
    for _ in range(200):
        if sp.avancement()["etat"] == "fini":
            return sp.avancement()
        time.sleep(0.05)
    raise AssertionError("import jamais fini")


SP_ETAB = {"id": "ETAB", "chemin": "RESTAURANTS/Zorba Grill Bastille"}
assert sp.lancer_import(ETAB, SP_ETAB, ["S1", "S2", "S3", "FORGE"], "rh")
av = attendre()
assert (av["total"], av["fait"], av["crees"], av["erreurs"]) == (3, 3, 3, []), av
ana = next(i for i in store.tout() if i.get("import_cle") == sp.cle("S2"))
assert store.fichiers(ana, "contrat") == ["Contrat_de_travail.pdf"]
assert store.fichiers(ana, "pieces") == ["cni.pdf", "vitale.jpg"]
assert ana["journal"][0]["sp_dossier"] == "Seconds Managers/SECOND Ana (18h)"
assert sp.liens()[ETAB] == SP_ETAB
AUTRE = "ACME Restauration / Paris Opéra"
DOSSIERS = [SP_ETAB, {"id": "OP", "chemin": "RESTAURANTS/Zorba Grill Opéra"},
            {"id": "MA", "chemin": "RESTAURANTS/Zorba Grill Marseille"}]
MARSEILLE = "ACME Sud / Marseille Prado"
assert sp.dossier_de(ETAB, DOSSIERS) == SP_ETAB, "établissement lié : son dossier"
assert sp.dossier_de(AUTRE, DOSSIERS)["id"] == "OP"                     # « Opéra » en commun
assert sp.dossier_de(MARSEILLE, DOSSIERS)["id"] == "MA"
assert sp.dossier_de(AUTRE, [SP_ETAB]) is None, "dossier lié à un autre établissement rendu"
ambigus = [{"id": "P1", "chemin": "RESTAURANTS/Paris Nord"}, {"id": "P2", "chemin": "RESTAURANTS/Paris Sud"}]
assert sp.dossier_de(AUTRE, ambigus) is None, "ambigu : la RH ne doit pas tomber sur l'un au hasard"
vrai_sp = config.sharepoint
config.sharepoint = lambda: {**vrai_sp(), "dossiers": {AUTRE: "RESTAURANTS/Paris Sud"}}
assert sp.dossier_de(AUTRE, ambigus)["id"] == "P2", "dossier écrit par l'installateur"
config.sharepoint = vrai_sp
for cle_, sp_ in [(AUTRE, SP_ETAB), (ETAB, DOSSIERS[1])]:
    try:
        sp.lancer_import(cle_, sp_, ["S1"], "rh")
        raise AssertionError(f"import croisé accepté : {cle_} <- {sp_['chemin']}")
    except ValueError:
        pass
assert sp.lancer_import(ETAB, SP_ETAB, ["S1", "S2", "S3", "S4"], "rh")
av = attendre()
assert (av["fait"], av["crees"]) == (4, 1), av                    # seul S4 est nouveau
assert sp.deja_importes() >= {sp.cle(i) for i in ("S1", "S2", "S3", "S4")}
print("ok  import : cochés seulement, id forgé ignoré, relance = nouveaux seulement")

# 14. un seul import à la fois
sp._import["etat"] = "en_cours"
assert not sp.lancer_import(ETAB, SP_ETAB, ["S1"], "rh")
sp._import["etat"] = "fini"
print("ok  second import refusé pendant le premier")

# 15. config : bloc absent, mal formé, correct
vrai_instance = config.instance
SITE = {"site": "https://acme.sharepoint.com/sites/rh", "racine": "SALARIES"}
for bloc, attendu in [(None, {}), ("oui", "sharepoint"), ({**SITE, "site": "http://x.fr"}, "sharepoint"),
                      ({"site": SITE["site"]}, "sharepoint"),                       # sans racine
                      ({**SITE, "postes": {"A": 1}}, "sharepoint"),
                      ({**SITE, "dossiers": ["x"]}, "sharepoint"),
                      ({**SITE, "ignorer": "Leavers"}, "sharepoint"),
                      ({**SITE, "mots_ignores": [1]}, "sharepoint"),
                      ({**SITE, "postes": {"Direction": ""}, "ignorer": ["Anciens"]}, {})]:
    config.instance = lambda b=bloc: {} if b is None else {"sharepoint": b}
    r = config._verifier_sharepoint()
    assert (r == attendu) if isinstance(attendu, dict) else (attendu in r), (bloc, r)
config.instance = vrai_instance
print("ok  bloc sharepoint d'instance.json vérifié")

# 16. l'écran : bouton, liste à cocher, garde-fous
import html as _html  # noqa: E402

app.app.config["SESSION_COOKIE_SECURE"] = False
anonyme, c = app.app.test_client(), app.app.test_client()
c.post("/login", data={"identifiant": "rh", "mot_de_passe": "fixture"})
vrai_compte = sp.compte
sp.compte = lambda: "rh@acme.test"
page = lambda u: _html.unescape(c.get(u).get_data(as_text=True))          # noqa: E731

assert "Récupérer les dossiers" in page("/etablissements")
assert anonyme.get(f"/etablissements/recuperer?cle={ETAB}").status_code == 302
s = page(f"/etablissements/recuperer?cle={ETAB}")          # lié : salariés d'office
assert "SECOND Ana" in s and "PARTI Max" not in s and s.count(">déjà importé<") == 4, s[-1500:]
assert "rh@acme.test" in s and "Se déconnecter" in s
assert s.count('class="faite"') == 3 and "aria-current=step" not in s, "fil d'étapes : import fini non coché"
assert "RESTAURANTS/Zorba Grill Bastille" in s and "#Ne pas toucher/" not in s, "choix du dossier"
assert "Les dossiers rangés dans Leavers, #Ne pas toucher ne sont pas listés" in s
assert c.post("/etablissements/recuperer", data={"cle": ETAB, "sp": "FORGE", "ids": "S1"}).status_code == 404
assert c.post("/etablissements/recuperer", data={"cle": AUTRE, "sp": "ETAB", "ids": "S1"}).status_code == 404,     "import croisé par l'écran"
assert "SECOND Ana" not in page(f"/etablissements/recuperer?cle={AUTRE}&sp=ETAB"), "sp de l'URL cru"
s = page(f"/etablissements/recuperer?cle={AUTRE}")
assert "Zorba Grill Bastille" not in s and "Aucun dossier SharePoint ne correspond" in s, "dossier lié proposé ailleurs"
assert "<select" not in s and "<select" not in page(f"/etablissements/recuperer?cle={ETAB}"), "la RH choisit encore"
assert c.get("/etablissements/recuperer?cle=Inconnu").status_code == 404

sp.compte = lambda: None
sp._conn.update(etat="attente", code="ABCD-1234", url="https://microsoft.com/devicelogin")
s = page(f"/etablissements/recuperer?cle={ETAB}")
assert "ABCD-1234" in s and "devicelogin" in s
sp._conn.clear()
sp._conn["etat"] = "vierge"
assert "Se connecter" in page(f"/etablissements/recuperer?cle={ETAB}")

# 17. fiche et liste d'un salarié importé (revue visuelle du 09/10)
sp.compte = lambda: "rh@acme.test"
s = page(f"/etablissements/recuperer?cle={ETAB}")
assert "Tous les salariés de ce dossier sont déjà importés" in s and "Importer les salariés cochés" not in s
uid_ana = ana["id"]
f = page(f"/dossier/{uid_ana}")
assert "Déposer le contrat signé" not in f, "dépôt du contrat signé proposé sur un importé"
assert "Refaire la copie et le lien" not in f, "envoi au cabinet proposé sur un importé"
assert "Date d'entrée" in f and "Contrat et documents" in f
c.post(f"/dossier/{uid_ana}/renvoyer")
assert not any(e.get("type") == "renvoi_comptable" for e in store.lire(uid_ana)["journal"]), "importé envoyé au cabinet"
assert store.date_remise(store.lire(uid_ana), repli=False) == ""
lst = page("/salaries")
assert "à compléter" in lst and "Importé le" in lst, "ancienneté/remise d'un importé"
c.post(f"/dossier/{uid_ana}/date-entree", data={"date_entree": "pas une date"})
assert not config.valeur(store.lire(uid_ana)["champs"], "date_debut")
c.post(f"/dossier/{uid_ana}/date-entree", data={"date_entree": "2024-03-01"})
item_ana = store.lire(uid_ana)
assert config.valeur(item_ana["champs"], "date_debut") == "2024-03-01"
assert store.etat(item_ana) == "RemisComptable" and not recap._remis_apres(item_ana, seuil)
print("ok  importé : ni dépôt de contrat ni envoi au cabinet, « à compléter », date d'entrée saisie")

# 18. revue du 10/10 : archivage, rapprochement, purge, session expirée
assert "Archiver dans LEAVERS" in f, "départ impossible pour un importé"
assert sp.dossier_de(AUTRE, [{"id": "R", "chemin": "RESTAURANTS/Restauration Nord"}]) is None, \
    "lié sur un mot du nom de la société"
vraies_societes = config.societes      # une société par restaurant
config.societes = lambda: [{"nom": "Zorba Grill Bastille", "etablissements": [{"nom": "Bastille"}]},
                           {"nom": "Zorba Grill Toulouse", "etablissements": [{"nom": "Blagnac"}]}]
resto = [{"id": "DB", "chemin": "DARK KITCHENS/Bastille"},
         {"id": "RB", "chemin": "RESTAURANTS/Zorba Grill Bastille"},
         {"id": "RT", "chemin": "RESTAURANTS/Zorba Grill Toulouse"}]
assert sp.dossier_de("Zorba Grill Toulouse / Blagnac", resto)["id"] == "RT", \
    "société à un seul établissement : son nom le désigne"
config.societes = vraies_societes
assert item_ana["sp_etab"] == SP_ETAB, "lien établissement <-> dossier hors racine"
vrai_conservation = config.conservation
config.conservation = lambda: {"jours": 0, "apres": ["RemisComptable", "Parti"]}
assert uid_ana not in {i["id"] for i, *_ in store.eligibles()}, "importé en poste purgeable"
c.post(f"/dossier/{uid_ana}/archiver", data={"date_sortie": "15/09/2026"})
assert c.post(f"/dossier/{uid_ana}/date-entree", data={"date_entree": "2024-01-01"}).status_code == 400
assert store.date_sortie(store.lire(uid_ana)) == "2026-09-15", "date de sortie effacée"
assert uid_ana in {i["id"] for i, *_ in store.eligibles()}, "importé parti jamais purgé"
store.purger(uid_ana)
assert store.lire(uid_ana).get("purge") and sp.liens()[ETAB] == SP_ETAB, "purge : lien perdu"
config.conservation = vrai_conservation
vrai_etabs = sp.etablissements_sp


def expiree():
    raise sp.NonConnecte("expirée")


sp.etablissements_sp = expiree
r = c.post("/etablissements/recuperer", data={"cle": ETAB, "sp": "ETAB", "ids": "S1"})
assert r.status_code == 302, r.status_code
sp.etablissements_sp = vrai_etabs
print("ok  importé : archivable, jamais purgé en poste, lien gardé après purge, session expirée sans 500")

config.sharepoint = lambda: {}
assert "Récupérer les dossiers" not in page("/etablissements"), "bouton sans bloc sharepoint"
assert c.get(f"/etablissements/recuperer?cle={ETAB}").status_code == 404
print("ok  écran : connexion par code, liste à cocher, déjà importés grisés, id forgé refusé")

# 19. revue du 11/10 : déjà dans l'app, gros fichiers, Leavers sous un poste, msal
import types  # noqa: E402

import configurer  # noqa: E402

config.sharepoint = vrai_sp
ARBRE["EQ"].append(D("NOUVEAU Léa", "S7"))
ARBRE["S7"] = []
case_s7 = lambda: re.search(r'<input[^>]*value="S7"[^>]*>', page(f"/etablissements/recuperer?cle={ETAB}")).group()  # noqa: E731
store.creer_soumission({"etablissement": AUTRE, "nom_naissance": "Nouveau", "prenom": "Léa"}, {})
assert "checked" in case_s7(), "homonyme d'un autre établissement signalé"
uid_lea = store.creer_soumission({"etablissement": ETAB, "nom_naissance": "Martin",
                                  "nom_usage": "nouveau", "prenom": "Lea"}, {})
assert sp.deja_dans_app(ETAB)[sp.cle_nom("NOUVEAU Léa")]["id"] == uid_lea, "nom d'usage, accent, ordre"
assert sp.cle_nom("DIR Paul") not in sp.deja_dans_app(ETAB), "un importé compté comme déjà dans l'app"
s = page(f"/etablissements/recuperer?cle={ETAB}")
assert "déjà dans l'app" in s and f"/dossier/{uid_lea}" in s and "Soumise" in s, s[-2000:]
assert "checked" not in case_s7() and "disabled" not in case_s7(), "doublon : décoché mais cochable"
assert "La date d'entrée ne figure pas dans le SharePoint" in s

GROS = {"name": "video.mp4", "id": "G", "file": {}, "size": 60 * 2**20}
arbre = {"S8": [GROS, {"name": "cni.pdf", "id": "P", "file": {}, "size": 1000}]}
ouverts, vrai_ouvrir = [], sp.ouvrir
sp.ouvrir = lambda it: ouverts.append(it["id"]) or b"%PDF x"
sp._import.update(etat="en_cours", crees=0, fait=0, erreurs=[])
sp._un_salarie(ETAB, SP_ETAB, {"id": "S8", "nom": "GROS Fichier", "dossier": "Equipiers/GROS Fichier",
                               "heures": "", "poste": ""}, arbre.get, "rh", set())
sp.ouvrir, sp._import["etat"] = vrai_ouvrir, "fini"
assert ouverts == ["P"], "fichier de 60 Mo téléchargé"
assert sp._import["erreurs"] == ["GROS Fichier : video.mp4 (60 Mo) non importé, à récupérer à la main"], sp._import
gros = next(i for i in store.tout() if i.get("import_cle") == sp.cle("S8"))
assert store.fichiers(gros, "pieces") == ["cni.pdf"]

arbre = {"E": [D("Equipiers", "P")], "P": [D("EQUI Bob", "B"), D("Leavers", "LV")], "LV": [D("PARTI Léo", "X")]}
assert [s_["id"] for s_ in sp.salaries(arbre.get, "E")] == ["B"], "Leavers sous un poste listé"

vrai_msal = sys.modules.get("msal")
sys.modules["msal"] = types.SimpleNamespace(
    SerializableTokenCache=lambda: types.SimpleNamespace(deserialize=lambda t: None),
    PublicClientApplication=lambda client, authority, token_cache: {"authority": authority})
sp._msal = None
assert sp._app()[0]["authority"].endswith("/organizations")
sys.modules["msal"], sp._msal = None, None                       # msal absent
try:
    sp._app()
    raise AssertionError("msal absent sans ErreurSharePoint")
except sp.ErreurSharePoint as e:
    assert "requirements-sharepoint.txt" in str(e), e
c.post("/sharepoint/connexion", data={"cle": ETAB})
assert "pas installé" in page(f"/etablissements/recuperer?cle={ETAB}"), "msal absent muet à la connexion"
assert any("msal" in a for a in configurer.avertissements()), configurer.avertissements()
sys.modules.pop("msal")
if vrai_msal:
    sys.modules["msal"] = vrai_msal
assert not any("msal" in a for a in configurer.avertissements()), "contrôle : msal installé"
sp._conn.clear()
sp._conn["etat"] = "vierge"
print("ok  déjà dans l'app décoché, > 50 Mo laissé à la RH, Leavers sous un poste ignoré, msal absent lisible")

# 20. cas limites de la revue du 11/10
store.creer_soumission({"etablissement": ETAB, "nom_naissance": "Second", "prenom": "Ana"}, {})
s = page(f"/etablissements/recuperer?cle={ETAB}")
assert "disabled" in re.search(r'<input[^>]*value="S2"[^>]*>', s).group(), "importé redevenu cochable"
assert s.count(">déjà dans l'app<") == 1, "un déjà importé aussi marqué « déjà dans l'app »"
assert sp.lancer_import(ETAB, SP_ETAB, ["S7"], "rh")                     # homonyme coché par la RH
av = attendre()
assert (av["crees"], av["erreurs"]) == (1, []), "homonyme bloqué côté serveur"
assert "disabled" in case_s7(), "importé : grisé"

vrai_role = config.role
config.role = lambda n: "nom_complet" if n == "nom" else vrai_role(n)
store.creer_soumission({"etablissement": ETAB, "nom_complet": "ZED Ali"}, {})
assert sp.cle_nom("Ali ZED") in sp.deja_dans_app(ETAB), "formulaire à champ unique « NOM Prénom »"
config.role = vrai_role
store.creer_soumission({"etablissement": ETAB, "prenom": "Seul"}, {})
assert frozenset() not in sp.deja_dans_app(ETAB), "dossier sans nom : rapproche tout dossier SharePoint sans nom"
assert sp.cle_nom("MARTIN Jean-Pierre") == sp.cle_nom("martin  jean pierre")
abandon = store.creer_soumission({"etablissement": ETAB, "nom_naissance": "Equi", "prenom": "Bob"}, {})
store.transition(abandon, "Abandonnee", "rh")
rejet = store.creer_soumission({"etablissement": ETAB, "nom_naissance": "Dir", "prenom": "Paul"}, {})
store.rejeter(rejet, "piece", "CNI illisible", "rh")
assert sp.cle_nom("EQUI Bob") not in sp.deja_dans_app(ETAB), "embauche abandonnée comptée « déjà dans l'app »"
assert sp.deja_dans_app(ETAB)[sp.cle_nom("DIR Paul")]["id"] == rejet, "dossier rejeté (en correction) ignoré"

LIMITE = {"name": "pile.pdf", "id": "L50", "file": {}, "size": sp.TAILLE_MAX}
arbre = {"S9": [LIMITE, {"name": "sans_taille.pdf", "id": "NS", "file": {}}], "S10": [dict(GROS, id="G2")]}
ouverts = []
sp.ouvrir = lambda it: ouverts.append(it["id"]) or b"%PDF x"
sp._import.update(etat="en_cours", erreurs=[])
for sid in ("S9", "S10"):
    sp._un_salarie(ETAB, SP_ETAB, {"id": sid, "nom": f"BORNE {sid}", "dossier": "x", "heures": "", "poste": ""},
                   arbre.get, "rh", set())
sp.ouvrir, sp._import["etat"] = vrai_ouvrir, "fini"
assert ouverts == ["L50", "NS"], "50 Mo pile ou taille absente : non importés"
s10 = next(i for i in store.tout() if i.get("import_cle") == sp.cle("S10"))
assert store.fichiers(s10, "pieces") == [] and len(sp._import["erreurs"]) == 1, sp._import
assert "BORNE S10 : video.mp4 (60 Mo) non importé" in page(f"/etablissements/recuperer?cle={ETAB}"), \
    "fichier écarté invisible pour la RH"

jeton_fichier = config.DONNEES / "sharepoint-jeton.json"
jeton_fichier.write_text("{}", encoding="utf-8")                  # connectée avant le retrait de msal
sys.modules["msal"], sp._msal, sp.compte = None, None, vrai_compte
r = c.get(f"/etablissements/recuperer?cle={ETAB}")
assert r.status_code == 200 and "pas installé" in _html.unescape(r.get_data(as_text=True)), r.status_code
sys.modules.pop("msal")
if vrai_msal:
    sys.modules["msal"] = vrai_msal
jeton_fichier.unlink()
sp._msal, sp.compte = None, lambda: "rh@acme.test"
anciens = page("/salaries?anciens=1")                  # DUPONT : importé, archivé sans date d'entrée
assert f"/dossier/{uid}" in anciens and "à compléter" not in anciens, \
    "ancien « à compléter » alors que la date ne se saisit plus après l'archivage"
print("ok  homonyme importable, nom unique, bornes 50 Mo, dossier tout trop gros, msal retiré sans 500")

# 21. disque presque plein (stockage sur un VPS) : l'import s'arrête net, avant d'écrire, et reprend ensuite
import shutil  # noqa: E402

ARBRE["EQ"] += [D("DISQUE Un", "SD1"), D("DISQUE Deux", "SD2")]
ARBRE["SD1"] = ARBRE["SD2"] = []
vrai_disk = shutil.disk_usage
shutil.disk_usage = lambda p: types.SimpleNamespace(total=0, used=0, free=sp.MARGE_DISQUE // 2)
assert sp.lancer_import(ETAB, SP_ETAB, ["SD1", "SD2"], "rh")
av = attendre()
shutil.disk_usage = vrai_disk
assert (av["crees"], av["fait"]) == (0, 0) and len(av["erreurs"]) == 1, av
assert av["erreurs"][0].startswith("Espace disque insuffisant"), av["erreurs"]
assert sp.lancer_import(ETAB, SP_ETAB, ["SD1", "SD2"], "rh")
assert attendre()["crees"] == 2, "relance après libération de place"
print("ok  disque presque plein : import arrêté avant d'écrire, relance possible")

print("\nOK test_import")
