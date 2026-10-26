"""Forme générale d'une façade à partir de son élévation photographique : niveaux, rythme des baies, balcons, RDC à part.

Ordre de travail (avant tout détail) :
1. les planchers sont donnés (niveaux saisis répartis entre RDC et égout), éventuellement recalés sur les lignes horizontales des photos ;
2. les axes de baies sont repérés sur l'ensemble des niveaux courants (le rythme) puis régularisés ;
3. chaque case (axe x niveau) porte une baie si elle apparaît sur les photos ; la hauteur et la position des baies dans le niveau sont
   les médianes observées (à affiner dans un second temps) ;
4. les balcons sont les éléments continus dans la bande de garde-corps de chaque niveau ;
5. le RDC est analysé indépendamment (grandes ouvertures sombres : porches, portes, vitrines).
"""
import cv2
import numpy as np

from . import ouvertures as ov


def _row(z, z_max, ech):
    return int(round((z_max - z) * ech))


def estimer_h_rdc(base, valide, ech, z_max, sol, h_min=2.4, h_max=5.2):
    """Hauteur du RDC d'après la plus forte ligne horizontale (bandeau, corniche de soubassement) entre h_min et h_max au-dessus du sol ; None si faible."""
    g = cv2.GaussianBlur(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY), (0, 0), 2).astype(np.float32)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    gy[~valide] = 0
    prof = gy.sum(axis=1) / np.maximum(valide.sum(axis=1), 1)
    a, b = _row(sol + h_max, z_max, ech), _row(sol + h_min, z_max, ech)
    a, b = max(a, 0), min(b, len(prof) - 1)
    if b - a < 5:
        return None
    seg = prof[a:b + 1]
    k = int(np.argmax(seg))
    if seg[k] < 2.0 * np.median(prof[valide.any(axis=1)]) or seg[k] < 4.0:
        return None
    return float(z_max - (a + k) / ech - sol)


def caler_planchers(base, valide, ech, z_max, planchers, tol=0.35):
    """Décalage uniforme (m) à appliquer aux planchers prévus pour les poser sur les lignes horizontales de la photo ; 0 si peu fiable."""
    g = cv2.GaussianBlur(cv2.cvtColor(base, cv2.COLOR_BGR2GRAY), (0, 0), 2).astype(np.float32)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    gy[~valide] = 0
    prof = gy.sum(axis=1) / np.maximum(valide.sum(axis=1), 1)
    ref = np.median(prof[valide.any(axis=1)]) + 1e-6
    ecarts = []
    for z in planchers:
        a, b = _row(z + tol, z_max, ech), _row(z - tol, z_max, ech)
        a, b = max(a, 0), min(b, len(prof) - 1)
        if b - a < 3:
            continue
        seg = prof[a:b + 1]
        k = int(np.argmax(seg))
        if seg[k] > 2.5 * ref:
            ecarts.append(z_max - (a + k) / ech - z)
    if len(ecarts) < max(3, int(0.6 * len(planchers))):
        return 0.0
    d = float(np.median(ecarts))
    return d if abs(d) <= tol else 0.0


