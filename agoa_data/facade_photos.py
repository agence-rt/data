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

from . import ouvertures as ov
from . import panoramax, photos

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
                cands.append((couv, p, el, ok))
            except Exception as e:                                       # noqa: BLE001
                avert.append(f"Image Panoramax {p.id[:8]} ignorée : {e}")
    if not cands:
        avert.append("Aucune photo Panoramax exploitable pour cette façade : élévation photographique non produite.")
        return None, avert
    cands.sort(key=lambda c: -c[0])
    couv, p0, base, mask = cands[0]
    sources.append(f"Panoramax {p0.id[:8]} {p0.date} ({'360°' if p0.equirect else 'plate'}, couverture {100 * couv:.0f} %)")
    for couv2, p, el, ok in cands[1:]:
        M, n = photos.aligner(base, el, mask, min_inliers=25, echelle_ok=(0.9, 1.1))
        if M is None or abs(M[0, 2]) > 1.5 * ech or abs(M[1, 2]) > 1.5 * ech:
            avert.append(f"Image Panoramax {p.id[:8]} non recalée sur la base ({n} points concordants) : écartée")
            continue
        e2, m2 = photos.placer(el, M, base.shape[:2])
        base, mask = photos.assembler(base, mask, [(e2, m2)])
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
            base, mask = photos.assembler(base, mask, [(e2, m2)])
            sources.append(f"{os.path.basename(chemin)} (redressée et recalée, {n} points)")
        except Exception as e:                                           # noqa: BLE001
            avert.append(f"Photo {os.path.basename(chemin)} ignorée : {e}")
    meta = {"ech": ech, "z_max": z_max, "z_min": z_min}
    return (base, mask, meta, sources), avert


def traiter(f, numero, dossier, say=None, photos_dir=None, ech=50.0, avec_panoramax=True, **kw):
    """Produit l'élévation photographique et les ouvertures d'une façade. Renvoie un dict (jamais d'exception)."""
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
        res["avertissements"].append("Moins de 10 % de la façade est vue sur les photos : ouvertures non détectées.")
        return res
    det = ov.aligner_sur_trames(ov.detecter(base, valide, ech, zh, zb), ech)
    ov.dessiner(base, det, os.path.join(dossier, f"{nom}_ouvertures.png"), ech)
    res["apercu"] = f"{nom}_ouvertures.png"
    for o in det:
        res["ouvertures"].append({"type": o["type"], "x": round(o["x0"] / ech, 2), "largeur": round((o["x1"] - o["x0"]) / ech, 2),
                                  "z_bas": round(meta["z_max"] - o["y1"] / ech, 2), "hauteur": round((o["y1"] - o["y0"]) / ech, 2)})
    f.r["ouvertures"] = res["ouvertures"]
    say(f"Façade {f.voie} : {sum(o['type'] in ('baie', 'fenetre') for o in det)} baie(s), {sum(o['type'] == 'balcon' for o in det)} balcon(s) détecté(s)")
    return res
