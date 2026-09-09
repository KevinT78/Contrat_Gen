"""Champs piece_jointe multi-fichiers (ex. carte d'identite recto + verso).

    python tests/test_pieces_multi.py

Ecrit dans un dossier temporaire, ne touche jamais data/.
Couvre : nommage indexe (1er = nom nu, retro-compatible), fichiers_role,
_extraire_role, manquantes (max_fichiers = plafond, pas minimum), et le
chemin dict simple (import myrhis / tests) qui n'a pas .getlist.
"""
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DONNEES"] = tempfile.mkdtemp(prefix="contratgen-multi-")

from werkzeug.datastructures import FileStorage   # noqa: E402
import config          # noqa: E402
import store           # noqa: E402

for zone in ("soumissions",):
    (config.DONNEES / zone).mkdir(parents=True, exist_ok=True)

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


def fichier():
    return FileStorage(stream=BytesIO(PDF), filename="scan.pdf")


class MultiDict(dict):
    """getlist minimal facon werkzeug, pour l'entree formulaire."""
    def getlist(self, k):
        return self.get(k, [])


ident = next(c for c in config.pieces() if c.get("max_fichiers", 1) > 1)
mono = next(c for c in config.pieces() if c.get("max_fichiers", 1) == 1)
champs = {c["id"]: "x" for c in config.champs() if c["type"] != "piece_jointe"}


def cas(nom, fn):
    fn()
    print(f"  ✓ {nom}")


def test_premier_fichier_garde_le_nom_nu():
    recus = MultiDict({ident["id"]: [fichier(), fichier()], mono["id"]: fichier()})
    uid = store.creer_soumission(dict(champs), recus)
    item = store.lire(uid)
    noms = set(store.fichiers(item, "pieces"))
    role = store.SAIN.sub("-", ident["role"])
    assert f"{role}.pdf" in noms, noms
    assert f"{role}_2.pdf" in noms, noms
    assert store._extraire_role(f"{role}_2.pdf") == role
    assert len(store.fichiers_role(item, "pieces", ident["role"])) == 2


def test_un_seul_fichier_suffit_manquantes_vide():
    recus = MultiDict({c["id"]: fichier() for c in config.pieces()})
    recus[ident["id"]] = [fichier()]          # une seule face
    uid = store.creer_soumission(dict(champs), recus)
    item = store.lire(uid)
    role = store.SAIN.sub("-", ident["role"])
    assert store.fichiers_role(item, "pieces", ident["role"]) == [f"{role}.pdf"]
    assert store.manquantes(item) == [], "max_fichiers est un plafond, pas un minimum"


def test_chemin_dict_simple_sans_getlist():
    recus = {c["id"]: fichier() for c in config.pieces()}   # dict nu, pas de .getlist
    uid = store.creer_soumission(dict(champs), recus)
    item = store.lire(uid)
    assert store.manquantes(item) == []
    assert len(store.fichiers_role(item, "pieces", ident["role"])) == 1


cas("1er fichier = nom nu, 2e = _2, fichiers_role en compte 2", test_premier_fichier_garde_le_nom_nu)
cas("une seule face depose -> manquantes() vide", test_un_seul_fichier_suffit_manquantes_vide)
cas("dict nu (sans getlist) accepte par creer_soumission", test_chemin_dict_simple_sans_getlist)
print("\nPieces multi-fichiers OK — nommage indexe retro-compatible, plafond, dict et MultiDict.")
