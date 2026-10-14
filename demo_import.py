"""Démo de l'import SharePoint et des exports Excel, sans réseau ni compte Microsoft.

    python demo_import.py     # remet .scratch/demo-import/ à zéro, sert sur :5000

Même config que demo.py (config.demo/) + un bloc `sharepoint`, et un faux
SharePoint en mémoire à la place de Microsoft Graph (sharepoint._TRANSPORT).
La RH est « connectée » d'office. Identifiants : rh / demo-rh.

Parcours :
  1. /etablissements -> « Récupérer les dossiers » : 2 établissements, 5 + 2
     salariés (BENALI Sarah existe déjà dans l'app : doublon signalé ; le
     dossier « Leavers » est ignoré).
  2. « Compléter les dossiers » -> déposer les .xlsx de .scratch/demo-import/excel/
     (un export RH, un planning qui contredit l'export, une ligne inconnue,
     une valeur illisible).
Données jetables : .scratch/demo-import/, jamais data_demo/ ni data/.
"""
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import escape

RACINE = Path(__file__).resolve().parent
BAC = RACINE / ".scratch" / "demo-import"
os.environ["CONFIG_DIR"] = str(BAC / "config")
os.environ["DONNEES"] = str(BAC / "data")
os.environ.setdefault("PORT", "5000")
os.environ.setdefault("DEBUG", "1")      # sinon le cookie Secure bloque la session en http

shutil.rmtree(BAC, ignore_errors=True)
shutil.copytree(RACINE / "config.demo", BAC / "config")
inst = BAC / "config" / "instance.json"
conf = json.loads(inst.read_text(encoding="utf-8"))
conf["sharepoint"] = {"site": "https://basiliccafe.sharepoint.com/sites/rh",
                      "racine": "DOSSIERS SALARIES", "ignorer": ["Leavers", "#Archives"]}
inst.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")

import demo          # noqa: E402,F401 — efface DONNEES, lit CONFIG_DIR déjà posé
import sharepoint    # noqa: E402

# --- faux SharePoint -------------------------------------------------------------
PDF = demo.PDF
D = lambda nom, i: {"name": nom, "id": i, "folder": {}}                                    # noqa: E731
F = lambda nom, i: {"name": nom, "id": i, "file": {}, "size": len(PDF),                    # noqa: E731
                    "@microsoft.graph.downloadUrl": f"https://dl.demo/{i}"}
ARBRE = {
    "ROOT": [D("Basilic Café", "G")],
    "G": [D("Paris Bastille", "PB"), D("Lyon Confluence", "LY"), D("#Archives", "AR")],
    "PB": [D("Managers", "MG"), D("Equipiers polyvalents", "EQ"), D("Leavers", "LV"),
           F("planning.xlsx", "PLAN")],
    "MG": [D("MARTIN Camille (35h)", "S1"), D("LEROY Hugo", "S2")],
    "EQ": [D("BENALI Sarah (18h)", "S3"), D("OKAFOR Chidi (24h)", "S4"),
           D("PETIT Lola (18H)", "S5"), D("NGUYEN Thao", "S6")],
    "LV": [D("ANCIEN Paul", "S7")],
    "LY": [D("Equipiers polyvalents", "EQ2")],
    "EQ2": [D("GARCIA Inès (20h)", "S8"), D("DIALLO Moussa (15h)", "S9")],
}
for n, s in enumerate(("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")):
    ARBRE[s] = [F("cni.pdf", f"{s}cni"), D("CONTRAT", f"{s}c"), D("FICHE PERSONNELLE", f"{s}f"),
                D("MALADIE", f"{s}m")]
    ARBRE[f"{s}c"] = [F("Contrat_de_travail.pdf", f"{s}ct")]
    ARBRE[f"{s}f"] = [F("fiche.pdf", f"{s}ff")]
    ARBRE[f"{s}m"] = [F("arret.pdf", f"{s}ar")]       # jamais importé : hors périmètre


