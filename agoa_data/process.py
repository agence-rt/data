"""Process « Façade → DWG » en 6 étapes (bouton 4).

1. adresse → 2. cadastre : rue choisie, largeur de façade → 3. nuage de points : coupe perpendiculaire à la rue (hauteur, pente de toit)
→ 4. photos libres de la rue (Panoramax) cadrant le RDC et R+1, compilées et redressées à la largeur de la façade
→ 5. axes et trames de baies des étages courants (le RDC est étudié à part) → 6. fichier DXF / DWG.
Un sous-dossier par façade ; chaque étape écrit ses fichiers numérotés (1_…, 2_…, … 6_…) et un journal."""
import datetime
import json
import math
import os
import re
import traceback
import unicodedata

import cv2
import numpy as np
from shapely.geometry import LineString

from . import APP_VERSION
from . import baies as baies_mod, dwg, export, facades, geo, lidar, livraison, mosaique, panoramax, vue_unique, volumetrie
from . import coupes as cp
from .pipeline import nom_dossier

Z_HAUT = 9.5            # m : hauteur de l'élévation photographique (RDC + R+1 et le bas de R+2)
COUVERTURE_MIN = 0.97   # part de la largeur de façade couverte avant de chercher d'autres vues


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()).strip()


def preparer(adresse, progress=None):
    """Étapes 1 et 2 sans le nuage : adresse, parcelle, bâtiments, voies et une façade par rue riveraine (largeur)."""
    say = progress or (lambda s: None)
    say("1 · Adresse…")
    adr = adresse if isinstance(adresse, geo.Adresse) else geo.geocoder(adresse)
    say(f"Adresse : {adr.label}")
    say("2 · Cadastre : parcelle, bâtiments, rues…")
    parc = geo.parcelle_au_point(adr)
    bats = geo.batiments_de_la_parcelle(parc)
    if not bats:
        raise geo.DataError("Aucun bâtiment BD TOPO sur cette parcelle.")
    voies = geo.voies_du_batiment(bats[0])
    fac = facades.definir_facades(bats[0].geom, parc.geom, voies)
    if not fac:
        raise geo.DataError("Aucune façade ne donne sur une rue identifiée.")
    return {"adr": adr, "parc": parc, "bats": bats, "voies": voies, "facades": fac}


def libelle_facade(f):
    s = f"{f.voie} — façade de {f.longueur:.2f} m".replace(".", ",")
    if f.front_parcelle:
        s += f" (front de parcelle {f.front_parcelle:.2f} m)".replace(".", ",")
    return s


def choisir_facade(fac, voie=None):
    if voie:
        for f in fac:
            if _norm(f.voie) == _norm(voie):
                return f
        raise geo.DataError(f"Aucune façade sur « {voie} ». Rues disponibles : " + ", ".join(f.voie for f in fac))
    return max(fac, key=lambda f: f.longueur)


# ---------------------------------------------------------------- étape 4 : photos compilées

def _couverture(mk, ech, z0=0.5, z1=7.5):
    H = mk.shape[0]
    band = mk[max(int(H - z1 * ech), 0):int(H - z0 * ech)]
    return band.mean(axis=0) > 0.9


