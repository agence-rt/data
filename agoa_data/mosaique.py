"""Assemblage de photos de rue Panoramax le long d'une façade (RDC + premiers étages).

Principe : on se place à la limite gauche de la parcelle, on prend la vue 360° la plus proche, on avance de quelques mètres,
on reprend la vue suivante, jusqu'à la limite droite. Chaque vue 360° est ramenée par rotation pure à une vue frontale
(`vues.vue_frontale`), mise à l'échelle métrique nominale (distance caméra-façade), puis recalée sur ses voisines (SIFT)
et fusionnée. Le repère de sortie : x = abscisse le long de la façade (m, 0 = limite gauche), y = hauteur (m, 0 = sol).
"""
import json
import math
import os

import cv2
import numpy as np

from . import panoramax, photos, vues

H_CAMERA = 2.4        # hauteur de l'objectif au-dessus du sol (véhicule), m


def positions_pas(longueur, pas=3.0):
    n = max(1, int(math.ceil(longueur / pas)))
    return [longueur * i / n for i in range(n + 1)]


def _coords(p, f):
    v = (p.x - f.origine[0], p.y - f.origine[1])
    t = v[0] * f.droite[0] + v[1] * f.droite[1]
    d = v[0] * f.sortant[0] + v[1] * f.sortant[1]
    return t, d


def choisir_par_pas(cands, f, pas=3.0, d_min=3.0, d_max=22.0, tol=6.0):
    """Une vue 360° par pas : proche de l'abscisse visée, côté rue, distinctes. Renvoie [(t_pas, Photo, t_cam, dist)]."""
    ok = []
    for p in cands:
        if not p.equirect:
            continue
        t, d = _coords(p, f)
        if d_min <= d <= d_max and -tol <= t <= f.longueur + tol:
            ok.append((p, t, d))
    out, pris = [], set()
    for ti in positions_pas(f.longueur, pas):
        mieux, note = None, 1e9
        for p, t, d in ok:
            s = abs(t - ti) + 0.15 * abs(d - 8) + (50 if p.id in pris else 0)
            if s < note:
                mieux, note = (p, t, d), s
        if mieux and mieux[0].id not in pris:
            pris.add(mieux[0].id)
            out.append((ti, *mieux))
    out.sort(key=lambda e: e[2])
    return out


def _bbox_candidats(f, marge=25.0, limite=200):
    cx = f.origine[0] + f.droite[0] * f.longueur / 2 + f.sortant[0] * 8
    cy = f.origine[1] + f.droite[1] * f.longueur / 2 + f.sortant[1] * 8
    derniere = None
    for dist, lim in (((1, int(math.ceil(f.longueur / 2 + marge))), limite), ((2, 40), 100), ((4, 45), 80), ((4, 30), 50)):
        try:
            r = panoramax.chercher(cx, cy, distance=dist, limite=lim)
            if r:
                return r
        except Exception as ex:                  # requête refusée : on retente avec des paramètres plus simples
            derniere = ex
    if derniere:
        raise derniere
    return []



