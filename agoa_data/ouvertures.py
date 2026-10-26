"""Détection des ouvertures et des balcons sur une élévation redressée (niveau 2).

L'élévation est une image à l'échelle (`ech` px/m, ligne 0 = haut). Le parement (pierre, enduit) est la couleur dominante de la façade ;
les baies, persiennes, garde-corps et dalles de balcon s'en écartent. Les composantes sont regroupées, filtrées par dimensions
plausibles et alignées sur les trames verticales (mêmes abscisses d'un niveau à l'autre).
"""
import math

import cv2
import numpy as np


def _masque_ecart(img, valide):
    """Pixels qui ne sont pas du parement : moins « beige » que la pierre (cadres blancs, persiennes, dalles) ou nettement plus sombres
    (vitrages, ferronnerie). Le parement est la teinte médiane de la zone."""
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 1.5), cv2.COLOR_BGR2LAB).astype(np.float32)
    ref = np.median(lab[valide], axis=0)
    L, B = lab[..., 0], lab[..., 2]
    ecart_b = max(6.0, min(10.0, float(np.percentile(np.abs(B[valide] - ref[2]), 70)) * 1.6))
    return ((B < ref[2] - ecart_b) | (L < ref[0] - 55)) & valide


def _segments_1d(profil, seuil, ecart_min, long_min):
    """Plages [a, b) où profil > seuil ; deux plages séparées de moins de `ecart_min` sont fusionnées ; celles de moins de `long_min` sont ignorées."""
    on = profil > seuil
    plages, debut = [], None
    for i, v in enumerate(list(on) + [False]):
        if v and debut is None:
            debut = i
        elif not v and debut is not None:
            plages.append([debut, i])
            debut = None
    fus = []
    for pl in plages:
        if fus and pl[0] - fus[-1][1] < ecart_min:
            fus[-1][1] = pl[1]
        else:
            fus.append(pl)
    return [(a, b) for a, b in fus if b - a >= long_min]


def detecter(img, valide, ech, z_haut_px=None, z_bas_px=None):
    """Détecte baies et balcons par la trame de la façade : bandes horizontales de niveau (lignes où le parement est interrompu),
    puis, dans la partie haute de chaque bande (vitrages, persiennes, sans garde-corps), les colonnes de baies ; la partie basse
    donne les balcons (éléments continus plus larges que les baies). Renvoie des dicts {x0, x1, y0, y1 (px), type, w_m, h_m}."""
    h, w = img.shape[:2]
    zone = valide.copy()
    if z_haut_px is not None:
        zone &= (np.arange(h)[:, None] >= z_haut_px[None, :] + 0.15 * ech)
    if z_bas_px is not None:
        zone &= (np.arange(h)[:, None] <= z_bas_px[None, :] - 0.15 * ech)
    zone = cv2.erode(zone.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    if zone.sum() < 100:
        return []
    m = (_masque_ecart(img, zone) & zone).astype(np.uint8)
    k = max(3, int(0.05 * ech) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))               # supprime joints de pierre
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    haut_zone = np.where(zone.any(axis=1))[0]
    y_min, y_max = int(haut_zone.min()), int(haut_zone.max()) + 1
    cols = zone[y_min:y_max].mean(axis=0)
    mm = m > 0
    # 1. axes verticaux : colonnes où les ouvertures sont fréquentes (baies alignées d'un niveau à l'autre)
    prof_c = mm[y_min:y_max].sum(axis=0) / np.maximum(zone[y_min:y_max].sum(axis=0), 1)
    prof_c[cols < 0.3] = 0
    prof_c = np.convolve(prof_c, np.ones(5) / 5, mode="same")
    t_c = _otsu_borne(prof_c[cols >= 0.3], 0.20, 0.6)
    axes = [a for a in _segments_1d(prof_c, t_c, int(0.2 * ech), int(0.45 * ech)) if (a[1] - a[0]) / ech <= 4.8]
    if not axes:
        return []
    # 2. niveaux : pics du profil horizontal restreint aux axes (les trames de baies sont communes à tous les axes)
    sur_axes = np.zeros(w, bool)
    for x0, x1 in axes:
        sur_axes[x0:x1] = True
    prof_r = (mm[:, sur_axes]).mean(axis=1)
    prof_r[~zone[:, sur_axes].any(axis=1)] = 0
    k = max(3, int(0.15 * ech) | 1)
    prof_fine = np.convolve(prof_r, np.ones(5) / 5, mode="same")
    prof_r = np.convolve(prof_r, np.ones(k) / k, mode="same")
    ordre = np.argsort(prof_r)[::-1]
    pics = []
    for y in ordre:
        if prof_r[y] < 0.30 * prof_r.max():
            break
        if all(abs(int(y) - q) >= 1.9 * ech for q in pics):
            pics.append(int(y))
    pics.sort()
    niveaux = []
    for y in pics:
        a_ = y
        while a_ > 0 and prof_fine[a_ - 1] >= 0.12 and y - a_ < 2.6 * ech:
            a_ -= 1
        b_ = y
        while b_ < h - 1 and prof_fine[b_ + 1] >= 0.12 and b_ - y < 2.6 * ech:
            b_ += 1
        niveaux.append((a_, b_))
    out = []
    # 3. une baie par case (axe x niveau) dont l'occupation est suffisante
    for (x0, x1) in axes:
        for (ya, yb) in niveaux:
            cell = mm[ya:yb, x0:x1]
            if cell.size == 0 or cell.mean() < 0.22:
                continue
            lignes = np.where(cell.mean(axis=1) > 0.2)[0]
            if len(lignes) == 0:
                continue
            y0, y1 = ya + int(lignes.min()), ya + int(lignes.max()) + 1
            hm = (y1 - y0) / ech
            if hm < 0.45 or hm > 3.6:
                continue
            out.append({"x0": int(x0), "x1": int(x1), "y0": y0, "y1": y1, "type": "baie" if hm >= 1.75 else "fenetre",
                        "w_m": (x1 - x0) / ech, "h_m": hm})
    # 4. balcons : éléments continus (dalle + garde-corps) dans le tiers bas de chaque niveau, plus larges que 1,6 m
    for (ya, yb) in niveaux:
        bas = yb - int(0.38 * (yb - ya))
        sub = mm[bas:yb]
        occ = np.convolve(sub.mean(axis=0), np.ones(7) / 7, mode="same")
        occ[cols < 0.3] = 0
        for (x0, x1) in _segments_1d(occ, 0.45, int(0.3 * ech), int(1.6 * ech)):
            out.append({"x0": int(x0), "x1": int(x1), "y0": int(bas), "y1": int(yb), "type": "balcon",
                        "w_m": (x1 - x0) / ech, "h_m": (yb - bas) / ech})
    return sorted(out, key=lambda o: (o["y0"], o["x0"]))


