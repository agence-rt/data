"""Photos de façade (niveau 2) : redressement de la perspective verticale et repérage des lignes de planchers.

Les photos prises depuis la rue, en levant l'appareil, font converger les verticales. On estime le point de fuite vertical
(RANSAC sur les segments quasi verticaux), puis on redresse par homographie pour que les verticales redeviennent verticales.
"""
import math

import cv2
import numpy as np


def _segments(gris, longueur_min):
    lsd = cv2.createLineSegmentDetector(0)
    s = lsd.detect(gris)[0]
    if s is None:
        return np.zeros((0, 4))
    s = s.reshape(-1, 4)
    L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
    return s[L >= longueur_min]


def point_de_fuite_vertical(segs, largeur, hauteur, iters=3000, seed=0):
    """Point de fuite (x, y) des segments quasi verticaux (RANSAC) ; None si insuffisant. Renvoie aussi le masque des segments retenus."""
    dx, dy = segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]
    ang = np.degrees(np.arctan2(np.abs(dx), np.abs(dy)))                  # 0 = vertical
    ok = ang < 25
    v = segs[ok]
    if len(v) < 8:
        return None, None
    P = np.column_stack([v[:, 0], v[:, 1], np.ones(len(v))])
    Q = np.column_stack([v[:, 2], v[:, 3], np.ones(len(v))])
    lignes = np.cross(P, Q)
    lignes /= np.linalg.norm(lignes[:, :2], axis=1, keepdims=True) + 1e-12
    poids = np.hypot(v[:, 2] - v[:, 0], v[:, 3] - v[:, 1])
    rng = np.random.default_rng(seed)
    meilleur, score = None, -1
    centres = (P + Q) / 2
    for _ in range(iters):
        i, j = rng.choice(len(v), 2, replace=False)
        p = np.cross(lignes[i], lignes[j])
        if abs(p[2]) < 1e-9:
            continue
        p = p / p[2]
        if abs(p[0]) > 40 * largeur or abs(p[1]) > 40 * hauteur:
            continue
        d = centres - p
        d = d[:, :2] / (np.linalg.norm(d[:, :2], axis=1, keepdims=True) + 1e-12)
        u = np.column_stack([v[:, 2] - v[:, 0], v[:, 3] - v[:, 1]])
        u = u / (np.linalg.norm(u, axis=1, keepdims=True) + 1e-12)
        s = float((poids * (np.abs((d * u).sum(1)) > math.cos(math.radians(1.2)))).sum())
        if s > score:
            meilleur, score = p, s
    if meilleur is None:
        return None, None
    return (float(meilleur[0]), float(meilleur[1])), score / poids.sum()


def redresser(img):
    """Redresse la perspective verticale. Renvoie (image redressée, info) ; info = dict(angle, point_de_fuite, confiance)."""
    h, w = img.shape[:2]
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = 1600 / max(h, w)
    petit = cv2.resize(gris, None, fx=k, fy=k) if k < 1 else gris
    f = k if k < 1 else 1.0
    segs = _segments(petit, 40) / f
    vp, conf = point_de_fuite_vertical(segs, w, h)
    if vp is None:
        return img, {"redresse": False}
    vx, vy = vp
    # les verticales convergent vers (vx, vy) : on envoie les bords haut et bas à droite et à gauche en rectangle
    yb, yt = h - 1.0, 0.0
    x0, x1 = 0.0, w - 1.0

    def sur_ligne(x, y):                                       # point de la ligne (x, yb) -> (vx, vy) à la hauteur y
        t = (y - yb) / (vy - yb)
        return x + (vx - x) * t
    src = np.float32([[sur_ligne(x0, yt), yt], [sur_ligne(x1, yt), yt], [x1, yb], [x0, yb]])
    xs = src[:, 0]
    largeur = float(xs.max() - xs.min())
    dst = np.float32([[0, 0], [largeur, 0], [largeur, h - 1], [0, h - 1]])
    # coin bas gauche/droit : on garde l'écartement du bas
    dst = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    H = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(img, H, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return out, {"redresse": True, "point_de_fuite": (vx, vy), "confiance": conf}


def redresser_complet(img):
    """Redresse verticales puis horizontales (élévation à plat, proportions à recaler ensuite sur les cotes connues)."""
    a, i1 = redresser(img)
    t, i2 = redresser(cv2.transpose(a))
    return cv2.transpose(t), {"verticales": i1, "horizontales": i2}
