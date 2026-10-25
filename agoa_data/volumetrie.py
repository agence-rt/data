"""Volumétrie d'un bâtiment à partir du nuage de points classifié (classe 6 = bâtiment, 2 = sol)."""
from dataclasses import dataclass, field

import numpy as np
from shapely import contains_xy


@dataclass
class Volumetrie:
    emprise_m2: float
    emprise_raster_m2: float
    altitude_sol: float
    sol_min: float
    sol_max: float
    n_points_toit: int
    n_points_sol: int
    couverture: float           # part de l'emprise couverte par le modèle de toiture (1 = complet)
    hauteur_moyenne: float
    hauteur_mediane: float
    hauteur_p90: float
    hauteur_max: float          # 98e centile (écarte antennes et souches isolées)
    altitude_point_haut: float
    volume_m3: float
    modele: np.ndarray = field(repr=False, default=None)   # altitudes du toit (NaN hors emprise)
    origine: tuple = (0.0, 0.0)  # coin haut-gauche du raster
    pas: float = 0.5

    def resume(self):
        d = {k: v for k, v in self.__dict__.items() if k not in ("modele", "origine")}
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}


def altitude_du_sol(pts, emprise, anneau=(0.5, 3.0)):
    """Altitude médiane du sol (classe 2) dans une couronne autour de l'emprise."""
    couronne = emprise.buffer(anneau[1]).difference(emprise.buffer(anneau[0]))
    m = (pts[:, 3] == 2) & contains_xy(couronne, pts[:, 0], pts[:, 1])
    if m.sum() < 5:      # cour fermée ou sol masqué : on élargit
        couronne = emprise.buffer(anneau[1] * 3).difference(emprise.buffer(anneau[0]))
        m = (pts[:, 3] == 2) & contains_xy(couronne, pts[:, 0], pts[:, 1])
    if m.sum() < 5:
        raise ValueError("Pas assez de points « sol » autour du bâtiment.")
    z = pts[m, 2]
    return float(np.median(z)), float(z.min()), float(z.max()), int(m.sum())


def modele_de_toiture(pts, emprise, pas=0.5, marge=0.3, classes=(6,)):
    """Raster d'altitudes du toit : moyenne par maille puis comblement des trous par propagation."""
    zone = emprise.buffer(marge)
    x0, y0, x1, y1 = zone.bounds
    nx, ny = int(np.ceil((x1 - x0) / pas)), int(np.ceil((y1 - y0) / pas))
    m = np.isin(pts[:, 3], classes) & contains_xy(zone, pts[:, 0], pts[:, 1])
    ix = np.clip(((pts[m, 0] - x0) / pas).astype(int), 0, nx - 1)
    iy = np.clip(((y1 - pts[m, 1]) / pas).astype(int), 0, ny - 1)     # ligne 0 = nord
    somme = np.zeros((ny, nx))
    n = np.zeros((ny, nx))
    np.add.at(somme, (iy, ix), pts[m, 2])
    np.add.at(n, (iy, ix), 1)
    Z = np.where(n > 0, somme / np.maximum(n, 1), np.nan)
    gx = x0 + (np.arange(nx) + 0.5) * pas
    gy = y1 - (np.arange(ny) + 0.5) * pas
    X, Y = np.meshgrid(gx, gy)
    dedans = contains_xy(zone, X, Y)
    # comblement : moyenne des 8 voisins connus, jusqu'à stabilité (limité à l'emprise)
    for _ in range(40):
        vide = np.isnan(Z) & dedans
        if not vide.any():
            break
        P = np.pad(Z, 1, constant_values=np.nan)
        voisins = np.stack([P[1 + dy:1 + dy + ny, 1 + dx:1 + dx + nx]
                            for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)])
        connus = (~np.isnan(voisins)).sum(axis=0)
        moy = np.where(connus > 0, np.nansum(voisins, axis=0) / np.maximum(connus, 1), np.nan)
        nouveau = vide & ~np.isnan(moy)
        if not nouveau.any():
            break
        Z[nouveau] = moy[nouveau]
    Z[~dedans] = np.nan
    return Z, (x0, y1), pas, X, Y


def calculer(pts, emprise, pas=0.5):
    sol, smin, smax, nsol = altitude_du_sol(pts, emprise)
    Z, orig, pas, X, Y = modele_de_toiture(pts, emprise, pas)
    dans_emprise = contains_xy(emprise, X, Y)
    dedans = dans_emprise & ~np.isnan(Z)
    if dedans.sum() == 0:
        raise ValueError("Aucun point « bâtiment » dans l'emprise : toiture non classée dans cette zone.")
    H = Z[dedans] - sol
    H = np.clip(H, 0, None)
    cell = pas * pas
    toit = (pts[:, 3] == 6) & contains_xy(emprise, pts[:, 0], pts[:, 1])
    return Volumetrie(
        emprise_m2=float(emprise.area), emprise_raster_m2=float(dedans.sum() * cell),
        altitude_sol=sol, sol_min=smin, sol_max=smax, n_points_toit=int(toit.sum()), n_points_sol=nsol,
        couverture=float(dedans.sum() / max(dans_emprise.sum(), 1)),
        hauteur_moyenne=float(H.mean()), hauteur_mediane=float(np.median(H)),
        hauteur_p90=float(np.percentile(H, 90)), hauteur_max=float(np.percentile(pts[toit, 2], 98) - sol),
        altitude_point_haut=float(pts[toit, 2].max()), volume_m3=float(H.sum() * cell),
        modele=Z, origine=orig, pas=pas)
