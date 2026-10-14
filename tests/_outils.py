"""Petits outils partagés par les scripts de test (importés depuis tests/)."""
import os


def base_mongo(nom):
    """Base vide pour ce script : mongomock par défaut (en mémoire, propre au
    process : un sous-process repart d'une base vide), ou la base `test-<nom>`
    du MONGO_URI réel, vidée. À appeler avant le premier accès à store.db()."""
    os.environ.setdefault("MONGO_URI", "mongomock://")
    os.environ["MONGO_DB"] = f"test-{nom}"
    if not os.environ["MONGO_URI"].startswith("mongomock://"):
        import pymongo
        pymongo.MongoClient(os.environ["MONGO_URI"]).drop_database(os.environ["MONGO_DB"])


def corps_texte(msg):
    """Corps texte d'un mail, simple ou multipart (le récap a aussi une part HTML)."""
    part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    return part.get_payload(decode=True).decode("utf-8")


def mails(modele=None):
    """Mails du mode console, du plus ancien au plus récent (collection `mails`),
    en objets email. Même process que le code testé : une base mongomock est
    propre à son process."""
    import email
    import store
    filtre = {"modele": modele} if modele else {}
    return [email.message_from_bytes(d["eml"])
            for d in store.db().mails.find(filtre).sort("_id", 1)]


def compte(ident="rh", mdp="fixture"):
    """Compte de connexion en base ET dans l'amorce que les sous-process
    mongomock rechargent (MONGOMOCK_AMORCE) : leur base repart vide sinon."""
    import json
    import tempfile
    from werkzeug.security import generate_password_hash
    import store
    doc = {"_id": ident, "mdp_hash": generate_password_hash(mdp)}
    store.db().comptes.replace_one({"_id": ident}, doc, upsert=True)
    if os.environ["MONGO_URI"].startswith("mongomock://"):
        chemin = os.environ.get("MONGOMOCK_AMORCE")
        if not chemin:
            chemin = os.environ["MONGOMOCK_AMORCE"] = os.path.join(
                tempfile.mkdtemp(prefix="contratgen-amorce-"), "amorce.json")
        amorce = json.load(open(chemin, encoding="utf-8")) if os.path.exists(chemin) else {}
        amorce["comptes"] = [d for d in amorce.get("comptes", []) if d["_id"] != ident] + [doc]
        json.dump(amorce, open(chemin, "w", encoding="utf-8"))
