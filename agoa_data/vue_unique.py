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
    # travées manquées aux extrémités (persiennes fermées, bord de façade) : seuil plus bas, hors gouttières
    for fac in (0.6, 0.45):
        cand = [(a / ech, b / ech) for a, b in ov._segments_1d(p, t0 * fac, int(0.4 * ech), int(0.4 * ech))]
        cand = [c for c in cand if 0.9 <= c[1] - c[0] <= 2.4 and 0.9 < (c[0] + c[1]) / 2 < longueur - 0.9]
        for c in cand:
            if segs and all(c[0] >= s2[1] + 0.3 or c[1] <= s2[0] - 0.3 for s2 in segs):
                segs.append(c)
        segs.sort()
        if len(segs) >= 7:
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


def remettre_a_plat(img, mk, ech, ancres, kappa=1.0):
    """Déformation verticale par morceaux : `ancres` = [(z_lu, z_voulu)] ; sol et sommet fixes."""
    H, W = img.shape[:2]
    pts = sorted(ancres)
    src, dst = [0.0], [0.0]
    for zl, zv in pts:
        if zl > src[-1] + 0.4 and zv > dst[-1] + 0.4:
            src.append(zl)
            dst.append(zv)
    top = H / ech
    src.append(max(top / kappa, src[-1] + 0.1))
    dst.append(top)
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