def graph(url, entetes):
    if url.startswith("https://dl.demo/"):
        return 200, PDF, {}
    ok = lambda o: (200, json.dumps(o).encode(), {})                                       # noqa: E731
    url = unquote(url)
    if ":/sites/rh" in url:
        return ok({"id": "SITE"})
    if "/root:/" in url:
        return ok({"value": ARBRE["ROOT"]})
    item = url.split("/items/")[1].split("/")[0]
    return ok({"value": ARBRE.get(item, [])})


sharepoint._TRANSPORT = graph
sharepoint.jeton = lambda: "demo"
sharepoint.compte = lambda: "rh@basiliccafe.example"
sharepoint.etat_connexion = lambda: {"etat": "connecte", "compte": sharepoint.compte()}


# --- exports Excel à déposer ------------------------------------------------------
def xlsx(lignes):
    rows = []
    for r, ligne in enumerate(lignes, 1):
        cells = "".join(
            f'<c r="{chr(65 + c)}{r}"><v>{v}</v></c>' if isinstance(v, (int, float))
            else f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            for c, v in enumerate(ligne) if v is not None)
        rows.append(f'<row r="{r}">{cells}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet {ns}><sheetData>{"".join(rows)}</sheetData></worksheet>')
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships"><sheets><sheet name="Liste" sheetId="1" '
                   'r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
    return buf.getvalue()


EXCEL = BAC / "excel"
EXCEL.mkdir()
(EXCEL / "1_export_rh.xlsx").write_bytes(xlsx([
    ["Nom", "Prénom", "Sexe", "Date de naissance", "N° Téléphone", "Email", "Adresse 1",
     "Code Postal", "Ville", "Nationalité", "Nom de permis de travail / carte de séjour",
     "Fin de carte de séjour", "Date d'entrée", "Poste", "Type de contrat", "Contrat hebdo",
     "N° de sécurité sociale"],
    ["MARTIN", "Camille", "Feminin", "12/03/1990", "06 11 22 33 44", "camille.martin@example.com",
     "8 rue des Lilas", "75011", "Paris", "Français-e", "", "", "02/09/2024", "Manager",
     "Durée indéterminée", 35, "2 90 03 75 123 456 78"],
    ["OKAFOR", "Chidi", "Masculin", "30/06/1998", "07 55 66 77 88", "", "21 bd Voltaire",
     "75011", "Paris", "Nigérian-e", "VLS-TS", "31/08/2027", "14/01/2025", "Equipier polyvalent",
     "Durée indéterminée", 24, "1 98 06 99 345 678 90"],
    ["PETIT", "Lola", "Feminin", "05/11/2002", "", "", "3 impasse du Pont", "75012", "Paris",
     "Français-e", "", "", "01/06/2025", "Equipier polyvalent", "Durée déterminée", 17.5, ""],
    ["GARCIA", "Inès", "Feminin", "19/01/2000", "06 99 88 77 66", "ines.garcia@example.com",
     "12 quai Saint-Antoine", "69002", "Lyon", "Français-e", "", "", "10/02/2025",
     "Equipier polyvalent", "Durée indéterminée", 20, "2 00 01 69 123 456 12"],
    ["INCONNU", "Zed", "Masculin", "01/01/1980", "", "", "", "", "", "Français-e"],
]))
(EXCEL / "2_planning.xlsx").write_bytes(xlsx([
    ["Nom", "Prénom", "Volume Horaire Hebdomadaire (h)*", "Type de Contrat", "Intitulé Poste"],
    ["OKAFOR", "Chidi", 20, "CDI", "Equipier polyvalent"],      # contredit l'export : 24 h
    ["NGUYEN", "Thao", 30, "CDI", "Equipier polyvalent"],
]))

# --- amorçage : quelques dossiers du parcours normal (dont BENALI Sarah) ------------
import contextlib  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    demo.amorcer()

if __name__ == "__main__":
    print(f"""
Démo import — {demo.config.instance()['client']}   (SharePoint simulé, aucun réseau)
  Espace RH  : http://localhost:{os.environ['PORT']}/login   (rh / demo-rh)
  Excel      : {EXCEL}
  Parcours   : Établissements -> « Récupérer les dossiers », puis « Compléter les dossiers »
""", flush=True)
    sys.argv = [sys.argv[0]]
    demo.serveur.demarrer()
