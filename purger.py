"""Ce que la purge effacerait — LECTURE SEULE, n'écrit jamais.

    python purger.py            # dossiers éligibles + avertissements

La purge elle-même se fait dans l'app (bouton RH derrière @rh, une écriture par
requête) : pas de cron à déployer, la RH voit ce qui part avant de confirmer.

Sort en code non nul s'il reste des dossiers à purger : un cron qui l'appelle
en simulation devient une alerte, pas un no-op silencieux.
"""
import sys

import config
import store

sys.stdout.reconfigure(encoding="utf-8")


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
        # Ni nom ni NIR : sur le VPS, cette sortie finit dans le journal systemd,
        # sur le disque. L'id suffit à retrouver le dossier dans l'app.
        for item, ecoule, duree in elig:
            etab = item["champs"].get("etablissement") or "?"
            print(f"  {item['id']}  {etab:40}  {store.etat(item):16}  "
                  f"{ecoule} j / {duree} j")

    print("\ncollection mails (.eml) : purgés par ancienneté, pas par dossier (aucun uid "
          "dans le nom — artefact du mode console). En mode smtp, ces données sont "
          "dans les boîtes des destinataires, hors de portée.")

    sys.exit(1 if elig else 0)


if __name__ == "__main__":
    main()
