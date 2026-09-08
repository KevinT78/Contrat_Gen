"""Mail hebdomadaire au cabinet comptable — à mettre en cron / Tâche planifiée
côté client (aucun scheduler dans l'app).

    python recap.py              # dossiers remis ces 7 derniers jours
    python recap.py --jours 14

Un mail PAR CABINET (comptable_email de la société, repli mails.comptable_defaut),
la RH en copie (mails.recap, repli mails.rh) : les dossiers remis au comptable
sur la fenêtre, chacun avec son lien de lot signé (30 jours, révocable par
« Refaire la copie et le lien »). Un cabinet ne voit jamais les dossiers d'une
société qui n'est pas la sienne. Aucun mail à un cabinet sans dossier.

La fenêtre est dérivée du journal (« vers == RemisComptable », ou un
« renvoi_comptable »). Simplification volontaire : pas d'état « déjà envoyé » —
deux exécutions le même jour donnent le même mail, pas un doublon de contenu.
"""
import sys
from datetime import datetime, timedelta, timezone

import config
import contrat
import mails
import store

sys.stdout.reconfigure(encoding="utf-8")


def remis_depuis(seuil):
    """[item] des dossiers remis (ou renvoyés) au comptable après `seuil`."""
    out = []
    for item in store.tout():
        if store.etat(item) != "RemisComptable":
            continue
        for e in item["journal"]:
            if (e.get("vers") == "RemisComptable" or e.get("type") == "renvoi_comptable") \
                    and datetime.fromisoformat(e["le"]) >= seuil:
                out.append(item)
                break
    return out


def lien(item):
    jeton = store.signer("lot_comptable", item["id"], item.get("lien_comptable_epoch", 0))
    return f"{config.url_publique()}/lot/{jeton}"


def ligne(item):
    c = item["champs"]
    prenom, nom = config.identite(c)
    return (f"- {prenom} {nom.upper()} — {config.valeur(c, 'poste')} — "
            f"{config.valeur(c, 'etablissement')} — "
            f"début {contrat.date_fr(config.valeur(c, 'date_debut', '?'))}\n  {lien(item)}")


def par_cabinet(items):
    """{adresse du cabinet: [items]}"""
    groupes = {}
    for item in items:
        cab = config.comptable(config.valeur(item["champs"], "etablissement"))
        groupes.setdefault(cab, []).append(item)
    return groupes


def main(jours):
    seuil = datetime.now(timezone.utc) - timedelta(days=jours)
    periode = f"{seuil:%d/%m/%Y} → {datetime.now(timezone.utc):%d/%m/%Y}"
    conf = config.instance()["mails"]
    copie = conf.get("recap") or conf["rh"]
    groupes = par_cabinet(remis_depuis(seuil))
    print(f"{sum(map(len, groupes.values()))} dossier(s) remis sur {periode}, "
          f"{len(groupes)} cabinet(s)")
    rate = False
    for cab, items in groupes.items():
        ok, raison = mails.envoyer("recap_hebdo", cab, cc=copie, periode=periode,
                                   nombre=len(items), liste="\n".join(map(ligne, items)))
        print(f"  {cab} : {len(items)} dossier(s) — "
              + ("mail envoyé" if ok else f"mail NON envoyé : {raison}"))
        rate |= not ok
    sys.exit(1 if rate else 0)


if __name__ == "__main__":
    j = 7
    if "--jours" in sys.argv:
        j = int(sys.argv[sys.argv.index("--jours") + 1])
    main(j)
