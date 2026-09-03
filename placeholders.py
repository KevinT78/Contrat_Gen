"""Fiche des {{Placeholders}} à remettre au client, générée depuis SA config.

    python placeholders.py            # écrit config/PLACEHOLDERS.md

C'est le client qui balise ses propres .docx : il lui faut donc la liste exacte
des jetons valides pour son instance. Cette liste est *dérivée* de sa config
(formulaire.json, derives, saisie_rh, grille, mentions des sociétés) — elle
change dès qu'on touche à sa config, donc on la génère au lieu de la rédiger.

À relancer et renvoyer au client après toute modification de config/.
"""
import sys
from pathlib import Path

import config

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

EN_TETE = """# Placeholders — {client}

Vos modèles de contrat sont des `.docx` ordinaires. Là où une valeur doit être
remplie automatiquement, écrivez le jeton correspondant **exactement** comme
dans le tableau ci-dessous.

Trois règles, sans exception :

1. **Deux accolades collées, sans espace à l'intérieur** : `{{{{Nom}}}}`, jamais
   `{{{{ Nom }}}}` — le texte est remplacé littéralement.
2. **La casse compte** : `{{{{Nom}}}}` fonctionne, `{{{{nom}}}}` non.
3. **Un jeton absent de ce tableau bloque la mise en service** : l'application
   refuse de démarrer et vous dit lequel. Rien ne part en production à moitié
   rempli.

Un jeton peut apparaître autant de fois que voulu dans le document.

## Jetons disponibles

| Jeton | Ce qu'il contient |
|---|---|
"""


def fiche():
    lignes = [EN_TETE.format(client=config.instance()["client"])]
    for ph, origine in sorted(config.placeholders_connus().items()):
        lignes.append(f"| `{{{{{ph}}}}}` | {origine} |\n")

    lignes.append("\n## Modèles attendus\n\n")
    for nom in sorted(config._templates_actifs()):
        etat = "déposé" if (config.CLIENT / "contrats" / nom).exists() else "**manquant**"
        lignes.append(f"- `config/contrats/{nom}` — {etat}\n")
    return "".join(lignes)


if __name__ == "__main__":
    dest = Path(config.CLIENT) / "PLACEHOLDERS.md"
    dest.write_text(fiche(), encoding="utf-8")
    n = len(config.placeholders_connus())
    print(f"{dest} — {n} jetons pour « {config.instance()['client']} ».")