def dessiner(img, segs, niv, ech):
    out = img.copy()
    H, W = out.shape[:2]
    for i, (a, b) in enumerate(segs):
        cv2.rectangle(out, (int(a * ech), 0), (int(b * ech), H - 1), (0, 160, 0), 2)
        cv2.putText(out, str(i + 1), (int((a + b) / 2 * ech) - 15, H - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 160, 0), 4)
    if niv.get("rdc"):
        y = int(H - niv["rdc"] * ech)
        cv2.line(out, (0, y), (W, y), (0, 0, 255), 3)
        cv2.putText(out, "haut du RDC", (W - 330, y + 34), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    for k, z in enumerate(niv.get("linteaux", [])):          # linteaux : repère régulier
        y = int(H - z * ech)
        if 0 <= y < H:
            cv2.line(out, (0, y), (W, y), (255, 200, 0), 2)
            cv2.putText(out, f"linteau R+{k + 1}", (W - 330, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 200, 0), 2)
    for k, z in enumerate(niv.get("planchers", [])):         # planchers : dessus de dalle
        y = int(H - z * ech)
        if 0 <= y < H:
            cv2.line(out, (0, y), (W, y), (255, 80, 0), 3)
            cv2.putText(out, f"plancher R+{k + 1}", (10, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 80, 0), 2)
    return out


def hauteurs(zc, zs, h_haut, n=None, e_dalle=None):
    """Niveaux déduits des linteaux (pas régulier), du haut du RDC (corniche) et du sommet de façade à l'alignement (coupe LiDAR).

    Un linteau est surmonté d'une dalle dont le dessus est le plancher du niveau supérieur : plancher R+(k+1) = linteau R+k + e_dalle ;
    e_dalle est lu entre le plancher R+1 (haut du RDC) et le premier linteau. Renvoie un dict (hauteurs en m, échelle de l'image)."""
    r = {}
    pas = float(np.median(np.diff(zs))) if len(zs) >= 2 else None
    if pas is None:
        return r
    # linteau « R+0 » virtuel = linteau du niveau sous R+1 : premier linteau lu − pas ; e_dalle = haut du RDC − ce linteau
    e = e_dalle if e_dalle is not None else zc - (zs[0] - pas)
    r["pas_lu"], r["e_dalle"] = pas, float(e)
    if h_haut:
        if n is None:
            n = max(1, int(round((h_haut - zc) / pas)))
            r["n_auto"] = True
        r["n"] = int(n)
        r["sommet_attendu"] = float(zc + n * pas)
        r["ecart_sommet"] = float(h_haut - (zc + n * pas))
        r["kappa"] = float(h_haut / (zc + n * pas)) if (zc + n * pas) > 0 else 1.0
    else:
        r["n"] = int(n) if n else len(zs)
    return r


def analyser_elevation(img, mk, ech, longueur, h_niveau=None, h_haut=None, n=None):
    """Corniche du RDC, travées numérotées, linteaux (référence des niveaux), hauteurs d'après la coupe, remise à plat."""
    zc = corniche(img, mk, ech)
    res = {"corniche_rdc_m": None if zc is None else round(zc, 2)}
    if zc is None:
        return img, mk, res, [], [], {}
    segs = travees(img, mk, ech, zc, longueur)
    lint = linteaux(img, ech, zc, segs)
    res["travees_m"] = [[round(a, 2), round(b, 2)] for a, b in segs]
    res["linteaux_lus_m"] = [round(z, 2) for z, _, _ in lint]
    res["linteaux_nb_travees"] = [k for _, k, _ in lint]
    zs = [z for z, _, _ in lint]
    hh = hauteurs(zc, zs, h_haut, n) if len(zs) >= 2 else {}
    if len(zs) >= 2:
        pas = hh["pas_lu"]
        res["pas_niveau_lu_m"] = round(pas, 2)
        reg = [zs[0] + k * pas for k in range(len(zs))]
        kap = hh.get("kappa", 1.0)
        res.update({"e_dalle_m": round(hh["e_dalle"], 2), "n_niveaux": hh.get("n"), "n_niveaux_auto": bool(hh.get("n_auto")),
                    "sommet_facade_coupe_m": None if not h_haut else round(h_haut, 2),
                    "sommet_attendu_image_m": None if "sommet_attendu" not in hh else round(hh["sommet_attendu"], 2),
                    "ecart_sommet_m": None if "ecart_sommet" not in hh else round(hh["ecart_sommet"], 2),
                    "kappa_vertical": round(kap, 3)})
        appliquer = 0.93 <= kap <= 1.07
        res["kappa_applique"] = bool(appliquer)
        if h_haut and not appliquer:
            n_alt = max(1, int(round((h_haut - zc) / pas)))
            res["avertissement"] = (f"Le sommet de façade lu sur la coupe ({h_haut:.2f} m) ne correspond pas à {hh.get('n')} niveaux de {pas:.2f} m "
                                    f"au-dessus du RDC ({zc:.2f} m) : écart {hh['ecart_sommet']:+.2f} m ; {n_alt} niveaux conviendraient. "
                                    f"Image non étirée ; vérifier le nombre de niveaux saisi.")
        kk = kap if appliquer else 1.0
        anc = [(zc, kk * zc)] + [(a, kk * b) for a, b in zip(zs, reg)]
        plat, mk2 = remettre_a_plat(img, mk, ech, anc, kappa=kk)
        # niveaux dans le repère final
        n_tot = hh.get("n", len(zs))
        pl = [kk * (zc + k * pas) for k in range(n_tot + 1)]                  # plancher R+1 … plancher du toit
        li = [kk * (zs[0] + k * pas) for k in range(n_tot)]                   # linteaux R+1 … R+n
        res["planchers_m"] = [round(z, 2) for z in pl]
        res["linteaux_m"] = [round(z, 2) for z in li]
        return plat, mk2, res, segs, {"planchers": pl, "linteaux": li, "rdc": kk * zc}
    return img, mk, res, segs, {"rdc": zc}


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
    plat, mk2, res, segs, niv = analyser_elevation(img, mk, ech, f.longueur, h_niveau=hn, h_haut=r.get("haut_alignement"), n=r.get("n_visibles"))
    cv2.imwrite(os.path.join(dossier, "2_niveaux_remis_a_plat.jpg"), plat)
    ann = dessiner(plat, segs, niv, ech)
    cv2.imwrite(os.path.join(dossier, "3_travees_numerotees.jpg"), ann, [cv2.IMWRITE_JPEG_QUALITY, 92])
    ryt = rythme_central(segs, f.longueur)
    diag = {"vue": {"id": p.id, "azimut": p.azimut, "t_m": round(t, 2), "dist_m": round(d, 2)}, "rotation": info,
            "haut_alignement_coupe_m": r.get("haut_alignement"), **res, "rythme": ryt}
    with open(os.path.join(dossier, "diagnostic.json"), "w", encoding="utf-8") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=1)
    return ann, diag
