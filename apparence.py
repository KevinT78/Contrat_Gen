"""La direction artistique du client : `theme.json` traduit en tokens CSS.

Un client = un dossier d'instance ; sa DA y vit comme le reste (`theme.json`
+ un dossier `marque/` pour le logo et le favicon). Le code reste un paquet
partage : il ne connait aucun client.

Pourquoi du JSON valide plutot qu'une feuille CSS fournie par le client : une
CSS arbitraire ne se verifie pas (on ne peut pas calculer le contraste d'un
fichier qu'on ne comprend pas) et elle casse en silence a chaque refonte de
base.html. Des tokens se valident, et c'est l'app qui ecrit le CSS -- le
client n'en redige jamais.

Ne connait pas `config` : on lui passe le contenu deja lu de theme.json et
le chemin de `marque/`, et c'est `config.apparence()` qui reste la porte
d'entree de l'app. Pas sans disque pour autant -- _fichier() fait un stat par
fichier declare, parce que verifier la PRESENCE du logo est justement la
moitie du garde-fou ; `resoudre()` se rappelle au rendu de chaque page, pas
dans une boucle serree.
"""
import re
from collections import namedtuple
from pathlib import Path

# Les deux sorties du juge, NOMMEES : le rendu ne veut que les tokens, le
# garde-fou que les raisons, et deux appelants qui piochaient par index
# ([0] / [1]) n'avaient rien pour rattraper une inversion.
DA = namedtuple("DA", "tokens raisons")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_FICHIER = re.compile(r"^[A-Za-z0-9._-]+$")     # pas de chemin, pas de ..
_AA = 4.5                                       # WCAG AA, texte normal

# Les couleurs que le client peut poser, et le nom de la variable CSS de
# base.html derriere. Liste FERMEE : tout le reste de la palette (les tons
# d'etat) est semantique, pas decoratif -- une alerte reste rouge chez un
# client bleu.
_COULEURS = {"accent": "--accent", "fond": "--fond", "surface": "--surface",
             "texte": "--texte", "bord": "--bord"}

# La DA D'ORIGINE DU PRODUIT, pas celle d'un client : c'est ce que voit une
# instance SANS theme.json, et le repli de chaque couleur qu'un client ne pose
# pas. Les couleurs d'un client, elles, ne sont jamais ici -- elles vivent dans
# SON config/theme.json, et le code ne connait aucun client.
#
# Recopiees de base.html parce que le garde-fou doit calculer le contraste des
# couples REELS : un client qui ne pose que `accent` le pose contre CE fond et
# CETTE surface. Si la palette de base.html change, ces cinq-la suivent (le
# test le verifie).
_PALETTE_ORIGINE = {"accent": "#1f4d3f", "fond": "#f7f6f2",
                    "surface": "#ffffff", "texte": "#14140f", "bord": "#e6e3da"}
_BLANC, _NOIR = "#ffffff", "#000000"

_IMAGES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico"}


# Les couleurs secondaires ne sont PAS demandees au client : elles sont
# derivees, parce que le design les a deja construites comme des melanges. Les
# fractions ci-dessous reproduisent les valeurs reglees a la main a 1-4 unites
# RGB pres (--gris = texte melange a 35 % de fond -> #63635e contre #66645b).
# Sans ca, une DA violette gardait des libelles et des soulignements kaki :
# visible a l'oeil, invisible dans la config.
_DERIVES = {
    # variable        depuis      vers        part   emise si le client pose...
    "--gris":        ("texte",   "fond",      .35,  ("texte", "fond")),
    "--gris-clair":  ("texte",   "fond",      .56,  ("texte", "fond")),
    "--bord-fort":   ("bord",    "texte",     .13,  ("bord",)),
    "--surface-2":   ("surface", "fond",      .66,  ("surface", "fond")),
}


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _melange(a, b, part):
    """`a` melange dans `b` a hauteur de `part` (0 = a pur, 1 = b pur)."""
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * part)
                                   for x, y in zip(_rgb(a), _rgb(b)))


