"""Façade sur rue à partir d'UNE vue 360° bien choisie (face à la façade, vers le milieu).

1. choix de la vue la plus centrée ; vue frontale large (rotation pure : le plan de façade devient une image à échelle uniforme)
2. numérotation des travées de baies ; rythme déduit des travées centrales (peu déformées), appliqué à toute la façade
3. hauteurs de niveaux : lignes de planchers repérées dans l'image puis remise à plat sur les niveaux calculés (LiDAR + nombre de niveaux)
Repère de sortie : x = m depuis la limite gauche, z = m depuis le sol.
"""
import json
import math
import os

import cv2
import numpy as np

from . import mosaique, ouvertures as ov, panoramax, vues

H_CAMERA = mosaique.H_CAMERA


def choisir(cands, f, d_min=4.0, d_max=16.0):
    """Vue 360° la plus centrée devant la façade, ni trop près ni trop loin."""
    meil, note = None, 1e9
    for p in cands:
        if not p.equirect:
            continue
        t, d = mosaique._coords(p, f)
        if not d_min <= d <= d_max or not -2 <= t <= f.longueur + 2:
            continue
        s = abs(t - f.longueur / 2) + 0.25 * abs(d - 8)
        if s < note:
            meil, note = (p, t, d), s
    return meil


def vue_redressee(pano, p, f, t, d, ech=80, z_haut=12.0, fov=125.0, taille=(2600, 2400)):
    """Élévation métrique de la façade (BGR, ech px/m, 0 = sol en bas) + marge ; renvoie (img, masque, infos)."""
    nrm = (-f.sortant[0], -f.sortant[1])
    vue, fl, info = vues.vue_frontale(pano, p.azimut or 0.0, nrm, fov=fov, taille=taille, affiner=True)
    k = ech * d / fl
    img = cv2.resize(vue, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    cx, cy = vue.shape[1] / 2 * k, vue.shape[0] / 2 * k
    W, H = int(f.longueur * ech), int(z_haut * ech)
    M = np.float32([[1, 0, t * ech - cx - 0.0], [0, 1, 0]])
    # colonne 0 = limite gauche (s=0) ; ligne 0 = z_haut ; le centre optique est à s=t, z=H_CAMERA
    M = np.float32([[1, 0, -(t * ech - cx)], [0, 1, -((z_haut - H_CAMERA) * ech - cy)]]) * 1.0
    M[0, 2] = -(t * ech - cx) * -1 * -1
    # image -> sortie : x_out = x_img - (cx - t*ech) ; y_out = y_img - (cy - (z_haut-H)*ech)
    M = np.float32([[1, 0, t * ech - cx], [0, 1, (z_haut - H_CAMERA) * ech - cy]])
    out = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    mk = cv2.warpAffine(np.full(img.shape[:2], 255, np.uint8), M, (W, H), flags=cv2.INTER_NEAREST) > 0
    info.update({"t_cam": round(t, 2), "dist": round(d, 2), "echelle_px_m": round(fl / d, 1), "ech": ech})
    return out, mk, info


def _sm(v, n):
    return np.convolve(v, np.ones(n) / n, mode="same")


def corniche(img, mk, ech, z_min=3.0, z_max=6.5):
    """Haut du RDC : arête horizontale la plus longue/forte entre z_min et z_max (m). Renvoie z (m) ou None."""
    H = img.shape[0]
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.2)
    ey = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    ey[~mk] = 0
    prof = _sm(np.median(ey, axis=1), 5)
    z = (H - np.arange(H)) / ech
    sel = (z > z_min) & (z < z_max)
    if not sel.any():
        return None
    i = int(np.argmax(np.where(sel, prof, 0)))
    return float(z[i]) if prof[i] > 2.0 * max(np.median(prof[prof > 0]), 1e-3) else None


