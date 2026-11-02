"""Plan de toiture : courbes de niveau, pans (pente, orientation) et points cotés lus dans le nuage de points (classe « bâtiment »)."""
import math

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely import contains_xy

PAS = 0.5             # m : maille du modèle de toiture
PENTE_MAX = 68.0      # ° : au-delà ce n'est plus un pan de toit (murs, souches, bords de zones sans points)
PENTE_PLAT = 6.0      # ° : en dessous, le pan est une terrasse (toiture plate)
PENTE_RAIDE = 40.0    # ° : au-dessus, comble brisé / mansard (on distingue le haut et le bas d'une toiture à la Mansart)
SURFACE_MIN = 6.0     # m² : pans plus petits ignorés
SECTEURS = ["N", "N-E", "E", "S-E", "S", "S-O", "O", "N-O"]


def _modele(pts, emprise):
    from .volumetrie import modele_de_toiture
    Z, (x0, y1), pas, X, Y = modele_de_toiture(pts, emprise, pas=PAS, marge=0.0)
    dedans = contains_xy(emprise, X, Y)
    Z = np.where(dedans, Z, np.nan)
    # mailles réellement mesurées (≥ 1 point « bâtiment ») : les trous comblés par propagation ne doivent pas fabriquer de faux pans
    from scipy import ndimage as ndi
    ny, nx = Z.shape
    ix = np.clip(((pts[:, 0] - x0) / PAS).astype(int), 0, nx - 1)
    iy = np.clip(((y1 - pts[:, 1]) / PAS).astype(int), 0, ny - 1)
    mes = np.zeros((ny, nx), bool)
    b = pts[:, 3] == 6
    mes[iy[b], ix[b]] = True
    mes = ndi.binary_dilation(mes, iterations=1)
    return Z, x0, y1, dedans & mes


def _pans(Z, x0, y1, dedans):
    from scipy import ndimage as ndi
    import cv2
    F = np.where(np.isnan(Z), np.nanmedian(Z), Z)
    G = ndi.gaussian_filter(F, 1.0)
    dzdx = np.gradient(G, axis=1) / PAS
    dzdy = -np.gradient(G, axis=0) / PAS                   # lignes vers le sud → y vers le nord
    pente = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    az = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360) % 360   # direction de la plus grande pente descendante, depuis le nord
    secteur = np.round(az / 45).astype(int) % 8
    code = np.where(pente < PENTE_PLAT, 0, 1 + secteur + 8 * (pente >= PENTE_RAIDE))
    code = np.where(dedans & ~np.isnan(Z) & (pente < PENTE_MAX), code, -1)
    code = ndi.median_filter(code, size=3)
    pans = []
    for k in [c for c in np.unique(code) if c >= 0]:
        lab, n = ndi.label(code == k)
        for j in range(1, n + 1):
            m = lab == j
            surf = m.sum() * PAS * PAS
            if surf < SURFACE_MIN:
                continue
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            c = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
            if len(c) < 4:
                continue
            xy = [(x0 + (cx + 0.5) * PAS, y1 - (cy + 0.5) * PAS) for cx, cy in c]
            poly = Polygon(xy).buffer(0).simplify(0.2)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            zz = Z[m]
            pn = float(np.median(pente[m]))
            a = float(np.degrees(np.arctan2(np.mean(np.sin(np.radians(az[m]))), np.mean(np.cos(np.radians(az[m]))))) % 360)
            pans.append({"geom": poly, "surface_plan_m2": float(surf), "surface_reelle_m2": float(surf / math.cos(math.radians(min(pn, 80)))),
                         "pente_deg": pn, "orientation_deg": a, "orientation": "plat" if k == 0 else SECTEURS[int(round(a / 45)) % 8],
                         "plat": k == 0, "z_min": float(zz.min()), "z_max": float(zz.max()), "z_moy": float(zz.mean())})
    pans.sort(key=lambda p: -p["surface_plan_m2"])
    for i, p in enumerate(pans, 1):
        p["id"] = i
    return pans


def _courbes(Z, x0, y1, pas_niveau):
    from skimage.measure import find_contours
    zmin, zmax = float(np.nanmin(Z)), float(np.nanmax(Z))
    out = []
    for niv in np.arange(math.ceil(zmin / pas_niveau) * pas_niveau, zmax, pas_niveau):
        for c in find_contours(Z, niv):
            if len(c) < 4:
                continue
            xy = [(x0 + (col + 0.5) * PAS, y1 - (row + 0.5) * PAS) for row, col in c]
            ls = LineString(xy).simplify(0.1)
            if ls.length >= 1.0:
                out.append((float(niv), ls))
    return out


def _cotes(Z, x0, y1, pans):
    """Points cotés : sommet (ou milieu) de chaque pan et point haut général."""
    pts = []
    for p in pans:
        c = p["geom"].representative_point()
        pts.append((c.x, c.y, p["z_moy"], p["id"]))
    return pts


