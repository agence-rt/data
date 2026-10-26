"""Vues frontales d'une façade à partir d'images 360° (équirectangulaires).

Une caméra virtuelle sténopé est orientée face à la façade (rotation pure du panorama : la position GPS de la prise de vue n'intervient
pas) ; son attitude est affinée sur les points de fuite (verticales et horizontales de la façade), ce qui corrige aussi le tangage et le
roulis résiduels. La vue obtenue est une similitude du plan de la façade : échelle uniforme = focale / distance.
"""
import math

import cv2
import numpy as np

from . import photos


def _axes_vers_monde(avant):
    """Axes caméra (droite, bas, avant) en repère monde (x est, y nord, z haut) pour un axe optique `avant`, horizon à niveau."""
    f = np.asarray(avant, float)
    f = f / np.linalg.norm(f)
    bas = np.array([0.0, 0.0, -1.0])
    droite = np.cross(bas, f)
    droite /= np.linalg.norm(droite)
    bas = np.cross(f, droite)
    return np.column_stack([droite, bas, f])


def _intrinseques(taille, fov_deg):
    w, h = taille
    f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
    return f, w / 2, h / 2


def rendre(pano, azimut, Rw, taille, f):
    """Vue sténopé de taille (w, h) et focale f (px) du panorama équirectangulaire `pano` (centre = azimut, en degrés depuis le nord),
    pour une caméra d'axes monde Rw (colonnes : droite, bas, avant)."""
    w, h = taille
    u, v = np.meshgrid(np.arange(w, dtype=np.float32) + 0.5, np.arange(h, dtype=np.float32) + 0.5)
    d = np.stack([(u - w / 2) / f, (v - h / 2) / f, np.ones_like(u)], -1)
    dm = d @ Rw.T
    brg = np.degrees(np.arctan2(dm[..., 0], dm[..., 1]))
    lat = np.degrees(np.arctan2(dm[..., 2], np.hypot(dm[..., 0], dm[..., 1])))
    ph, pw = pano.shape[:2]
    mx = ((((brg - azimut) / 360.0 + 0.5) % 1.0) * pw).astype(np.float32)
    my = ((0.5 - lat / 180.0) * ph).astype(np.float32)
    return cv2.remap(pano, mx, my, cv2.INTER_AREA if pw > 3 * w else cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def _vp_vers_direction(vp, f, cx, cy):
    d = np.array([(vp[0] - cx) / f, (vp[1] - cy) / f, 1.0])
    return d / np.linalg.norm(d)


def attitude_depuis_fuites(vue, f, seuil=0.55):
    """Rotation (3x3, repère caméra) qui met la caméra de face : verticales verticales (tangage, roulis) et horizontales de la façade
    parallèles au plan image (lacet). Renvoie (M, info) ; M = identité si les points de fuite sont peu fiables."""
    h, w = vue.shape[:2]
    cx, cy = w / 2, h / 2
    gris = cv2.cvtColor(vue, cv2.COLOR_BGR2GRAY)
    k = 1400 / max(w, h)
    petit = cv2.resize(gris, None, fx=k, fy=k) if k < 1 else gris
    kk = k if k < 1 else 1.0
    segs = photos._segments(petit, 35) / kk
    info = {"verticale": None, "horizontale": None}
    if len(segs) < 12:
        return np.eye(3), info
    vpv, cv = photos.point_de_fuite_vertical(segs, w, h)
    # horizontales : même estimation sur les segments transposés
    segs_t = segs[:, [1, 0, 3, 2]]
    vph, ch = photos.point_de_fuite_vertical(segs_t, h, w)
    if vph is not None:
        vph = (vph[1], vph[0])
    info["verticale"] = None if vpv is None else {"vp": [round(float(vpv[0]), 1), round(float(vpv[1]), 1)], "confiance": round(float(cv), 2)}
    info["horizontale"] = None if vph is None else {"vp": [round(float(vph[0]), 1), round(float(vph[1]), 1)], "confiance": round(float(ch), 2)}
    if vpv is None or cv < seuil:
        return np.eye(3), info
    haut = _vp_vers_direction(vpv, f, cx, cy)
    if haut[1] > 0:
        haut = -haut
    if vph is not None and ch >= seuil:
        r = _vp_vers_direction(vph, f, cx, cy)
        if r[0] < 0:
            r = -r
    else:
        r = np.cross(np.array([0.0, 0.0, 1.0]), -haut)        # sans horizontale fiable : on garde le lacet courant
        r = np.array([1.0, 0.0, 0.0]) if np.linalg.norm(r) < 1e-6 else r / np.linalg.norm(r)
    r = r - (r @ haut) * haut                                  # r ⟂ haut
    r /= np.linalg.norm(r)
    bas = -haut
    avant = np.cross(r, bas)
    M = np.column_stack([r, bas, avant])
    ang = math.degrees(math.acos(float(np.clip((np.trace(M) - 1) / 2, -1, 1))))
    info["rotation_deg"] = round(ang, 2)
    if ang > 25:                                               # correction aberrante : on ne l'applique pas
        info["rejetee"] = True
        return np.eye(3), info
    return M, info


def vue_frontale(pano, azimut, normale_vers_facade, fov=100.0, taille=(1600, 1600), affiner=True):
    """Vue frontale d'une façade. `normale_vers_facade` : vecteur horizontal (x est, y nord) de la caméra vers la façade.
    Renvoie (vue, f, info)."""
    f, cx, cy = _intrinseques(taille, fov)
    Rw0 = _axes_vers_monde([normale_vers_facade[0], normale_vers_facade[1], 0.0])
    vue = rendre(pano, azimut, Rw0, taille, f)
    info = {"fov": fov, "f": round(f, 1)}
    if not affiner:
        return vue, f, info
    M, inf = attitude_depuis_fuites(vue, f)
    info.update(inf)
    if not np.allclose(M, np.eye(3)):
        vue = rendre(pano, azimut, Rw0 @ M, taille, f)
        info["corrigee"] = True
    return vue, f, info
