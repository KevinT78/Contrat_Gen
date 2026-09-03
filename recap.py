"""Récap hebdomadaire des nouveaux salariés — à mettre en cron / Tâche planifiée
côté client (aucun scheduler dans l'app).

    python recap.py              # 7 derniers jours
    python recap.py --jours 14

La fenêtre est dérivée du journal (entrée « vers == ATraiter » = le salarié est
entré). ponytail: pas d'état « déjà envoyé » — deux exécutions le même jour
donnent le même mail, pas un doublon de contenu.
"""
import sys
from datetime import datetime, timedelta, timezone

import config
import mails
import store

sys.stdout.reconfigure(encoding="utf-8")


def entres_depuis(seuil):
    """[(item, date_entree)] pour les dossiers passés à ATraiter après `seuil`."""
    out = []
    for item in store.tout():
        for e in item["journal"]:
            if e.get("vers") == "ATraiter" and datetime.fromisoformat(e["le"]) >= seuil:
                out.append((item, e["le"][:10]))
                break
    return out


def ligne(item):
    c = item["champs"]
    nom = (c.get("nom_usage") or c.get(config.role("nom", "nom_naissance")) or "").upper()
    return (f"- {c.get('prenom', '')} {nom} — {c.get('poste', '')} — "
            f"{c.get('etablissement', '')} — "
            f"début {c.get(config.role('date_debut', 'date_debut'), '?')}")


def main(jours):
    seuil = datetime.now(timezone.utc) - timedelta(days=jours)
    entres = entres_depuis(seuil)
    periode = f"{seuil.date().isoformat()} → {datetime.now(timezone.utc).date().isoformat()}"
    liste = "\n".join(ligne(i) for i, _ in entres) or "(aucun)"

    conf = config.instance()["mails"]
    ok, raison = mails.envoyer("recap_hebdo", conf.get("recap") or conf["rh"],
                               periode=periode, nombre=len(entres), liste=liste)
    print(f"{len(entres)} salarié(s) sur {periode}")
    print("mail envoyé" if ok else f"mail NON envoyé : {raison}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    j = 7
    if "--jours" in sys.argv:
        j = int(sys.argv[sys.argv.index("--jours") + 1])
    main(j)