def travees(img, mk, ech, z_corniche, longueur, hauteur=1.6):
    """Travées de baies : colonnes où l'énergie des arêtes verticales est forte juste au-dessus de la corniche (vitrage du 1er niveau).
    Renvoie [(x0, x1) en m] de gauche à droite, hors gouttières et bords."""
    H = img.shape[0]
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.2)
    ex = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    ya, yb = max(int(H - (z_corniche + hauteur + 0.3) * ech), 0), int(H - (z_corniche + 0.3) * ech)
    p = _sm(ex[ya:yb].mean(axis=0), 9)
    if p.max() <= 0:
        return []
    t0 = ov._otsu_borne(p / p.max(), 0.15, 0.6) * p.max()
    for fac in (1.0, 0.85, 0.7):
        segs = [(a / ech, b / ech) for a, b in ov._segments_1d(p, t0 * fac, int(0.5 * ech), int(0.5 * ech))]
        segs = [s for s in segs if 0.8 <= s[1] - s[0] <= 2.6 and 0.9 < (s[0] + s[1]) / 2 < longueur - 0.9]
        if len(segs) >= 3:
            break
    return segs


def linteaux(img, ech, z_corniche, segs, tol=0.5):
    """Têtes de baie : haut des blocs d'arêtes verticales dans chaque travée (au-dessus de la corniche), regroupées par niveau.
    Renvoie [(z_median, nb_travees, [z...])] du bas vers le haut."""
    H = img.shape[0]
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.2)
    ex = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    yc = int(H - z_corniche * ech)
    tetes = []
    for a, b in segs:
        c, w = (a + b) / 2 * ech, (b - a) * ech * 0.3
        e = _sm(ex[:yc, int(c - w):int(c + w)].mean(axis=1), 9)
        if not (e > 0).any():
            continue
        on = e > 0.4 * np.percentile(e, 90)
        s0 = None
        for y in range(len(e)):
            if on[y] and s0 is None:
                s0 = y
            if (not on[y] or y == len(e) - 1) and s0 is not None:
                if y - s0 > 0.5 * ech:
                    tetes.append((H - s0) / ech)
                s0 = None
    tetes.sort()
    groupes = []
    for z in tetes:
        if groupes and z - groupes[-1][-1] < tol:
            groupes[-1].append(z)
        else:
            groupes.append([z])
    out = [(float(np.percentile(gp, 70)), len(gp), gp) for gp in groupes if len(gp) >= 2 and np.median(gp) > z_corniche + 0.8]
    return out


def remettre_a_plat(img, mk, ech, ancres):
    """Déformation verticale par morceaux : `ancres` = [(z_lu, z_voulu)] ; sol et sommet fixes."""
    H, W = img.shape[:2]
    pts = sorted(ancres)
    src, dst = [0.0], [0.0]
    for zl, zv in pts:
        if zl > src[-1] + 0.4 and zv > dst[-1] + 0.4:
            src.append(zl)
            dst.append(zv)
    top = H / ech
    src.append(top + (dst[-1] - src[-1]) * 0)
    dst.append(top + (dst[-1] - src[-1]) * 0) if False else dst.append(top)
    z_out = (H - np.arange(H) - 0.5) / ech
    z_src = np.interp(z_out, dst, src)
    my = (H - z_src * ech - 0.5).astype(np.float32)
    mapy = np.tile(my[:, None], (1, W))
    mx = np.tile(np.arange(W, dtype=np.float32), (H, 1))
    return cv2.remap(img, mx, mapy, cv2.INTER_LINEAR, borderValue=(255, 255, 255)), cv2.remap(mk.astype(np.uint8), mx, mapy, cv2.INTER_NEAREST) > 0