def _contraste(a, b):
    """Rapport de contraste WCAG entre deux couleurs opaques (1 a 21)."""
    def luminance(h):
        c = [v / 255 for v in _rgb(h)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
             for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _derive(nom_var, palette):
    """Une secondaire, melangee depuis la palette resolue. LE seul endroit qui
    lit _DERIVES : le garde-fou de contraste et l'emission des variables
    demandent le meme melange, et deux lectures de la table divergeraient."""
    depuis, vers, part, _ = _DERIVES[nom_var]
    return _melange(palette[depuis], palette[vers], part)


def _couples(p):
    """Les couples texte/fond que la DA doit tenir, nommes pour le message.

    Calcules sur la palette RESOLUE, pas sur ce que le client a ecrit : il
    peut poser `accent` seul, il sera lu sur le fond d'origine."""
    clair = _melange(p["accent"], p["fond"], .90)
    gris = _derive("--gris", p)
    return {"le texte sur le fond de page": (p["texte"], p["fond"]),
            "le texte sur les cartes": (p["texte"], p["surface"]),
            # --gris porte les libelles de champ et le texte secondaire : il
            # est derive, mais il doit rester lisible comme le reste.
            # (--gris-clair n'est PAS controle : le design d'origine le pose a
            # 2.87:1, on n'exige pas du client ce qu'on ne s'applique pas.)
            "le texte secondaire sur le fond": (gris, p["fond"]),
            "l'accent sur les cartes": (p["accent"], p["surface"]),
            "l'accent sur le fond de page": (p["accent"], p["fond"]),
            # base.html ecrit `color:#fff` en dur sur fond accent (bouton
            # principal, monogramme) : l'accent doit porter du blanc.
            "le blanc sur l'accent": (_BLANC, p["accent"]),
            "l'accent sur sa teinte pale": (p["accent"], clair)}


def _assombrir(accent, palette):
    """Le meme accent, assombri juste assez pour tenir tous ses couples.
    Sert le message d'erreur : une charte a presque toujours une variante
    foncee, encore faut-il dire laquelle chercher."""
    for cran in range(5, 100, 5):
        essai = _melange(accent, _NOIR, cran / 100)
        p = {**palette, "accent": essai}
        if all(_contraste(a, b) >= _AA for a, b in _couples(p).values()):
            return essai
    return _NOIR


def _nombre(val, mini, maxi):
    return isinstance(val, (int, float)) and not isinstance(val, bool) \
        and mini <= val <= maxi


def _fichier(nom, marque, extensions, raisons, sujet):
    """Un nom de fichier de `marque/` : forme, extension, et PRESENCE REELLE.

    La presence est verifiee parce que le mode d'echec courant n'est pas la
    faute de frappe, c'est le fichier que le graphiste a envoye et que
    personne n'a copie dans l'instance -- l'app demarrait alors verte avec un
    logo casse."""
    if not isinstance(nom, str) or not _FICHIER.match(nom):
        raisons.append(f"« {sujet} » attend un nom de fichier de marque/ "
                       f"(sans chemin), reçu : {nom!r}")
        return None
    if Path(nom).suffix.lower() not in extensions:
        raisons.append(f"« {sujet} » : extension {Path(nom).suffix!r} non "
                       f"prise en charge — au choix : "
                       + ", ".join(sorted(extensions)))
        return None
    if not (marque / nom).is_file():
        raisons.append(f"« {sujet} » : {marque / nom} est absent — le fichier "
                       f"a-t-il été copié dans marque/ ?")
        return None
    return nom


def _inconnues(obj, autorisees, sujet=None):
    """Le message de refus d'une cle inventee, ou None. Nommer les cles
    attendues plutot que refuser sec : la faute de frappe se corrige seule."""
    if inc := sorted(set(obj) - autorisees):
        return ((f"« {sujet} » : " if sujet else "") + "clé(s) inconnue(s) : "
                + ", ".join(inc) + " — au choix : " + ", ".join(sorted(autorisees)))
    return None


def _variables(brut, raisons):
    """Les variables CSS que la palette du client fait bouger.

    Resolue contre la palette d'origine : les couleurs non posees restent
    celles de base.html, donc un client qui ne pose que `accent` le pose
    contre CE fond. Palette douteuse = aucune variable, le contraste n'aurait
    rien de fiable a juger."""
    if not isinstance(brut, dict):
        raisons.append('« palette » attend un objet, ex. {"accent": "#1f4d3f"}')
        return {}
    palette = dict(_PALETTE_ORIGINE)
    for cle, valeur in brut.items():
        if cle not in _COULEURS:
            raisons.append(f"« palette.{cle} » inconnue — au choix : "
                           + ", ".join(sorted(_COULEURS)))
        elif not isinstance(valeur, str) or not _HEX.match(valeur.strip()):
            raisons.append(f"« palette.{cle} » attend une couleur #rrggbb "
                           f"(reçu : {valeur!r})")
        else:
            palette[cle] = valeur.strip().lower()
    if raisons:
        return {}

    # Le contraste est CALCULE, jamais suppose : une DA illisible ne se voit
    # pas de celui qui l'installe, elle se voit du salarie.
    if faibles := [f"{nom} ({_contraste(a, b):.2f}:1)"
                   for nom, (a, b) in _couples(palette).items()
                   if _contraste(a, b) < _AA]:
        raisons.append("contraste insuffisant pour " + ", ".join(faibles)
                       + f" — il faut {_AA}:1 partout. Accent qui passe "
                         f"avec ce fond : {_assombrir(palette['accent'], palette)}")

    var = {nom_var: palette[cle] for cle, nom_var in _COULEURS.items()
           if brut.get(cle)}
    if brut.get("accent"):
        var["--accent-sombre"] = _melange(palette["accent"], _NOIR, .35)
        var["--accent-clair"] = _melange(palette["accent"], palette["fond"], .90)
    # Les secondaires suivent leur source, sinon une DA violette garde des
    # libelles et des soulignements kaki. Emises SEULEMENT si le client a pose
    # la couleur dont elles derivent : sans theme.json, la palette reglee a la
    # main reste intacte au pixel pres.
    var.update({nom_var: _derive(nom_var, palette)
                for nom_var, (*_, declencheurs) in _DERIVES.items()
                if any(brut.get(d) for d in declencheurs)})
    return var


def _logo(brut, marque, raisons):
    """Le logo de l'en-tete, ou None. Forme courte (juste le fichier) ou objet
    -- une charte simple n'a pas a ecrire un objet pour un nom de fichier."""
    if isinstance(brut, str):
        brut = {"fichier": brut}
    if not isinstance(brut, dict):
        raisons.append('« logo » attend un nom de fichier ou un objet, '
                       'ex. {"fichier": "logo.svg", "hauteur": 28}')
        return None
    if message := _inconnues(brut, {"fichier", "hauteur", "remplace_le_nom"},
                             "logo"):
        raisons.append(message)
        return None
    if not (nom := _fichier(brut.get("fichier"), marque, _IMAGES, raisons,
                            "logo.fichier")):
        return None
    hauteur = brut.get("hauteur", 28)
    if not _nombre(hauteur, 16, 40):
        raisons.append("« logo.hauteur » attend un nombre de pixels entre 16 "
                       f"et 40 (reçu : {hauteur!r}) — l'en-tête fait 60px "
                       "de haut")
        return None
    return {"fichier": nom, "hauteur": hauteur,
            "remplace_le_nom": bool(brut.get("remplace_le_nom"))}


def resoudre(t, marque):
    """LE seul juge de theme.json : rend DA(tokens, raisons).

    Un validateur pour deux appelants -- `config.apparence()` au rendu et
    `config._verifier_theme()` au demarrage. Deux copies des regles divergent,
    et une DA refusee au demarrage finirait quand meme dans la page."""
    vide = {"variables": {}, "logo": None, "favicon": None}
    if not t:
        return DA(vide, [])
    if not isinstance(t, dict):
        return DA(vide, ['attend un objet, ex. {"palette": {"accent": "#1f4d3f"}}'])
    if message := _inconnues(t, {"palette", "logo", "favicon"}):
        return DA(vide, [message])

    raisons = []
    var = _variables(t.get("palette") or {}, raisons)
    logo = _logo(t["logo"], marque, raisons) if t.get("logo") is not None else None
    favicon = (_fichier(t["favicon"], marque, _IMAGES, raisons, "favicon")
               if t.get("favicon") is not None else None)
    return DA({"variables": var, "logo": logo, "favicon": favicon}, raisons)
