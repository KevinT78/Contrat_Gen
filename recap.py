"""Mail hebdomadaire au cabinet comptable — à mettre en cron / Tâche planifiée
côté client (aucun scheduler dans l'app).

    python recap.py              # dossiers remis depuis le dernier envoi réussi
    python recap.py --jours 14   # fenêtre du tout premier envoi d'un cabinet (défaut 7)

Un mail PAR CABINET (comptable_email de la société, repli mails.comptable_defaut),
la RH en copie (mails.recap, repli mails.rh) : les dossiers remis au comptable
depuis le dernier envoi réussi à ce cabinet, chacun avec son lien de lot signé
(30 jours, révocable par « Refaire la copie et le lien »). Un cabinet ne voit
jamais les dossiers d'une société qui n'est pas la sienne. Aucun mail à un
cabinet sans dossier.

Remise lue dans le journal (« vers == RemisComptable », ou un
« renvoi_comptable ») ; dernier envoi réussi et dernier échec par cabinet dans
la collection `recap` (écrite ici seulement, lu par l'écran Salariés). Un envoi
raté ne fait rien avancer : les dossiers repartent au prochain passage.
"""
import html
import sys
from datetime import datetime, timedelta, timezone

import config
import contrat
import mails
import store

sys.stdout.reconfigure(encoding="utf-8")

_PHRASE_LIEN = "Cliquer pour consulter le dossier"


def _remis_apres(item, seuil):
    return any(store.est_remise(e) and datetime.fromisoformat(e["le"]) >= seuil
               for e in item["journal"])


def remis_depuis(seuil):
    """[item] des dossiers remis (ou renvoyés) au comptable après `seuil`."""
    return [i for i in store.tout()
            if store.etat(i) == "RemisComptable" and _remis_apres(i, seuil)]


def lien(item):
    jeton = store.signer("lot_comptable", item["id"], item.get("lien_comptable_epoch", 0))
    return f"{config.url_publique()}/lot/{jeton}"


def resume(item):
    c = item["champs"]
    prenom, nom = config.identite(c)
    # prenom vide quand le formulaire n'a qu'un champ « Nom Prénom »
    return (f"{' '.join(filter(None, (prenom, nom.upper())))} — {config.valeur(c, 'poste')} — "
            f"{config.valeur(c, 'etablissement')} — "
            f"début {contrat.date_fr(config.valeur(c, 'date_debut', '?'))}")


def ligne(item):
    return f"- {resume(item)}\n  {_PHRASE_LIEN} : {lien(item)}"


def ligne_html(item):
    url = html.escape(lien(item), quote=True)
    return (f"<li>{html.escape(resume(item))}<br>"
            f'<a href="{url}">{_PHRASE_LIEN}</a></li>')


def corps_html(periode, nombre, items):
    """Version HTML du gabarit recap_hebdo : la phrase est le lien cliquable."""
    return (
        "<!DOCTYPE html><html><body>"
        "<p>Bonjour,</p>"
        f"<p>{nombre} dossier(s) d'embauche vous ont été remis "
        f"sur la période {html.escape(periode)} :</p>"
        f"<ul>{''.join(map(ligne_html, items))}</ul>"
        "<p>Chaque lien est personnel, valable 30 jours, et donne accès aux pièces "
        "du salarié, au contrat et à l'accusé DPAE (« Tout télécharger »).</p>"
        "<p>--<br>Envoi automatique, ne pas répondre.</p>"
        "</body></html>"
    )


def par_cabinet(items):
    """{adresse du cabinet: [items]}"""
    groupes = {}
    for item in items:
        cab = config.comptable(config.valeur(item["champs"], "etablissement"))
        groupes.setdefault(cab, []).append(item)
    return groupes


def lire_etat():
    """{cabinet: {"depuis": iso, "echec": {"le", "motif", "nombre"}?}} -- ecrit
    par recap.py SEUL, lu par l'app (bandeau Salaries). Jamais dans un dossier :
    l'etat d'envoi est celui du cabinet, pas d'un salarie."""
    return {d.pop("_id"): d for d in store.db().recap.find()}


def _ecrire_etat(etat):
    col = store.db().recap
    col.delete_many({"_id": {"$nin": list(etat)}})
    for cab, e in etat.items():
        col.replace_one({"_id": cab}, e, upsert=True)


def main(jours):
    """`jours` ne sert qu'au premier envoi d'un cabinet. Ensuite chaque cabinet
    repart de son dernier envoi REUSSI : un mail rate (SMTP en panne) laisse ses
    dossiers en attente au lieu de les faire sortir de la fenetre la semaine
    suivante -- le cabinet ne les aurait jamais recus."""
    debut = datetime.now(timezone.utc)
    defaut = (debut - timedelta(days=jours)).isoformat(timespec="seconds")
    etat = lire_etat()
    seuil = lambda cab: datetime.fromisoformat(etat.get(cab, {}).get("depuis", defaut))
    plus_ancien = min([defaut] + [e["depuis"] for e in etat.values()], key=datetime.fromisoformat)
    groupes = {}
    for cab, items in par_cabinet(remis_depuis(datetime.fromisoformat(plus_ancien))).items():
        if items := [i for i in items if _remis_apres(i, seuil(cab))]:
            groupes[cab] = items
    conf = config.instance()["mails"]
    copie = conf.get("recap") or conf["rh"]
    print(f"{sum(map(len, groupes.values()))} dossier(s) à annoncer, {len(groupes)} cabinet(s)")
    rate = False
    for cab, items in groupes.items():
        periode = f"{seuil(cab):%d/%m/%Y} → {debut:%d/%m/%Y}"
        ok, raison = mails.envoyer(
            "recap_hebdo", cab, cc=copie, periode=periode,
            nombre=len(items), liste="\n".join(map(ligne, items)),
            html=corps_html(periode, len(items), items))
        print(f"  {cab} : {len(items)} dossier(s) — "
              + ("mail envoyé" if ok else f"mail NON envoyé : {raison}"))
        if ok:
            etat[cab] = {"depuis": debut.isoformat(timespec="seconds")}
        else:
            etat[cab] = {"depuis": seuil(cab).isoformat(timespec="seconds"),
                         "echec": {"le": debut.isoformat(timespec="seconds"),
                                   "motif": raison, "nombre": len(items)}}
        _ecrire_etat(etat)          # apres CHAQUE cabinet : un arret en route ne renvoie
        rate |= not ok              # pas leur mail aux cabinets deja servis
    # Echec d'un cabinet qui n'a plus rien a annoncer (dossiers archives depuis) :
    # sans ce menage, le bandeau Salaries l'annoncerait « en attente » pour toujours.
    if [cab for cab, e in etat.items() if cab not in groupes and e.pop("echec", None)]:
        _ecrire_etat(etat)
    sys.exit(1 if rate else 0)


if __name__ == "__main__":
    j = 7
    if "--jours" in sys.argv:
        j = int(sys.argv[sys.argv.index("--jours") + 1])
    main(j)
