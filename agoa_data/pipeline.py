"""Chaîne complète : adresse → parcelle → bâtiments → voies → dalles → nuage → volumétrie → coupes → livrables."""
import datetime
import os
import re
import unicodedata

from . import cadastre, dwg, export, facades, geo, lidar, plans, volumetrie
from . import coupes as cp

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


def _dossier(adr, dossier_sortie, sous, nom=None):
    """<dossier choisi>/<NOM>/<sous-dossier> : cadastre, volumetrie ou plan_dwg."""
    d = os.path.join(dossier_sortie, nom or nom_dossier(adr.label), sous)
    os.makedirs(d, exist_ok=True)
    return d


def analyser(adresse, dossier_sortie, progress=None, cache=None, nom=None, facade_seule=False):
    """Exécute toute la chaîne et écrit les livrables dans `dossier_sortie/<NOM>/`. Retourne un dict de résultats.

    `adresse` : texte libre ou `geo.Adresse` (déjà choisie dans les suggestions).
    `facade_seule` : ne produit que le dessin des façades sur rue (dossier `facade`), sans les livrables de volumétrie.
    """
    say = progress or (lambda s: None)
    say("Recherche de l'adresse…")
    adr = geo.geocoder(adresse)
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
    dossier = _dossier(adr, dossier_sortie, "facade" if facade_seule else "volumetrie", nom)
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
    donnees = []
    for i, c in enumerate(liste, 1):
        q, z, cl = cp.analyser_coupe(pts, b0.geom, c, v0.altitude_sol)
        donnees.append((c, q, z, cl))
        sens = "perp" if c.type == "perpendiculaire" else "para"
        fn = f"coupe_{i}_{sens}_{re.sub(r'[^A-Za-z0-9]+', '_', c.nom)}.png"
        if not facade_seule:
            cp.tracer_coupe(c, q, z, cl, b0.geom, os.path.join(dossier, fn))
        sorties.append({"voie": c.nom, "type": c.type, "distance_voie_batiment": round(c.voie_distance, 1),
                        "fichier": fn, "resultats": {k: (round(x, 2) if isinstance(x, float) else x) for k, x in c.r.items()}})
    say("Façades sur rue…")
    dfac = dossier if facade_seule else _dossier(adr, dossier_sortie, "facade", nom)
    fac = facades.definir_facades(b0.geom, parc.geom, voies)
    fac_res = []
    for i, f in enumerate(fac, 1):
        facades.profils(pts, f, b0.geom, v0.altitude_sol, b0.etages)
        fn = f"facade_{i}_{re.sub(r'[^A-Za-z0-9]+', '_', f.voie)}.png"
        if "abscisses" in f.r:
            facades.tracer_facade(f, os.path.join(dfac, fn))
        fac_res.append({**facades.resume(f), "fichier": fn})
    if fac:
        facades.ecrire_facades_dxf(fac, os.path.join(dfac, "facades.dxf"))
    if facade_seule:
        res = {"mode": "facade", "adresse": {"label": adr.label}, "parcelle": {"idu": parc.idu, "section": parc.section,
               "numero": parc.numero, "contenance": parc.contenance}, "facades": fac_res, "facades_dxf": "facades.dxf" if fac else None,
               "dossier": dossier, "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
        export.ecrire_json(res, os.path.join(dossier, "facades.json"))
        say("Terminé.")
        return res
    cp.tracer_plan(pts, b0.geom, parc, voies, liste, os.path.join(dossier, "plan_coupes.png"))

    say("Écriture des fichiers…")
    export.ecrire_laz(pts, os.path.join(dossier, "nuage_decoupe.laz"))
    say("Nuage de points et coupes en DXF…")
    _, n_dxf = dwg.ecrire_nuage_dxf(pts, os.path.join(dossier, "nuage_decoupe.dxf"), emprise=zone, coupes=liste)
    dwg.ecrire_coupes_dxf(donnees, os.path.join(dossier, "coupes.dxf"))
    export.ecrire_asc(v0, os.path.join(dossier, "modele_toiture.asc"))
    res = {"adresse": {"label": adr.label, "lon": adr.lon, "lat": adr.lat, "x_l93": adr.x, "y_l93": adr.y},
           "parcelle": {"idu": parc.idu, "section": parc.section, "numero": parc.numero, "contenance": parc.contenance},
           "dalles": [d.nom for d in dalles], "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
           "n_points": int(len(pts)), "batiments": res_b, "voies": [{"nom": v.nom, "distance": round(v.distance, 1)} for v in voies],
           "coupes": sorties, "dossier": dossier, "mode": "volumetrie",
           "nuage_dxf": "nuage_decoupe.dxf", "n_points_dxf": n_dxf, "coupes_dxf": "coupes.dxf",
           "facades": fac_res, "dossier_facade": dfac}
    export.ecrire_json(res, os.path.join(dossier, "resultats.json"))
    with open(os.path.join(dossier, "synthese.txt"), "w", encoding="utf-8") as f:
        f.write(export.synthese_texte(res))
    say("Terminé.")
    return res


# --------------------------------------------------------------------------------------------


def cadastres(adresse, dossier_sortie, progress=None):
    """Bouton « Cadastres » : trois plans cadastraux A3 vectoriels (1/500, 1/1000, 1/2000)."""
    say = progress or (lambda s: None)
    say("Recherche de l'adresse…")
    adr = geo.geocoder(adresse)
    say(f"Adresse : {adr.label}")
    say("Parcelle cadastrale…")
    parc = geo.parcelle_au_point(adr)
    say(f"Parcelle {parc.section} {parc.numero} ({parc.contenance:.0f} m²) ; lecture du cadastre alentour…")
    dossier = _dossier(adr, dossier_sortie, "cadastre")
    noms = cadastre.plans_cadastraux(adr, parc, dossier, progress=say)
    say("Terminé.")
    return {"mode": "cadastre", "adresse": {"label": adr.label}, "parcelle": {"idu": parc.idu, "section": parc.section,
            "numero": parc.numero, "contenance": parc.contenance}, "cadastre_pdfs": noms, "dossier": dossier,
            "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}


def plans_dwg(pdfs, adresse, dossier_sortie, echelle=None, progress=None):
    """Bouton « Plans DWG » : plans PDF fournis → fond de plan DXF/DWG recalé sur le cadastre (Lambert 93).

    `adresse` peut être vide : le plan est alors laissé dans son repère local, sans recalage.
    `echelle` : dénominateur imposé (50 pour 1/50) ; sinon lu dans le PDF.
    """
    say = progress or (lambda s: None)
    if not pdfs:
        raise geo.DataError("Choisissez au moins un plan PDF.")
    adr = parc = None
    contexte = None
    cibles = []
    if adresse:
        say("Recherche de l'adresse…")
        adr = geo.geocoder(adresse)
        say(f"Adresse : {adr.label} ; parcelle et bâtiments…")
        parc = geo.parcelle_au_point(adr)
        bats = geo.batiments_de_la_parcelle(parc)
        batiments_parcelle = bats[0].geom if bats else None
        for b in bats[1:]:
            batiments_parcelle = batiments_parcelle.union(b.geom)
        cibles = [("bâtiments de la parcelle", batiments_parcelle)] if batiments_parcelle is not None else []
        cibles.append(("parcelle", parc.geom))             # les caves débordent souvent de l'emprise bâtie
        zone = parc.geom.buffer(70).envelope
        contexte = {"parcelle": parc, "voisines": geo.parcelles_dans(zone), "batiments": geo.batiments_dans(zone)}
        dossier = _dossier(adr, dossier_sortie, "plan_dwg")
    else:
        dossier = os.path.join(dossier_sortie, "PLANS_DWG", "plan_dwg")
        os.makedirs(dossier, exist_ok=True)

    places, rapport = [], []
    for chemin in pdfs:
        nom = os.path.splitext(os.path.basename(chemin))[0]
        say(f"Lecture de {nom}…")
        try:
            pl = plans.lire_pdf(chemin)
        except plans.PlanError as e:
            raise geo.DataError(str(e)) from e
        ech = echelle or pl.echelle
        if not ech:
            raise geo.DataError(f"{nom} : échelle non lue dans le PDF. Indiquez-la (50 pour 1/50, 100 pour 1/100…).")
        say(f"  {'plan scanné (vectorisation)' if pl.scanne else 'plan vectoriel'}, échelle 1/{ech} "
            f"({'imposée' if echelle else 'lue dans le PDF'}), {len(pl.traces)} tracés")
        calques = plans.classer(pl, ech)
        try:
            if cibles:
                essais = [(lib, plans.recaler(calques, g)) for lib, g in cibles]
                lib, rec = max(essais, key=lambda t: t[1].iou)
                say(f"  recalage sur : {lib}")
            else:
                rec = plans.recaler(calques, None)
        except plans.PlanError as e:
            raise geo.DataError(f"{nom} : {e}") from e
        for w in rec.avertissements:
            say("  ⚠ " + w)
        if cibles:
            say(f"  recalé : rotation {rec.angle_deg:.1f}°, dimensions ×{rec.echelle:.3f}, recouvrement {100 * rec.iou:.0f} %")
        places.append((nom, plans.appliquer(calques, rec), rec.angle_deg))
        rapport.append({"plan": nom, "echelle": ech, "scanne": pl.scanne, "rotation_deg": round(rec.angle_deg, 2),
                        "facteur_dimensions": round(rec.echelle, 4), "recouvrement": round(rec.iou, 3),
                        "recalage_fiable": rec.fiable, "avertissements": rec.avertissements,
                        "calques": {k: len(v) for k, v in calques.items()}})

    base = nom_dossier(adr.label) if adr else "PLANS"
    say("Écriture du DXF…")
    f_dxf = dwg.ecrire_dxf(os.path.join(dossier, f"fond_de_plan_{base}.dxf"), places, contexte)
    say("Aperçu du recalage…")
    f_png = os.path.join(dossier, f"apercu_recalage_{base}.png")
    cadastre.apercu(f_png, places, contexte)
    say("Conversion en DWG…")
    f_dwg = dwg.convertir_dwg(f_dxf)
    if f_dwg:
        say(f"DWG écrit : {os.path.basename(f_dwg)}")
    else:
        say("ODA File Converter introuvable : le DXF est livré (ouvrez-le dans AutoCAD / BricsCAD et enregistrez en DWG). "
            "Pour obtenir le DWG directement, installez l'ODA File Converter (gratuit).")
    export.ecrire_json({"plans": rapport, "adresse": adr.label if adr else None}, os.path.join(dossier, f"recalage_{base}.json"))
    say("Terminé.")
    return {"mode": "dwg", "adresse": {"label": adr.label if adr else "(sans adresse)"}, "dossier": dossier,
            "dxf": os.path.basename(f_dxf), "dwg": os.path.basename(f_dwg) if f_dwg else None,
            "apercu": os.path.basename(f_png), "plans": rapport, "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
