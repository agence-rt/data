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


# --------------------------------------------------------------------------------------------
# Projection d'une photo à pose connue (Panoramax) sur le plan de la façade → élévation à l'échelle
def _grille(f, z_min, z_max, ech):
    W = max(1, int(math.ceil(f.longueur * ech)))
    H = max(1, int(math.ceil((z_max - z_min) * ech)))
    t = (np.arange(W) + 0.5) / ech
    z = z_max - (np.arange(H) + 0.5) / ech
    T, Z = np.meshgrid(t, z)
    X = f.origine[0] + T * f.droite[0]
    Y = f.origine[1] + T * f.droite[1]
    return X, Y, Z, W, H


def tangage_et_focale(img, fov=None):
    """(tangage °, focale px) d'une photo plate : focale d'après le champ de vision (défaut : équivalent 26 mm), tangage d'après
    le point de fuite vertical (appareil levé > 0)."""
    h, w = img.shape[:2]
    f = (max(w, h) / 2) / math.tan(math.radians(fov) / 2) if fov else 26.0 / 36.0 * max(w, h)
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = 1600 / max(h, w)
    petit = cv2.resize(gris, None, fx=k, fy=k) if k < 1 else gris
    segs = _segments(petit, 40) / (k if k < 1 else 1.0)
    vp, conf = point_de_fuite_vertical(segs, w, h)
    if vp is None or conf < 0.6:
        return 0.0, f
    dy = h / 2 - vp[1]
    tg = math.degrees(math.atan2(f, dy)) if dy > 0 else 0.0
    return float(np.clip(tg, -5, 60)), f


def projeter(img, photo, f, z_min, z_max, ech=50.0, hauteur_appareil=2.3, sol=0.0):
    """Élévation (BGR) de la façade vue par `photo` : lignes = altitudes z_max → z_min, colonnes = abscisse 0 → longueur, `ech` px/m.
    Renvoie (élévation, masque des pixels renseignés, méta)."""
    X, Y, Z, W, H = _grille(f, z_min, z_max, ech)
    dx, dy, dz = X - photo.x, Y - photo.y, Z - (sol + hauteur_appareil)
    ih, iw = img.shape[:2]
    cap_facade = math.degrees(math.atan2(f.origine[0] + f.droite[0] * f.longueur / 2 - photo.x,
                                         f.origine[1] + f.droite[1] * f.longueur / 2 - photo.y)) % 360
    meta = {"equirect": photo.equirect}
    graz = -(dx * f.sortant[0] + dy * f.sortant[1]) / (np.sqrt(dx * dx + dy * dy + dz * dz) + 1e-9)      # cosinus d'incidence
    if photo.equirect:
        az = photo.azimut if photo.azimut is not None else 0.0
        brg = np.degrees(np.arctan2(dx, dy))
        lat = np.degrees(np.arctan2(dz, np.hypot(dx, dy)))
        mx = (((brg - az) / 360.0 + 0.5) % 1.0) * iw
        my = (0.5 - lat / 180.0) * ih
        ok = graz > 0.2
    else:
        yaw = math.radians(photo.azimut if photo.azimut is not None else cap_facade)
        tang, foc = tangage_et_focale(img, photo.fov)
        th = math.radians(tang)
        fwd = np.array([math.cos(th) * math.sin(yaw), math.cos(th) * math.cos(yaw), math.sin(th)])
        right = np.array([math.cos(yaw), -math.sin(yaw), 0.0])
        up = np.array([-math.sin(th) * math.sin(yaw), -math.sin(th) * math.cos(yaw), math.cos(th)])
        zc = dx * fwd[0] + dy * fwd[1] + dz * fwd[2]
        xc = dx * right[0] + dy * right[1]
        yc = dx * up[0] + dy * up[1] + dz * up[2]
        zc_s = np.where(zc > 0.5, zc, 1.0)
        mx = iw / 2 + foc * xc / zc_s
        my = ih / 2 - foc * yc / zc_s
        ok = (zc > 0.5) & (mx >= 0) & (mx < iw) & (my >= 0) & (my < ih) & (graz > 0.2)
        meta.update(tangage=tang, focale=foc)
    out = cv2.remap(img, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_WRAP if photo.equirect else cv2.BORDER_CONSTANT)
    out[~ok] = 255
    meta.update(ech=ech, z_max=z_max, z_min=z_min, largeur_px=W, hauteur_px=H)
    return out, ok, meta


# --------------------------------------------------------------------------------------------
# Recalage d'une photo sans pose (vos photos) sur une élévation de base, puis assemblage
def aligner(base, img, masque_base=None, min_inliers=20, echelle_ok=(0.25, 4.0)):
    """Transformation affine 2x3 (img → base) par points d'intérêt SIFT + RANSAC ; (M, nb_inliers) ou (None, nb)."""
    g1 = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m1 = None if masque_base is None else (masque_base.astype(np.uint8) * 255)
    sift = cv2.SIFT_create(4000)
    k1, d1 = sift.detectAndCompute(g1, m1)
    k2, d2 = sift.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10:
        return None, 0
    bons = []
    for m, n in cv2.BFMatcher().knnMatch(d2, d1, k=2):
        if m.distance < 0.75 * n.distance:
            bons.append(m)
    if len(bons) < min_inliers:
        return None, len(bons)
    a = np.float32([k2[m.queryIdx].pt for m in bons])
    b = np.float32([k1[m.trainIdx].pt for m in bons])
    M, inl = cv2.estimateAffine2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=6.0, maxIters=5000)
    n = int(inl.sum()) if inl is not None else 0
    if M is None or n < min_inliers:
        return None, n
    s = math.sqrt(abs(np.linalg.det(M[:, :2])))
    if not echelle_ok[0] <= s <= echelle_ok[1]:
        return None, n
    return M, n


def assembler(base, masque_base, ajouts):
    """Complète `base` (priorité absolue) avec des images déjà dans son repère : [(image, masque), …] par ordre de priorité."""
    out = base.copy()
    m = masque_base.copy()
    for img, mk in ajouts:
        nouveau = mk & ~m
        out[nouveau] = img[nouveau]
        m |= mk
    return out, m


def placer(img, M, forme):
    """Image + masque warpés dans le repère de base (forme = (H, W))."""
    h, w = forme
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    mk = cv2.warpAffine(np.full(img.shape[:2], 255, np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
    return out, mk
