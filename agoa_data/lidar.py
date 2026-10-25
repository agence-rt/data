"""Récupération du nuage de points LiDAR HD (IGN, format COPC) sur une emprise, par lecture partielle."""
import os

import numpy as np
from shapely import contains_xy

from .httprange import HttpRangeFile

CLASSES = {1: "non classé", 2: "sol", 3: "végétation basse", 4: "végétation moyenne",
           5: "végétation haute", 6: "bâtiment", 9: "eau", 17: "pont", 64: "sursol pérenne",
           65: "bruit", 66: "virtuel"}


def points_sur_emprise(dalles, emprise, marge=40.0, progress=None, cache_dir=None):
    """Points (x, y, z, classe) des `dalles` dans `emprise` élargie de `marge` m (Lambert 93, IGN69).

    Seuls les morceaux utiles des dalles sont téléchargés (quelques Mo au lieu de ~100 Mo).
    """
    import laspy
    from laspy import CopcReader

    zone = emprise.buffer(marge)
    x0, y0, x1, y1 = zone.bounds
    cle = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cle = os.path.join(cache_dir, "nuage_%d_%d_%d_%d_%s.npy" % (x0, y0, x1, y1, "-".join(
            d.nom.split("_PTS")[0].replace("LHD_FXX_", "") + d.date_edition for d in dalles)))
        if os.path.exists(cle):
            return np.load(cle)
    morceaux = []
    for i, d in enumerate(dalles):
        if progress:
            progress(f"Nuage de points : dalle {i + 1}/{len(dalles)} ({d.nom})")
        f = HttpRangeFile(d.url_nuage, progress=(lambda b, n, i=i: progress(
            f"Nuage de points : dalle {i + 1}/{len(dalles)} : {b / 1e6:.0f} Mo reçus") if progress else None))
        r = CopcReader(f)
        h = r.header
        # la dalle ne couvre peut-être qu'une partie de la zone
        if x1 < h.mins[0] or x0 > h.maxs[0] or y1 < h.mins[1] or y0 > h.maxs[1]:
            continue
        b = laspy.copc.Bounds(mins=np.array([max(x0, h.mins[0]), max(y0, h.mins[1])]),
                              maxs=np.array([min(x1, h.maxs[0]), min(y1, h.maxs[1])]))
        pts = r.query(bounds=b)
        if len(pts) == 0:
            continue
        morceaux.append(np.c_[np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z),
                              np.asarray(pts.classification)])
    if not morceaux:
        raise RuntimeError("Aucun point LiDAR dans la zone demandée.")
    d = np.vstack(morceaux)
    d = d[contains_xy(zone, d[:, 0], d[:, 1])]
    if cle:
        np.save(cle, d)
    return d