def _recaler(img, mk, img2, mk2, ech, max_dec=0.8):
    """Décalage (px) à appliquer à la vue 2 pour la caler sur la base dans leur recouvrement (corrélation de phase), borné à ±max_dec m."""
    ov = mk & mk2
    ys, xs = np.where(ov)
    if len(xs) < 0.6 * ech * 0.6 * ech or xs.max() - xs.min() < 0.6 * ech:
        return 0.0, 0.0
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    a = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(img2[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    w = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), rep = cv2.phaseCorrelate(a, b, w)
    lim = max_dec * ech
    if rep < 0.1 or abs(dx) > lim or abs(dy) > lim:
        return 0.0, 0.0
    return -dx, -dy


def compiler_vues(f, cands, ch, telecharger, ech, z_haut, say, dossier=None, rendre=None, max_extra=3):
    """Vue la plus centrée redressée à l'échelle de la façade ; si elle ne couvre pas toute la largeur, les vues voisines ne comblent
    QUE les colonnes manquantes (pas de moyenne : pas de doublons), après un recalage fin sur le recouvrement. Renvoie (img, mk, infos)."""
    rendre = rendre or vue_unique.vue_redressee
    p, t, d = ch
    pano = telecharger(p)
    if dossier:
        cv2.imwrite(os.path.join(dossier, "4_pano_origine.jpg"), pano)
    if pano.shape[1] > 6000:
        pano = cv2.resize(pano, (6144, 3072), interpolation=cv2.INTER_AREA)
    img, mk, info = rendre(pano, p, f, t, d, ech=ech, z_haut=z_haut)
    vues = [{"id": p.id, "producteur": getattr(p, "producteur", ""), "date": getattr(p, "date", ""), "t_m": round(t, 2), "dist_m": round(d, 2), "role": "vue centrale",
             "rotation": info}]
    couv = _couverture(mk, ech)
    essais = 0
    deja = {p.id}
    while couv.mean() < COUVERTURE_MIN and essais < max_extra:
        manque = ~couv
        runs, i = [], 0
        while i < len(manque):
            if manque[i]:
                j = i
                while j < len(manque) and manque[j]:
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        a, b = max(runs, key=lambda r: r[1] - r[0])
        centre = (a + b) / 2 / ech
        meil = None
        for q in cands:
            if q.id in deja or not q.equirect:
                continue
            t2, d2 = mosaique._coords(q, f)
            if not 4.0 <= d2 <= 16.0:
                continue
            sc = abs(t2 - centre) + 0.2 * abs(d2 - d)
            if meil is None or sc < meil[0]:
                meil = (sc, q, t2, d2)
        if meil is None:
            break
        _, q, t2, d2 = meil
        deja.add(q.id)
        essais += 1
        try:
            pano2 = telecharger(q)
            if pano2.shape[1] > 6000:
                pano2 = cv2.resize(pano2, (6144, 3072), interpolation=cv2.INTER_AREA)
            img2, mk2, info2 = rendre(pano2, q, f, t2, d2, ech=ech, z_haut=z_haut)
        except Exception as ex:                     # une vue complémentaire manquante ne bloque pas
            say(f"  vue complémentaire {q.id[:8]} inutilisable : {ex}")
            continue
        dx, dy = _recaler(img, mk, img2, mk2, ech)
        if dx or dy:
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            img2 = cv2.warpAffine(img2, M, (img2.shape[1], img2.shape[0]), borderValue=(255, 255, 255))
            mk2 = cv2.warpAffine(mk2.astype(np.uint8), M, (mk2.shape[1], mk2.shape[0]), flags=cv2.INTER_NEAREST) > 0
        neuf = mk2 & ~mk
        img[neuf] = img2[neuf]
        mk = mk | mk2
        couv = _couverture(mk, ech)
        vues.append({"id": q.id, "producteur": getattr(q, "producteur", ""), "date": getattr(q, "date", ""), "t_m": round(t2, 2), "dist_m": round(d2, 2),
                     "role": "complément", "recalage_m": [round(dx / ech, 2), round(dy / ech, 2)]})
        say(f"  vue complémentaire {q.id[:8]} à {t2:.1f} m : couverture {100 * couv.mean():.0f} %")
    return img, mk, {"vues": vues, "couverture_largeur": round(float(couv.mean()), 3)}


# ---------------------------------------------------------------- orchestrateur

def facade_dwg(adresse, dossier_sortie, voie=None, niveaux=None, progress=None, cache=None, cands=None, telecharger=None, ech=80, prep=None):
    """Exécute les 6 étapes pour une rue ; écrit `<dossier>/<NOM>/facade_dwg/<RUE>/` ; renvoie le dict de résultats."""
    say0 = progress or (lambda s: None)
    lignes = [f"AGOA DATA {APP_VERSION} - process facade vers DWG"]

    def say(m):
        lignes.append(str(m))
        say0(m)

    prep = prep or preparer(adresse, say)
    adr, parc, bats, voies, fac = prep["adr"], prep["parc"], prep["bats"], prep["voies"], prep["facades"]
    f = choisir_facade(fac, voie)
    if len(fac) > 1 and not voie:
        say(f"Plusieurs rues : « {f.voie} » retenue (façade la plus longue) ; autres : " + ", ".join(x.voie for x in fac if x is not f))
    nom = nom_dossier(adr.label)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f.voie).strip("_")
    dossier = os.path.join(dossier_sortie, nom, "facade_dwg", slug)
    os.makedirs(dossier, exist_ok=True)
    hyp = []                                # hypothèses et limites, reprises dans le DWG et le rapport
    res = {"mode": "facade_dwg", "version": APP_VERSION, "adresse": {"label": adr.label, "x_l93": adr.x, "y_l93": adr.y},
           "voie": f.voie, "dossier": dossier, "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), "etapes": {}}
    export.ecrire_json({"adresse": adr.label, "x_l93": adr.x, "y_l93": adr.y, "lon": adr.lon, "lat": adr.lat}, os.path.join(dossier, "1_adresse.json"))

    # 2 · cadastre -----------------------------------------------------------------------------------------------------------------
    b0 = bats[0]
    zone = b0.geom
    for b in bats[1:]:
        zone = zone.union(b.geom)
    L = float(f.longueur)
    info2 = {"voie": f.voie, "largeur_facade_m": round(L, 2), "front_parcelle_m": None if f.front_parcelle is None else round(f.front_parcelle, 2),
             "parcelle": f"{parc.section} {parc.numero}", "contenance_m2": parc.contenance, "autres_rues": [x.voie for x in fac if x is not f]}
    contexte = {}
    try:
        z2 = parc.geom.buffer(45).envelope
        contexte = {"parcelles": geo.parcelles_dans(z2), "batiments": geo.batiments_dans(z2)}
    except Exception as ex:
        say(f"⚠ Cadastre alentour : {ex}")
    livraison.plan_cadastre_png(f, parc, bats, voies, contexte, os.path.join(dossier, "2_cadastre.png"), adr.label)
    export.ecrire_json(info2, os.path.join(dossier, "2_facade.json"))
    say(f"2 · Façade sur {f.voie} : largeur {L:.2f} m")
    res["etapes"]["2_cadastre"] = info2

    # 3 · nuage de points et coupe perpendiculaire -------------------------------------------------------------------------------
    say("3 · Nuage de points LiDAR HD…")
    dalles = geo.dalles_lidar(zone, marge=45)
    pts = lidar.points_sur_emprise(dalles, zone, marge=40, progress=say, cache_dir=cache)
    vols = []
    for b in bats:
        try:
            vols.append((b, volumetrie.calculer(pts, b.geom)))
        except ValueError:
            continue
    if not vols:
        raise geo.DataError("Les bâtiments de cette parcelle ne sont pas classés « bâtiment » dans le nuage.")
    b0, v0 = vols[0]
    sol_c = v0.altitude_sol
    facades.profils(pts, f, b0.geom, sol_c, b0.etages)
    ter = {}
    try:
        ter = facades.terrasse_attiques(pts, f, b0.geom)
    except Exception as ex:
        say(f"⚠ Terrasse / attiques : {ex}")
    sol_f = f.r.get("sol_med", sol_c)
    h_haut = ter.get("terrasse_dalle") if ter else None
    haut_source = "terrasse à l'alignement (dessus de dalle)"
    if h_haut is None:
        h_haut = f.r.get("egout_h")
        haut_source = "égout (toiture à l'alignement)"
    voie_obj = [v for v in voies if _norm(v.nom) == _norm(f.voie)] or voies[:1]
    liste = cp.definir_coupes(b0.geom, voie_obj, avec_parallele=False)
    coupe = None
    if liste:
        c = liste[0]
        q, z, cl = cp.analyser_coupe(pts, b0.geom, c, sol_c)
        try:
            cp.contexte(pts, b0.geom, c, c.r["sol"])
            pr = cp.profil(pts, b0.geom, c, c.r["sol"])
            if ter:
                pr = cp.corriger_terrasse(pr, ter["terrasse_dalle"], ter.get("recul"), ter.get("attique_haut"))
            coupe = {"c": c, "profil": pr, "r": c.r, "contexte": c.contexte}
            cp.tracer_coupe(c, q, z, cl, b0.geom, os.path.join(dossier, "3_coupe.png"))
        except Exception as ex:
            say(f"⚠ Coupe simplifiée : {ex}")
    info3 = {"sol_ngf": round(sol_f, 2), "egout_ngf": None if "egout_med" not in f.r else round(f.r["egout_med"], 2),
             "faitage_ngf": None if "faitage" not in f.r else round(f.r["faitage"], 2), "hauteur_facade_m": None if not h_haut else round(h_haut, 2),
             "hauteur_facade_source": haut_source, "terrasse": ter or None,
             "pente_toit_deg": None if not coupe else (round(coupe["r"].get("pente_deg"), 1) if coupe["r"].get("pente_deg") is not None else None),
             "voisins_coupe": 0 if not coupe else len(coupe["contexte"].get("voisins", []))}
    export.ecrire_json(info3, os.path.join(dossier, "3_coupe.json"))
    say(f"3 · Hauteur de façade {h_haut:.2f} m ({haut_source}), sol {sol_f:.2f} NGF" if h_haut else "3 · Hauteur de façade non lue")
    res["etapes"]["3_coupe"] = info3
    if not h_haut:
        raise geo.DataError("Hauteur de façade non lisible dans le nuage de points.")
    if niveaux:
        facades.definir_niveaux(f, int(niveaux))

    # 4 · photos Panoramax compilées et redressées à la largeur de la façade ---------------------------------------------------
    say("4 · Photos de rue libres de droit (Panoramax) : RDC et R+1…")
    photo, plat, mk2, elev, segs, niv = None, None, None, {}, [], {}
    try:
        telecharger = telecharger or panoramax.telecharger
        if cands is None:
            cands = mosaique._bbox_candidats(f)
        ch = vue_unique.choisir(cands, f)
        if ch is None:
            raise ValueError("aucune vue 360° exploitable devant cette façade")
        say(f"  {len(cands)} image(s) trouvée(s) ; vue centrale {ch[0].id[:8]} à {ch[1]:.1f} m du bord gauche, {ch[2]:.1f} m de la façade")
        img, mk, photo = compiler_vues(f, cands, ch, telecharger, ech, Z_HAUT, say, dossier)
        cv2.imwrite(os.path.join(dossier, "4_photo_redressee.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        export.ecrire_json(photo, os.path.join(dossier, "4_photo.json"))
        res["etapes"]["4_photos"] = photo
        # 5 · axes et trames ------------------------------------------------------------------------------------------------------
        say("5 · Axes de travées et niveaux courants…")
        plat, mk2, elev, segs, niv = vue_unique.analyser_elevation(img, mk, ech, L, h_haut=h_haut, n=niveaux, un_seul_linteau=True)
        cv2.imwrite(os.path.join(dossier, "facade_redressee.jpg"), plat, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if elev.get("avertissement"):
            say("⚠ " + elev["avertissement"])
    except Exception as ex:
        say(f"⚠ Photos / trame impossibles : {ex}")
        lignes.append(traceback.format_exc())
        res["etapes"]["4_photos"] = {"erreur": str(ex)}
    trame = livraison.construire_trame(L, h_haut, elev, segs, niv, f, hyp, photo is not None)
    export.ecrire_json(trame, os.path.join(dossier, "5_trame.json"))
    livraison.trame_png(trame, plat, ech, os.path.join(dossier, "5_trame.png"))
    res["etapes"]["5_trame"] = {k: trame[k] for k in ("n_travees", "n_niveaux", "axes_m", "largeurs_m", "pas_niveau_m", "rapports_h_l")}

    # 6 · DXF / DWG --------------------------------------------------------------------------------------------------------------
    say("6 · Fichier DXF / DWG…")
    plan = livraison.preparer_plan(f, parc, bats, voies, contexte)
    donnees = {"adresse": adr.label, "nom": nom, "voie": f.voie, "version": APP_VERSION, "date": res["date"], "L": L, "sol": sol_f,
               "trame": trame, "photo": None if photo is None else {"fichier": "facade_redressee.jpg", "z_haut": plat.shape[0] / ech if plat is not None else Z_HAUT,
                                                                    "vues": photo["vues"]},
               "coupe": coupe, "terrasse": ter, "plan": plan, "hypotheses": hyp, "egout": f.r.get("egout_med"), "faitage": f.r.get("faitage")}
    f_dxf = livraison.ecrire_dxf(donnees, os.path.join(dossier, f"6_{nom}_{slug}.dxf"))
    f_dwg = dwg.convertir_dwg(f_dxf)
    if f_dwg:
        say(f"DWG écrit : {os.path.basename(f_dwg)}")
    else:
        say("ODA File Converter introuvable : le DXF est livré (ouvrez-le dans AutoCAD / BricsCAD et enregistrez en DWG).")
    res.update({"dxf": os.path.basename(f_dxf), "dwg": os.path.basename(f_dwg) if f_dwg else None, "hypotheses": hyp})
    export.ecrire_json(res, os.path.join(dossier, "resultats.json"))
    with open(os.path.join(dossier, "journal.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lignes))
    say("Terminé.")
    return res