def _otsu_borne(v, lo, hi):
    """Seuil d'Otsu d'un profil (valeurs 0..1), borné à [lo, hi]."""
    if len(v) < 10:
        return lo
    t, _ = cv2.threshold(np.clip(np.asarray(v) * 255, 0, 255).astype(np.uint8).reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(np.clip(t / 255.0, lo, hi))


def aligner_sur_trames(ouvertures, ech, tol_m=0.25):
    """Aligne les abscisses des baies d'un même axe (écart < tol) sur leur médiane : les façades de copropriété ont des trames régulières."""
    cand = [o for o in ouvertures if o["type"] in ("baie", "fenetre")]
    for cle in ("x0", "x1"):
        cand.sort(key=lambda o: o[cle])
        groupe = []
        for o in cand + [None]:
            if o is not None and (not groupe or o[cle] - groupe[-1][cle] <= tol_m * ech):
                groupe.append(o)
                continue
            if len(groupe) >= 2:
                v = int(round(float(np.median([g[cle] for g in groupe]))))
                for g in groupe:
                    g[cle] = v
            groupe = [o] if o is not None else []
    for o in cand:
        o["w_m"] = (o["x1"] - o["x0"]) / ech
    return ouvertures


def niveaux_depuis(ouvertures, ech, z_max, tol_m=0.5):
    """Altitudes (z) des planchers déduites des dalles de balcon et des seuils (bas des baies) regroupés par niveau."""
    bas = sorted(o["y1"] for o in ouvertures if o["type"] in ("baie", "fenetre", "balcon"))
    niveaux, groupe = [], []
    for y in bas + [None]:
        if y is not None and (not groupe or y - groupe[-1] <= tol_m * ech):
            groupe.append(y)
            continue
        if len(groupe) >= 2:
            niveaux.append(z_max - float(np.median(groupe)) / ech)
        groupe = [y] if y is not None else []
    return sorted(niveaux)


def dessiner(img, ouvertures, chemin, ech):
    """Aperçu de validation : élévation redressée + rectangles colorés par type."""
    out = img.copy()
    couleurs = {"baie": (0, 120, 255), "fenetre": (0, 180, 0), "balcon": (200, 0, 200), "groupe": (0, 0, 220)}
    for o in ouvertures:
        cv2.rectangle(out, (o["x0"], o["y0"]), (o["x1"], o["y1"]), couleurs.get(o["type"], (0, 0, 0)), max(2, int(ech / 25)))
    cv2.imwrite(chemin, out)
