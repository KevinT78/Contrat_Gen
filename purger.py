"""Ce que la purge effacerait — LECTURE SEULE, n'écrit jamais.

    python purger.py            # dossiers éligibles + orphelins + avertissements

La purge elle-même se fait dans l'app (bouton RH derrière @rh, une écriture par
requête) : un `purger.py` qui écrirait serait le premier process écrivain
concurrent du dépôt et casserait l'invariant mono-process (cf. store._verrou).

Sort en code non nul s'il reste des dossiers à purger : un cron qui l'appelle
en simulation devient une alerte, pas un no-op silencieux.
"""
import sys
from pathlib import Path

import config
import store

sys.stdout.reconfigure(encoding="utf-8")


def orphelins():
    """Répertoires portant un dossier.json mais hors de portée de store._scan()
    (glob à profondeur fixe 4) : invisibles à l'app, donc à toute purge. Une
    purge qui dit « tout est propre » en en laissant un ment."""
    vus = {str(p) for p in store._scan().values()}
    return sorted(str(f.parent) for f in config.DONNEES.rglob("dossier.json")
                  if str(f.parent) not in vus)


def main():
    conf = config.conservation()
    if not conf:
        print("Aucun bloc « conservation » dans instance.json — purge non configurée.")
        elig = []
    else:
        elig = store.eligibles()
        cand = conf.get("jours_candidature", conf.get("jours"))
        print(f"{len(elig)} dossier(s) éligible(s) — conservation {conf.get('jours')} j, "
              f"candidatures jamais validées {cand} j :")
        for item, ecoule, duree in elig:
            prenom, nom = config.identite(item["champs"])
            libelle = " ".join(p for p in (prenom, nom.upper()) if p) or item["id"]
            print(f"  {item['id']}  {libelle:32}  {store.etat(item):16}  "
                  f"{ecoule} j / {duree} j")

    orph = orphelins()
    if orph:
        print(f"\n{len(orph)} dossier(s) ORPHELIN(S) — hors profondeur de _scan(), "
              "invisibles à l'app comme à la purge :")
        for o in orph:
            print(f"  {Path(o).relative_to(config.DONNEES)}")

    if config.stockage().get("mode") == "dossier":
        print("\n! Mode « dossier synchronisé » : la purge locale NE vide NI la "
              "corbeille NI l'historique de versions du service de synchronisation. "
              "La version complète de dossier.json (NIR, adresse compris) y reste "
              "récupérable — à vider à la main côté service. Voir docs/ADR-stockage.md.")
    print("\ndata/mails/*.eml : purgés par ancienneté, pas par dossier (aucun uid "
          "dans le nom — artefact du mode console). En mode smtp, ces données sont "
          "dans les boîtes des destinataires, hors de portée.")

    sys.exit(1 if elig else 0)


if __name__ == "__main__":
    main()
