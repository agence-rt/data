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


def _pics(profil, n, ecart):
    """Indices des n plus forts maxima locaux séparés d'au moins `ecart`."""
    idx = np.argsort(profil)[::-1]
    out = []
    for i in idx:
        if all(abs(i - j) >= ecart for j in out):
            out.append(int(i))
        if len(out) >= n:
            break
    return sorted(out)


def planchers_image(img, mk, ech, planchers_z, tol=0.6):
    """Pour chaque plancher attendu (z en m depuis le sol), hauteur réellement vue dans l'image (z lu) par les arêtes horizontales
    longues (dalles, bandeaux) ; renvoie la liste (z_attendu, z_lu | None)."""
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (0, 0), 1.5).astype(np.float32)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    gy[~mk] = 0
    H = img.shape[0]
    prof = np.convolve(np.median(gy, axis=1) + 0.5 * gy.mean(axis=1), np.ones(5) / 5, mode="same")
    res = []
    for z in planchers_z:
        r = int(round(H - z * ech))
        a, b = max(int(r - tol * ech), 0), min(int(r + tol * ech), H)
        if b - a < 5:
            res.append((z, None))
            continue
        j = a + int(np.argmax(prof[a:b]))
        ok = prof[j] > 1.8 * np.median(prof[prof > 0]) if (prof > 0).any() else False
        res.append((z, (H - j) / ech if ok else None))
    return res


def remettre_a_plat(img, mk, ech, couples):
    """Déformation verticale par morceaux : chaque z_lu est ramené sur z_attendu (sol et sommet fixes)."""
    H, W = img.shape[:2]
    src, dst = [0.0], [0.0]
    for z_att, z_lu in couples:
        if z_lu is not None and abs(z_lu - z_att) < 0.7 and z_lu > src[-1] + 0.5 and z_att > dst[-1] + 0.5:
            src.append(z_lu)
            dst.append(z_att)
    top = H / ech
    src.append(top)
    dst.append(top)
    # pour chaque ligne de sortie (z_att) -> ligne source (z_lu)
    z_out = (H - np.arange(H) - 0.5) / ech
    z_src = np.interp(z_out, dst, src)
    my = (H - z_src * ech - 0.5).astype(np.float32)
    mx = np.tile(np.arange(W, dtype=np.float32), (H, 1))
    mapy = np.tile(my[:, None], (1, W))
    return cv2.remap(img, mx, mapy, cv2.INTER_LINEAR, borderValue=(255, 255, 255)), cv2.remap(mk.astype(np.uint8), mx, mapy, cv2.INTER_NEAREST) > 0


def travees(img, mk, ech, z0, z1):
    """Travées de baies (x0, x1 en m) entre z0 et z1 : colonnes à forte proportion de pixels « non parement »."""
    H, W = img.shape[:2]
    ya, yb = max(int(H - z1 * ech), 0), min(int(H - z0 * ech), H)
    zone = np.zeros((H, W), bool)
    zone[ya:yb] = mk[ya:yb]
    zone = cv2.erode(zone.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    m = (ov._masque_ecart(img, zone) & zone).astype(np.uint8)
    k = max(3, int(0.05 * ech) | 1)
    m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8)), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    occ = m[ya:yb].mean(axis=0)
    occ[zone[ya:yb].mean(axis=0) < 0.3] = 0
    occ = np.convolve(occ, np.ones(5) / 5, mode="same")
    ok = zone[ya:yb].mean(axis=0) >= 0.3
    t = ov._otsu_borne(occ[ok], 0.16, 0.5) if ok.any() else 0.2
    segs = [(a / ech, b / ech) for a, b in ov._segments_1d(occ, t, int(0.45 * ech), int(0.45 * ech)) if (b - a) / ech <= 4.8]
    return segs


def rythme_central(segs, longueur):
    """Largeur et pas typiques d'après les travées du centre (moins déformées) ; renvoie dict ou None."""
    if len(segs) < 2:
        return None
    c = np.array([(a + b) / 2 for a, b in segs])
    w = np.array([b - a for a, b in segs])
    centre = longueur / 2
    ordre = np.argsort(np.abs(c - centre))[: max(2, (len(segs) + 1) // 2)]
    larg = float(np.median(w[ordre]))
    cc = np.sort(c[ordre])
    pas = float(np.median(np.diff(cc))) if len(cc) >= 2 else None
    return {"largeur": larg, "pas": pas}


def dessiner(img, segs, couples, ech, rythme):
    out = img.copy()
    H = out.shape[0]
    for i, (a, b) in enumerate(segs):
        cv2.rectangle(out, (int(a * ech), 0), (int(b * ech), H - 1), (0, 0, 255), 2)
        cv2.putText(out, str(i + 1), (int((a + b) / 2 * ech) - 15, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 4)
    for k, (z, zl) in enumerate(couples):
        y = int(H - z * ech)
        cv2.line(out, (0, y), (out.shape[1], y), (255, 120, 0), 2)
        cv2.putText(out, f"R+{k + 1}", (10, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 120, 0), 3)
    return out


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
    z_haut = h_rdc + max(n_etages, 1) * hn + 1.0 if n_etages else 12.0
    img, mk, info = vue_redressee(pano, p, f, t, d, ech=ech, z_haut=max(z_haut, 10.0))
    cv2.imwrite(os.path.join(dossier, "1_vue_redressee.jpg"), img)
    planch = [h_rdc + k * hn for k in range(max(n_etages, 1) + 1)]
    couples = planchers_image(img, mk, ech, planch)
    plat, mk2 = remettre_a_plat(img, mk, ech, couples)
    cv2.imwrite(os.path.join(dossier, "2_niveaux_remis_a_plat.jpg"), plat)
    segs = travees(plat, mk2, ech, h_rdc + 0.3, planch[-1] if planch else z_haut)
    ryt = rythme_central(segs, f.longueur)
    ann = dessiner(plat, segs, [(z, zl) for z, zl in zip(planch[:-1], couples)], ech, ryt)
    cv2.imwrite(os.path.join(dossier, "3_travees_numerotees.jpg"), ann, [cv2.IMWRITE_JPEG_QUALITY, 92])
    diag = {"vue": {"id": p.id, "azimut": p.azimut, "t_m": round(t, 2), "dist_m": round(d, 2)}, "rotation": info,
            "planchers_attendus_m": [round(z, 2) for z in planch], "planchers_lus_m": [None if zl is None else round(zl, 2) for _, zl in couples],
            "travees_m": [[round(a, 2), round(b, 2)] for a, b in segs], "rythme": ryt}
    with open(os.path.join(dossier, "diagnostic.json"), "w", encoding="utf-8") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=1)
    return ann, diag
