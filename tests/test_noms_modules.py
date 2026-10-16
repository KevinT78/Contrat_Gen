"""Aucun module du dépôt ne masque un module de la bibliothèque standard.

    python tests/test_noms_modules.py

`python app.py` met la racine du dépôt en tête de sys.path : un `compression.py`
à la racine masquait le paquet `compression` ajouté par Python 3.14, que `gzip`
importe (via lxml, donc python-docx) — l'app plantait au démarrage sur le VPS.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
RACINE = Path(__file__).resolve().parent.parent

# stdlib_module_names est celui de l'interpréteur qui lance le test : on y ajoute
# les modules des versions plus récentes, absents d'un Python plus ancien.
STDLIB = set(sys.stdlib_module_names) | {"compression", "annotationlib"}

masques = sorted(p.name for p in RACINE.glob("*.py") if p.stem in STDLIB)
assert not masques, f"masquent la bibliothèque standard : {masques}"
print("ok  noms de modules : aucun fichier de la racine ne masque la bibliothèque standard")
