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
