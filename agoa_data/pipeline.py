"""Chaîne complète : adresse → parcelle → bâtiments → voies → dalles → nuage → volumétrie → coupes → livrables."""
import datetime
import os
import re
import unicodedata

from . import coupes as cp
from . import export, geo, lidar, volumetrie

TYPES_VOIE = {"rue", "avenue", "av", "boulevard", "bd", "place", "pl", "passage", "impasse", "villa", "allee",
              "quai", "cite", "square", "route", "chemin", "cour", "faubourg", "fbg", "voie", "sentier", "ruelle"}
ARTICLES = {"de", "du", "des", "la", "le", "les", "l", "d", "et", "aux", "au"}


def nom_dossier(label):
    """« 198 Rue Championnet 75018 Paris » → « CHAMPIONNET198 » (convention des dossiers de l'agence)."""
    m = re.match(r"\s*(\d+)\s*(bis|ter)?\s+(.*?)\s+(\d{5})\b", label + " ", re.I)
    if not m:
        return re.sub(r"\W+", "", label)[:30].upper()
    num, suf, voie = m.group(1), (m.group(2) or ""), m.group(3)
    voie = unicodedata.normalize("NFD", voie).encode("ascii", "ignore").decode().lower()
    mots = [w for w in re.split(r"[^a-z0-9]+", voie) if w and w not in ARTICLES and w not in TYPES_VOIE]
    mots = ["st" if w == "saint" else "ste" if w == "sainte" else w for w in mots]
    return ("".join(mots) + num + suf).upper()


def analyser(texte_adresse, dossier_sortie, progress=None, cache=None, nom=None):
    """Exécute toute la chaîne et écrit les livrables dans `dossier_sortie/<NOM>/`. Retourne un dict de résultats."""
    say = progress or (lambda s: None)
    say("Recherche de l'adresse…")
    adr = geo.geocoder(texte_adresse)
    say(f"Adresse : {adr.label}")
    say("Parcelle cadastrale…")
    parc = geo.parcelle_au_point(adr)
    say(f"Parcelle {parc.section} {parc.numero} ({parc.contenance:.0f} m²) ; bâtiments BD TOPO…")
    bats = geo.batiments_de_la_parcelle(parc)
    if not bats:
        raise geo.DataError("Aucun bâtiment BD TOPO sur cette parcelle.")
    principal = bats[0]
    say("Voies riveraines…")
    voies = geo.voies_du_batiment(principal)
    say("Dalles LiDAR HD…")
    zone = principal.geom
    for b in bats[1:]:
        zone = zone.union(b.geom)
    dalles = geo.dalles_lidar(zone, marge=45)
    dossier = os.path.join(dossier_sortie, nom or nom_dossier(adr.label))
    os.makedirs(dossier, exist_ok=True)
    pts = lidar.points_sur_emprise(dalles, zone, marge=40, progress=say, cache_dir=cache)
    say(f"{len(pts):,} points reçus ; calcul des volumes…".replace(",", " "))

    res_b = []
    vols = []
    for b in bats:
        try:
            v = volumetrie.calculer(pts, b.geom)
        except ValueError as e:
            res_b.append({"id": b.id, "usage": b.usage, "annee": b.annee, "etages": b.etages,
                          "part_sur_parcelle": round(b.part_sur_parcelle, 2), "erreur": str(e)})
            vols.append(None)
            continue
        vols.append(v)
        d = {"id": b.id, "usage": b.usage, "annee": b.annee, "etages": b.etages,
             "part_sur_parcelle": round(b.part_sur_parcelle, 2), "volumetrie": v.resume()}
        if b.hauteur:
            d["hauteur_bdtopo"] = b.hauteur
            d["volume_bdtopo"] = b.geom.area * b.hauteur
        res_b.append(d)
    ok = [(b, v) for b, v in zip(bats, vols) if v is not None]
    if not ok:
        raise geo.DataError("Les bâtiments de cette parcelle ne sont pas classés « bâtiment » dans le nuage.")
    b0, v0 = ok[0]

    say("Coupes perpendiculaires aux voies…")
    liste = cp.definir_coupes(b0.geom, voies)
    sorties = []
    for i, c in enumerate(liste, 1):
        q, z, cl = cp.analyser_coupe(pts, b0.geom, c, v0.altitude_sol)
        sens = "perp" if c.type == "perpendiculaire" else "para"
        fn = f"coupe_{i}_{sens}_{re.sub(r'[^A-Za-z0-9]+', '_', c.nom)}.png"
        cp.tracer_coupe(c, q, z, cl, b0.geom, os.path.join(dossier, fn))
        sorties.append({"voie": c.nom, "type": c.type, "distance_voie_batiment": round(c.voie_distance, 1),
                        "fichier": fn, "resultats": {k: (round(x, 2) if isinstance(x, float) else x) for k, x in c.r.items()}})
    cp.tracer_plan(pts, b0.geom, parc, voies, liste, os.path.join(dossier, "plan_coupes.png"))

    say("Écriture des fichiers…")
    export.ecrire_laz(pts, os.path.join(dossier, "nuage_decoupe.laz"))
    export.ecrire_asc(v0, os.path.join(dossier, "modele_toiture.asc"))
    res = {"adresse": {"label": adr.label, "lon": adr.lon, "lat": adr.lat, "x_l93": adr.x, "y_l93": adr.y},
           "parcelle": {"idu": parc.idu, "section": parc.section, "numero": parc.numero, "contenance": parc.contenance},
           "dalles": [d.nom for d in dalles], "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
           "n_points": int(len(pts)), "batiments": res_b, "voies": [{"nom": v.nom, "distance": round(v.distance, 1)} for v in voies],
           "coupes": sorties, "dossier": dossier}
    export.ecrire_json(res, os.path.join(dossier, "resultats.json"))
    with open(os.path.join(dossier, "synthese.txt"), "w", encoding="utf-8") as f:
        f.write(export.synthese_texte(res))
    say("Terminé.")
    return res