def rythme_central(segs, longueur):
    """Largeur et pas typiques d'après les travées du centre (moins déformées)."""
    if len(segs) < 2:
        return None
    c = np.array([(a + b) / 2 for a, b in segs])
    w = np.array([b - a for a, b in segs])
    ordre = np.argsort(np.abs(c - longueur / 2))[: max(2, (len(segs) + 1) // 2)]
    cc = np.sort(c[ordre])
    return {"largeur": float(np.median(w[ordre])), "pas": float(np.median(np.diff(cc))) if len(cc) >= 2 else None}


def dessiner(img, segs, lint, z_corniche, ech):
    out = img.copy()
    H, W = out.shape[:2]
    for i, (a, b) in enumerate(segs):
        cv2.rectangle(out, (int(a * ech), 0), (int(b * ech), H - 1), (0, 160, 0), 2)
        cv2.putText(out, str(i + 1), (int((a + b) / 2 * ech) - 15, H - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 160, 0), 4)
    if z_corniche:
        y = int(H - z_corniche * ech)
        cv2.line(out, (0, y), (W, y), (0, 0, 255), 3)
        cv2.putText(out, "RDC", (10, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    for k, z in enumerate(lint):
        y = int(H - z * ech)
        cv2.line(out, (0, y), (W, y), (255, 120, 0), 3)
        cv2.putText(out, f"R+{k + 1}", (10, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 120, 0), 3)
    return out


def analyser_elevation(img, mk, ech, longueur, h_niveau=None):
    """Corniche du RDC, travées numérotées, linteaux (référence des niveaux), remise à plat. Renvoie (plat, mk_plat, dict)."""
    zc = corniche(img, mk, ech)
    res = {"corniche_rdc_m": None if zc is None else round(zc, 2)}
    if zc is None:
        return img, mk, res, [], []
    segs = travees(img, mk, ech, zc, longueur)
    lint = linteaux(img, ech, zc, segs)
    res["travees_m"] = [[round(a, 2), round(b, 2)] for a, b in segs]
    res["linteaux_lus_m"] = [round(z, 2) for z, _, _ in lint]
    res["linteaux_nb_travees"] = [n for _, n, _ in lint]
    zs = [z for z, _, _ in lint]
    if len(zs) >= 2:
        pas = float(np.median(np.diff(zs)))
        res["pas_niveau_lu_m"] = round(pas, 2)
        reg = [zs[0] + k * pas for k in range(len(zs))]          # linteaux régularisés : niveaux de même hauteur
        res["linteaux_regularises_m"] = [round(z, 2) for z in reg]
        plat, mk2 = remettre_a_plat(img, mk, ech, list(zip(zs, reg)))
        if h_niveau:
            res["ecart_pas_vs_lidar"] = round(pas / h_niveau, 3)
    else:
        reg, plat, mk2 = zs, img, mk
    return plat, mk2, res, segs, reg


def traiter(f, dossier, cands=None, telecharger=None, say=print, ech=80, n_etages=2, say_diag=None):
    """Pipeline complet sur une façade. `f.r` doit contenir les planchers (definir_niveaux) pour la remise à plat."""
    telecharger = telecharger or panoramax.telecharger
    os.makedirs(dossier, exist_ok=True)
    if cands is None:
        cands = mosaique._bbox_candidats(f)
    ch = choisir(cands, f)
    if ch is None:
        raise ValueError("Aucune vue 360° exploitable devant cette façade")
    p, t, d = ch
    say(f"Vue retenue {p.id[:8]} : à {t:.1f} m du bord gauche, {d:.1f} m de la façade, azimut {p.azimut}")
    pano = telecharger(p)
    cv2.imwrite(os.path.join(dossier, "pano.jpg"), pano)
    if pano.shape[1] > 6000:
        pano = cv2.resize(pano, (6144, 3072), interpolation=cv2.INTER_AREA)
    r = f.r
    h_rdc = r.get("h_rdc") or 3.0
    hn = r.get("h_niveau") or 2.8
    img, mk, info = vue_redressee(pano, p, f, t, d, ech=ech, z_haut=11.0)
    cv2.imwrite(os.path.join(dossier, "1_vue_redressee.jpg"), img)
    plat, mk2, res, segs, reg = analyser_elevation(img, mk, ech, f.longueur, h_niveau=hn)
    cv2.imwrite(os.path.join(dossier, "2_niveaux_remis_a_plat.jpg"), plat)
    zc = res.get("corniche_rdc_m")
    ann = dessiner(plat, segs, reg, zc, ech)
    cv2.imwrite(os.path.join(dossier, "3_travees_numerotees.jpg"), ann, [cv2.IMWRITE_JPEG_QUALITY, 92])
    ryt = rythme_central(segs, f.longueur)
    diag = {"vue": {"id": p.id, "azimut": p.azimut, "t_m": round(t, 2), "dist_m": round(d, 2)}, "rotation": info,
            "attendu_lidar": {"h_rdc": round(h_rdc, 2), "h_niveau": round(hn, 2)}, **res, "rythme": ryt}
    with open(os.path.join(dossier, "diagnostic.json"), "w", encoding="utf-8") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=1)
    return ann, diag