def plan(pts, emprise, sol, chemin_png, chemin_dxf):
    """Écrit le plan de toiture (PNG + DXF Lambert 93, altitudes NGF) et renvoie le dict de résultats."""
    Z, x0, y1, dedans = _modele(pts, emprise)
    if np.isnan(Z).all():
        raise ValueError("Aucun point « bâtiment » dans l'emprise.")
    pans = _pans(Z, x0, y1, dedans)
    Zm = np.where(dedans, Z, np.nan)
    mineures = _courbes(Zm, x0, y1, 1.0)
    _png(Z, x0, y1, emprise, pans, mineures, sol, chemin_png)
    _dxf(emprise, pans, mineures, sol, chemin_dxf)
    return {"altitude_max_ngf": float(np.nanmax(Z)), "altitude_min_ngf": float(np.nanmin(Z)),
            "n_pans": len(pans),
            "pans": [{"id": p["id"], "type": "terrasse" if p["plat"] else "versant", "orientation": p["orientation"],
                      "pente_deg": round(p["pente_deg"], 1), "surface_plan_m2": round(p["surface_plan_m2"], 1),
                      "surface_reelle_m2": round(p["surface_reelle_m2"], 1), "altitude_min_ngf": round(p["z_min"], 2),
                      "altitude_max_ngf": round(p["z_max"], 2), "altitude_moyenne_ngf": round(p["z_moy"], 2)} for p in pans]}


def _etiquette(p):
    if p["plat"]:
        return f"{p['id']} terrasse {p['z_moy']:.2f} NGF"
    return f"{p['id']} {p['orientation']} {p['pente_deg']:.0f}° ({p['z_min']:.1f}→{p['z_max']:.1f})"


def _etiquette_courte(p):
    return f"{p['id']}" + (" terr." if p["plat"] else f" {p['orientation']} {p['pente_deg']:.0f}°")


def _png(Z, x0, y1, emprise, pans, courbes, sol, chemin):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MPoly
    ny, nx = Z.shape
    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(np.where(np.isnan(Z), np.nan, Z) - sol, extent=[x0, x0 + nx * PAS, y1 - ny * PAS, y1], cmap="viridis", alpha=.85)
    cb = fig.colorbar(im, ax=ax, shrink=.7)
    cb.set_label(f"hauteur au-dessus du sol ({sol:.2f} m NGF)")
    for niv, l in courbes:
        major = abs(niv / 5 - round(niv / 5)) < 1e-6
        ax.plot(*l.xy, color="k", lw=.9 if major else .35, alpha=.8 if major else .5)
        if major and l.length > 6:
            c = l.interpolate(l.length / 2)
            ax.text(c.x, c.y, f"{niv:.0f}", fontsize=6, ha="center", bbox=dict(fc="white", ec="none", alpha=.7, pad=0))
    for p in pans:
        ax.add_patch(MPoly(np.array(p["geom"].exterior.coords), fill=False, ec="#ffffff" if p["plat"] else "#ffd000", lw=1.6))
        c = p["geom"].representative_point()
        ax.text(c.x, c.y, _etiquette_courte(p), fontsize=7, ha="center", color="white",
                bbox=dict(fc="black", ec="none", alpha=.55, pad=1))
    ax.plot(*emprise.exterior.xy, "r-", lw=2)
    ax.set_aspect("equal")
    ax.set_title("Plan de toiture — courbes tous les 1 m (fortes : 5 m NGF)\npans : n° / orientation de la plus grande pente / pente", fontsize=10)
    ax.set_xlabel("x Lambert 93 (m)")
    ax.set_ylabel("y Lambert 93 (m)")
    fig.tight_layout()
    fig.savefig(chemin, dpi=130)
    plt.close(fig)


def _dxf(emprise, pans, courbes, sol, chemin):
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    for nom, c in (("TOIT_EMPRISE", 1), ("TOIT_PANS", 2), ("TOIT_COURBES", 8), ("TOIT_COURBES_MAJEURES", 7), ("TOIT_TEXTES", 3)):
        doc.layers.add(nom, color=c)
    msp.add_lwpolyline(list(emprise.exterior.coords), dxfattribs={"layer": "TOIT_EMPRISE"})
    for p in pans:
        msp.add_lwpolyline(list(p["geom"].exterior.coords), dxfattribs={"layer": "TOIT_PANS"})
        c = p["geom"].representative_point()
        msp.add_text(_etiquette(p), height=0.35, dxfattribs={"layer": "TOIT_TEXTES", "insert": (c.x, c.y)})
    for niv, l in courbes:
        major = abs(niv / 5 - round(niv / 5)) < 1e-6
        msp.add_lwpolyline(list(l.coords), dxfattribs={"layer": "TOIT_COURBES_MAJEURES" if major else "TOIT_COURBES", "elevation": niv})
        if major:
            c = l.interpolate(l.length / 2)
            msp.add_text(f"{niv:.1f}", height=0.3, dxfattribs={"layer": "TOIT_TEXTES", "insert": (c.x, c.y)})
    doc.saveas(chemin)
    return chemin