def _vue_metrique(p, pano, f, t, d, ech, z_haut, taille=(1400, 1400), fov=100.0):
    """Vue frontale + placement nominal dans le repère (ech px/m) : renvoie (image, masque, (x0, y0), info)."""
    nrm = (-f.sortant[0], -f.sortant[1])
    vue, fl, info = vues.vue_frontale(pano, p.azimut or 0.0, nrm, fov=fov, taille=taille, affiner=True)
    k = ech * d / fl                                   # px/m = fl/d  →  facteur pour passer à ech px/m
    w, h = int(vue.shape[1] * k), int(vue.shape[0] * k)
    img = cv2.resize(vue, (w, h), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    mk = np.full(img.shape[:2], 255, np.uint8)
    cx, cy = vue.shape[1] / 2 * k, vue.shape[0] / 2 * k
    x0 = t * ech - cx
    y0 = (z_haut - H_CAMERA) * ech - cy
    info.update({"t_cam": round(t, 2), "dist": round(d, 2), "echelle_px_m": round(fl / d, 1)})
    return img, mk, (x0, y0), info


def assembler(f, ech=60, z_haut=9.0, pas=3.0, cands=None, telecharger=None, say=print, dossier=None, fov=100.0):
    """Mosaïque RDC + 2 étages. Renvoie (image BGR, masque, diagnostic dict)."""
    telecharger = telecharger or panoramax.telecharger
    if cands is None:
        cands = _bbox_candidats(f)
    pas_sel = choisir_par_pas(cands, f, pas)
    diag = {"facade_longueur": round(f.longueur, 2), "candidats": len(cands), "etapes": []}
    if not pas_sel:
        raise ValueError("Aucune vue 360° Panoramax exploitable devant cette façade")
    say(f"Panoramax : {len(cands)} images trouvées, {len(pas_sel)} retenues le long de la façade")
    W, H = int((f.longueur + 4) * ech), int((z_haut + 1.0) * ech)
    off_x = 2 * ech                                    # marge de 2 m de chaque côté
    base = np.full((H, W, 3), 255, np.uint8)
    mbase = np.zeros((H, W), bool)
    elements = []
    for i, (ti, p, t, d) in enumerate(pas_sel):
        e = {"pas": round(ti, 2), "id": p.id, "t_cam": round(t, 2), "dist": round(d, 2), "azimut": p.azimut}
        try:
            pano = telecharger(p)
            if dossier:
                os.makedirs(dossier, exist_ok=True)
                cv2.imwrite(os.path.join(dossier, f"pano_{i:02d}_{p.id[:8]}.jpg"), pano)
            if pano.shape[1] > 6000:                   # 12288 px inutiles : 4096 suffisent
                pano = cv2.resize(pano, (4096, 2048), interpolation=cv2.INTER_AREA)
            img, mk, (x0, y0), info = _vue_metrique(p, pano, f, t, d, ech, z_haut, fov=fov)
            e.update(info)
            if dossier:
                cv2.imwrite(os.path.join(dossier, f"vue_{i:02d}_{p.id[:8]}.jpg"), img)
            M = np.float32([[1, 0, x0 + off_x], [0, 1, y0]])
            if mbase.any():
                placee, mp = photos.placer(img, M, (H, W))
                rec, n = photos.aligner(base, placee, mbase & mp, min_inliers=15, echelle_ok=(0.8, 1.25))
                e["inliers"] = n
                if rec is not None and abs(rec[0, 2]) < 3 * ech and abs(rec[1, 2]) < 2 * ech:
                    M = np.vstack([rec, [0, 0, 1]]) @ np.vstack([M, [0, 0, 1]])
                    M = M[:2].astype(np.float32)
                    e["recalage"] = {"echelle": round(math.sqrt(abs(np.linalg.det(rec[:, :2]))), 3),
                                     "dx_m": round(float(rec[0, 2]) / ech, 2), "dy_m": round(float(rec[1, 2]) / ech, 2)}
                else:
                    e["recalage"] = "échec : placement nominal"
            placee, mp = photos.placer(img, M, (H, W))
            # poids : près du centre horizontal de la vue (moins de déformation) et hauteur utile
            cols = np.arange(W, dtype=np.float32)[None, :]
            cxv = float(M[0, 2] + img.shape[1] / 2 * M[0, 0])
            poids = np.exp(-((cols - cxv) / (3.0 * ech)) ** 2) * np.ones((H, 1), np.float32)
            elements.append((placee, mp, poids))
            base, mbase = photos.assembler_pondere(elements)
        except Exception as ex:                          # une vue défaillante n'arrête pas l'assemblage
            e["erreur"] = str(ex)
            say(f"  vue {p.id[:8]} ignorée : {ex}")
        diag["etapes"].append(e)
    if not mbase.any():
        raise ValueError("Aucune vue n'a pu être projetée")
    # rognage : du sol (y=0) à z_haut, de la limite gauche à droite
    y_sol = int(round(z_haut * ech + 0.0)) + int(0.0)
    res = base[:, off_x:off_x + int(f.longueur * ech)]
    mres = mbase[:, off_x:off_x + int(f.longueur * ech)]
    res = res[: int(z_haut * ech)]
    mres = mres[: int(z_haut * ech)]
    diag["taille_px"] = [res.shape[1], res.shape[0]]
    diag["echelle_px_m"] = ech
    diag["couverture"] = round(float(mres.mean()), 3)
    return res, mres, diag


def ecrire(f, dossier, **kw):
    os.makedirs(dossier, exist_ok=True)
    img, mk, diag = assembler(f, dossier=os.path.join(dossier, "vues"), **kw)
    cv2.imwrite(os.path.join(dossier, "assemblage.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    with open(os.path.join(dossier, "assemblage_diagnostic.json"), "w", encoding="utf-8") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=1)
    return img, diag
