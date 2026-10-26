"""Façade niveau 2 : élévation photographique (Panoramax en base prioritaire, vos photos en complément) et ouvertures détectées.

1. Panoramax : images voyant la façade, projetées géométriquement sur son plan (position, azimut, tangage estimé) → élévation à l'échelle.
2. L'image de meilleure couverture sert de base ; les autres images Panoramax sont recalées sur elle (points d'intérêt) et ne comblent que
   les zones non vues. Vos photos (dossier facultatif) sont redressées puis recalées de la même façon.
3. Détection des baies / balcons sur la zone de façade comprise entre le sol et la ligne d'égout du nuage LiDAR.
"""
import os
import re

import cv2
import numpy as np

from . import facades, panoramax, photos, rythme

EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def photos_du_dossier(dossier):
    """Fichiers image d'un dossier (non récursif), triés par nom."""
    if not dossier or not os.path.isdir(dossier):
        return []
    return sorted(os.path.join(dossier, n) for n in os.listdir(dossier) if n.lower().endswith(EXT))


def _zone_facade(f, ech, z_max, forme):
    """Masque de la façade (entre sol et égout, par colonne) et lignes haut/bas en pixels."""
    h, w = forme
    r = f.r
    t = (np.arange(w) + 0.5) / ech
    haut = z_max - np.interp(t, r["abscisses"], r["egout"])
    bas = z_max - np.interp(t, r["abscisses"], r["sol"])
    zh, zb = haut * ech, bas * ech
    lignes = np.arange(h)[:, None]
    return (lignes >= zh[None, :]) & (lignes <= zb[None, :]), zh, zb


def _lire(chemin):
    img = cv2.imdecode(np.fromfile(chemin, np.uint8), cv2.IMREAD_COLOR)    # gère les chemins Windows accentués
    return img


def construire_elevation(f, say, photos_dir=None, ech=50.0, chercher=panoramax.chercher, telecharger=panoramax.telecharger,
                         dossier_cache=None, avec_panoramax=True):
    """(élévation BGR, masque, méta, sources, avertissements) ou None si aucune base."""
    r = f.r
    z_min, z_max = r["sol_med"] - 0.6, r["egout_med"] + 0.4
    avert, sources, cands = [], [], []
    if avec_panoramax:
        say("Panoramax : recherche des photos de rue…")
        cx = f.origine[0] + f.droite[0] * f.longueur / 2
        cy = f.origine[1] + f.droite[1] * f.longueur / 2
        try:
            trouvees = chercher(cx, cy)
        except Exception as e:                                           # noqa: BLE001
            trouvees = []
            avert.append(f"Panoramax indisponible : {e}")
        choisies = panoramax.selectionner(trouvees, f, n=4) if trouvees else []
        say(f"Panoramax : {len(trouvees)} image(s) à proximité, {len(choisies)} retenue(s) pour cette façade")
        for p in choisies:
            try:
                img = telecharger(p)
                el, ok, meta = photos.projeter(img, p, f, z_min, z_max, ech, sol=r["sol_med"])
                if dossier_cache:
                    cv2.imencode(".jpg", img)[1].tofile(os.path.join(dossier_cache, f"panoramax_{re.sub(r'[^A-Za-z0-9]+', '', p.id)[:24]}.jpg"))
                zone, _, _ = _zone_facade(f, ech, z_max, ok.shape)
                couv = float((ok & zone).sum()) / max(float(zone.sum()), 1.0)
                cands.append((couv, p, el, ok, meta["incidence"]))
            except Exception as e:                                       # noqa: BLE001
                avert.append(f"Image Panoramax {p.id[:8]} ignorée : {e}")
    if not cands:
        avert.append("Aucune photo Panoramax exploitable pour cette façade : élévation photographique non produite.")
        return None, avert
    cands.sort(key=lambda c: -c[0])
    couv, p0, base, mask, inc0 = cands[0]
    sources.append(f"Panoramax {p0.id[:8]} {p0.date} ({'360°' if p0.equirect else 'plate'}, couverture {100 * couv:.0f} %)")
    elements = [(base, mask, 2.0 * np.maximum(inc0, 0.05) ** 2)]
    for couv2, p, el, ok, inc in cands[1:]:
        M, n = photos.aligner(base, el, mask, min_inliers=25, echelle_ok=(0.9, 1.1))
        if M is None or abs(M[0, 2]) > 1.5 * ech or abs(M[1, 2]) > 1.5 * ech:
            avert.append(f"Image Panoramax {p.id[:8]} non recalée sur la base ({n} points concordants) : écartée")
            continue
        e2, m2 = photos.placer(el, M, base.shape[:2])
        i2 = cv2.warpAffine(inc, M, (base.shape[1], base.shape[0]))
        elements.append((e2, m2, np.maximum(i2, 0.05) ** 2))
        sources.append(f"Panoramax {p.id[:8]} {p.date} (recalée, {n} points)")
    for chemin in photos_du_dossier(photos_dir):
        try:
            img = _lire(chemin)
            if img is None:
                continue
            rect, info = photos.redresser_complet(img)
            M, n = photos.aligner(base, rect, mask, min_inliers=20)
            if M is None:
                avert.append(f"Photo {os.path.basename(chemin)} : pas assez de points communs avec la base ({n}) : écartée")
                continue
            e2, m2 = photos.placer(rect, M, base.shape[:2])
            elements.append((e2, m2, np.full(base.shape[:2], 0.35, np.float32)))
            sources.append(f"{os.path.basename(chemin)} (redressée et recalée, {n} points)")
        except Exception as e:                                           # noqa: BLE001
            avert.append(f"Photo {os.path.basename(chemin)} ignorée : {e}")
    if len(elements) > 1:
        base, mask = photos.assembler_pondere(elements)
    meta = {"ech": ech, "z_max": z_max, "z_min": z_min}
    return (base, mask, meta, sources), avert