def analyser(base, valide, ech, z_max, sol, planchers, z_top, h_rdc=None):
    """Forme générale. `planchers` : altitudes des planchers R+1 … R+n ; `z_top` : altitude de l'égout. Dimensions en mètres
    (abscisse le long de la façade ; z en NGF)."""
    h, w = base.shape[:2]
    n = len(planchers)
    bornes = list(planchers) + [z_top]
    z_rdc = planchers[0]
    out = {"axes": [], "niveaux": [], "baies": [], "balcons": [], "rdc": [], "fenetre": None}
    for k in range(n):
        out["niveaux"].append({"nom": f"R+{k + 1}", "z0": float(bornes[k]), "z1": float(bornes[k + 1])})
    hn = (z_top - z_rdc) / n
    # zone des niveaux courants
    haut = np.zeros((h, w), bool)
    r0, r1 = max(_row(z_top, z_max, ech), 0), min(_row(z_rdc, z_max, ech), h)
    haut[r0:r1] = True
    zone = haut & valide
    zone = cv2.erode(zone.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    if zone.sum() > 0.05 * haut.sum():
        m = (ov._masque_ecart(base, zone) & zone).astype(np.uint8)
        kk = max(3, int(0.05 * ech) | 1)
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((kk, kk), np.uint8)), cv2.MORPH_CLOSE, np.ones((kk, kk), np.uint8))
        mm = m > 0
        bandes = []
        for k in range(n):
            # vitrage seul : au-dessus de la bande de garde-corps (1,1 m) et sous la tête de baie ; sans garde-corps, tout le niveau
            ya, yb = _row(bornes[k] + 0.9 * hn, z_max, ech), _row(bornes[k] + min(1.15, 0.45 * hn), z_max, ech)
            bandes.append((max(ya, 0), min(yb, h)))
        cols_ok = zone.mean(axis=0) > 0.3
        occ = np.zeros(w)
        cnt = 0
        for ya, yb in bandes:
            if yb > ya:
                occ += mm[ya:yb].mean(axis=0)
                cnt += 1
        occ /= max(cnt, 1)
        occ[~cols_ok] = 0
        occ = np.convolve(occ, np.ones(5) / 5, mode="same")
        t = ov._otsu_borne(occ[cols_ok], 0.16, 0.5)
        axes = [a for a in ov._segments_1d(occ, t, int(0.45 * ech), int(0.45 * ech)) if (a[1] - a[0]) / ech <= 4.8]
        presents = []
        for (x0, x1) in axes:
            pr = []
            for ya, yb in bandes:
                pr.append(bool(yb > ya and mm[ya:yb, x0:x1].mean() >= 0.15))
            presents.append(pr)
        gardes = [(a, p) for a, p in zip(axes, presents) if sum(p) >= max(2, 0.4 * n)]
        # largeurs régularisées : mêmes largeurs pour les axes semblables (écart < 0.3 m), centres conservés
        larg = [(a[1] - a[0]) / ech for a, _ in gardes]
        reg = []
        for i, ((x0, x1), p) in enumerate(gardes):
            proches = [larg[j] for j in range(len(gardes)) if abs(larg[j] - larg[i]) < 0.3]
            wm = float(np.median(proches)) if len(proches) >= 2 else larg[i]
            c = (x0 + x1) / 2
            reg.append((int(round(c - wm * ech / 2)), int(round(c + wm * ech / 2)), p))
        # hauteur de baie et décalage dans le niveau : médianes observées
        hauteurs, offs = [], []
        for (x0, x1, p) in reg:
            for k in range(n):
                ya, yb = max(_row(bornes[k + 1], z_max, ech), 0), min(_row(bornes[k], z_max, ech), h)
                if not p[k] or yb <= ya:
                    continue
                lig = np.where(mm[ya:yb, x0:x1].mean(axis=1) > 0.25)[0]
                if len(lig) >= 5:
                    zb, zh = z_max - (ya + lig.max() + 1) / ech, z_max - (ya + lig.min()) / ech
                    hauteurs.append(zh - zb)
                    offs.append(zb - bornes[k])
        hf = float(np.clip(np.median(hauteurs), 0.5 * hn, 0.92 * hn)) if len(hauteurs) >= 3 else 0.78 * hn
        of = float(np.clip(np.median(offs), -0.05 * hn, 0.3 * hn)) if len(offs) >= 3 else 0.0
        out["fenetre"] = {"hauteur": hf, "decalage": of, "source": "photos" if len(hauteurs) >= 3 else "défaut"}
        for i, (x0, x1, p) in enumerate(reg):
            out["axes"].append({"x": x0 / ech, "largeur": (x1 - x0) / ech, "niveaux_presents": int(sum(p))})
            for k in range(n):
                if p[k]:
                    out["baies"].append({"type": "baie", "niveau": k + 1, "axe": i, "x": x0 / ech, "largeur": (x1 - x0) / ech,
                                         "z_bas": float(bornes[k] + of), "hauteur": hf})
        # balcons : bande de garde-corps au-dessus de chaque plancher
        for k in range(n):
            ya, yb = _row(bornes[k] + 1.1, z_max, ech), _row(bornes[k], z_max, ech)
            ya, yb = max(ya, 0), min(yb, h)
            if yb - ya < 5:
                continue
            o = np.convolve(mm[ya:yb].mean(axis=0), np.ones(7) / 7, mode="same")
            o[~cols_ok] = 0
            for (x0, x1) in ov._segments_1d(o, 0.40, int(0.3 * ech), int(1.6 * ech)):
                out["balcons"].append({"type": "balcon", "niveau": k + 1, "x": x0 / ech, "largeur": (x1 - x0) / ech,
                                       "z_bas": float(bornes[k]), "hauteur": 1.1})
    # RDC, à part : grandes ouvertures sombres (porche, porte, vitrines)
    ra, rb = max(_row(z_rdc, z_max, ech), 0), min(_row(sol, z_max, ech), h)
    if rb - ra > 20:
        sub = base[ra:rb]
        vsub = valide[ra:rb]
        if vsub.sum() > 0.1 * vsub.size:
            lab = cv2.cvtColor(cv2.GaussianBlur(sub, (0, 0), 2), cv2.COLOR_BGR2LAB)
            L = lab[..., 0].astype(np.float32)
            sombre = ((L < np.median(L[vsub]) - 45) & vsub).astype(np.uint8)
            sombre = cv2.morphologyEx(sombre, cv2.MORPH_CLOSE, np.ones((int(0.15 * ech) | 1, int(0.15 * ech) | 1), np.uint8))
            nb, _, st, _ = cv2.connectedComponentsWithStats(sombre, 8)
            for i in range(1, nb):
                x, y, bw, bh, a = st[i]
                if bw / ech >= 0.7 and bh / ech >= 1.0 and a / float(bw * bh) >= 0.5:
                    out["rdc"].append({"type": "rdc", "x": x / ech, "largeur": bw / ech, "z_bas": float(z_max - (ra + y + bh) / ech),
                                       "hauteur": bh / ech})
    return out