def _dessiner(base, res, f, ech, z_max, chemin):
    """Aperçu de validation : élévation photographique + planchers, baies, balcons et RDC."""
    img = base.copy()
    ep = max(2, int(ech / 22))

    def rect(o, col, t):
        cv2.rectangle(img, (int(o["x"] * ech), int((z_max - o["z_bas"] - o["hauteur"]) * ech)),
                      (int((o["x"] + o["largeur"]) * ech), int((z_max - o["z_bas"]) * ech)), col, t)
    for nv in res["niveaux"]:
        y = int((z_max - nv["z0"]) * ech)
        cv2.line(img, (0, y), (img.shape[1], y), (255, 120, 0), ep)
        cv2.putText(img, nv["nom"], (8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, ech / 40, (255, 120, 0), ep)
    y = int((z_max - f.r["egout_med"]) * ech)
    cv2.line(img, (0, y), (img.shape[1], y), (255, 120, 0), ep)
    for o in res["baies"]:
        rect(o, (0, 140, 255), ep)
    for o in res["balcons"]:
        rect(o, (200, 0, 200), ep + 1)
    for o in res["rdc"]:
        rect(o, (0, 0, 230), ep + 1)
    cv2.imencode(".png", img)[1].tofile(chemin)


def traiter(f, numero, dossier, say=None, photos_dir=None, ech=50.0, avec_panoramax=True, n_visibles=None, **kw):
    """Forme générale d'une façade d'après les photos : niveaux (saisis ou estimés), rythme des baies, balcons, RDC à part.
    Renvoie un dict (jamais d'exception)."""
    say = say or (lambda s: None)
    nom = f"facade_{numero}_{re.sub(r'[^A-Za-z0-9]+', '_', f.voie)}"
    res = {"photos": [], "avertissements": [], "ouvertures": []}
    if "egout" not in f.r:
        res["avertissements"].append("Pas de ligne d'égout LiDAR : élévation photographique impossible.")
        return res
    cache = os.path.join(dossier, "photos")
    os.makedirs(cache, exist_ok=True)
    try:
        out, avert = construire_elevation(f, say, photos_dir, ech, dossier_cache=cache, avec_panoramax=avec_panoramax, **kw)
    except Exception as e:                                               # noqa: BLE001
        res["avertissements"].append(f"Élévation photographique en échec : {e}")
        return res
    res["avertissements"] += avert
    if out is None:
        return res
    base, mask, meta, sources = out
    res["photos"] = sources
    zone, zh, zb = _zone_facade(f, ech, meta["z_max"], base.shape[:2])
    valide = zone & mask
    cv2.imencode(".jpg", base, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tofile(os.path.join(dossier, f"{nom}_elevation.jpg"))
    res["elevation"] = f"{nom}_elevation.jpg"
    if valide.sum() < 0.1 * zone.sum():
        res["avertissements"].append("Moins de 10 % de la façade est vue sur les photos : forme générale non produite.")
        return res
    z_max, sol = meta["z_max"], f.r["sol_med"]
    n = n_visibles if n_visibles else max(1, f.r.get("niveaux_n", 2) - 1)
    h_rdc = rythme.estimer_h_rdc(base, valide, ech, z_max, sol)
    res["rdc_source"] = "photos" if h_rdc else "estimé"
    facades.definir_niveaux(f, n, h_rdc)
    d = rythme.caler_planchers(base, valide, ech, z_max, f.r["niveaux"])
    if d:
        f.r["niveaux"] = [z + d for z in f.r["niveaux"]]
        f.r["h_rdc"] += d
    res["niveaux_source"] = "saisi" if n_visibles else "estimé"
    f.r["niveaux_source"] = res["niveaux_source"]
    forme = rythme.analyser(base, valide, ech, z_max, sol, f.r["niveaux"], f.r["egout_med"])
    res.update({k: forme[k] for k in ("axes", "niveaux", "baies", "balcons", "rdc", "fenetre")})
    res["ouvertures"] = forme["baies"] + forme["balcons"] + forme["rdc"]
    _dessiner(base, forme, f, ech, z_max, os.path.join(dossier, f"{nom}_ouvertures.png"))
    res["apercu"] = f"{nom}_ouvertures.png"
    f.r["ouvertures"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in o.items()} for o in res["ouvertures"]]
    f.r["axes"] = forme["axes"]
    say(f"Façade {f.voie} : {len(forme['axes'])} axe(s) de baies, {len(forme['baies'])} baie(s), {len(forme['balcons'])} balcon(s), "
        f"{len(forme['rdc'])} ouverture(s) de RDC ; RDC {f.r['h_rdc']:.2f} m + {n} niveaux de {f.r['h_niveau']:.2f} m")
    return res
