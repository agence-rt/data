"""Profils en long des voies autour de la parcelle (sol LiDAR le long de l'axe de la route) : altitudes et pentes."""
import math

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.ops import linemerge

LARGEUR = 2.5      # m : demi-largeur de la bande de points « sol » prise de part et d'autre de l'axe
PAS = 1.0          # m : pas d'échantillonnage le long de l'axe
FENETRE = 10.0     # m : longueur sur laquelle une pente locale est mesurée


def _morceaux(geom, zone):
    """Tronçons de la voie situés dans la zone couverte par le nuage, fusionnés et d'un seul tenant."""
    g = geom.intersection(zone)
    if g.is_empty:
        return []
    if g.geom_type == "MultiLineString":
        g = linemerge(g)
    lignes = [g] if g.geom_type == "LineString" else [x for x in getattr(g, "geoms", []) if x.geom_type == "LineString"]
    return [l for l in lignes if l.length >= 15]


def _cap(ligne):
    (x0, y0), (x1, y1) = ligne.coords[0][:2], ligne.coords[-1][:2]
    az = (math.degrees(math.atan2(x1 - x0, y1 - y0)) + 360) % 360
    return az, ["N", "N-E", "E", "S-E", "S", "S-O", "O", "N-O"][int(((az + 22.5) % 360) // 45)]


def profil_voie(pts, ligne):
    """Profil en long d'un tronçon : liste de (s, altitude NGF), orienté dans le sens montant. None si trop peu de sol."""
    sol = pts[pts[:, 3] == 2]
    tampon = ligne.buffer(LARGEUR)
    m = shapely.contains_xy(tampon, sol[:, 0], sol[:, 1])
    if m.sum() < 30:
        return None
    s = shapely.line_locate_point(ligne, shapely.points(sol[m, 0], sol[m, 1]))
    z = sol[m, 2]
    cases = np.arange(0, ligne.length + PAS, PAS)
    zc = np.full(len(cases), np.nan)
    for i, a in enumerate(cases):
        k = (s >= a - PAS) & (s < a + PAS)            # fenêtre glissante de 2 m
        if k.sum() >= 3:
            zc[i] = np.median(z[k])
    bon = ~np.isnan(zc)
    if bon.sum() < 8:
        return None
    zi = np.interp(cases, cases[bon], zc[bon])
    # trous > 8 m (bâtiments, cours) : valeur non fiable → NaN
    trou = np.zeros(len(cases), bool)
    idx = np.flatnonzero(bon)
    for a, b in zip(idx[:-1], idx[1:]):
        if (b - a) * PAS > 8:
            trou[a + 1:b] = True
    zi[trou] = np.nan
    lisse = np.array([np.nanmedian(zi[max(i - 1, 0):i + 2]) if not np.isnan(zi[i]) else np.nan for i in range(len(zi))])
    return cases, lisse


def analyser(pts, nom, ligne, emprise):
    """Résultats chiffrés d'un tronçon (pentes en %), orienté pour que l'altitude croisse avec l'abscisse."""
    p = profil_voie(pts, ligne)
    if p is None:
        return None
    s, z = p
    ok = ~np.isnan(z)
    a, b = np.polyfit(s[ok], z[ok], 1)
    if a < 0:                                        # on lit toujours dans le sens montant
        ligne = LineString(list(ligne.coords)[::-1])
        p = profil_voie(pts, ligne)
        s, z = p
        ok = ~np.isnan(z)
        a, b = np.polyfit(s[ok], z[ok], 1)
    n = int(FENETRE / PAS)
    locales = []
    for i in range(0, len(s) - n, n // 2):
        if not (np.isnan(z[i]) or np.isnan(z[i + n])):
            locales.append((float(s[i]), float(s[i + n]), float((z[i + n] - z[i]) / (s[i + n] - s[i]) * 100)))
    cap, cardinal = _cap(ligne)
    pb = ligne.project(emprise.centroid)
    proche = ligne.distance(emprise)
    return {"nom": nom, "ligne": ligne, "s": s, "z": z, "longueur_m": float(ligne.length),
            "z_min": float(np.nanmin(z)), "z_max": float(np.nanmax(z)),
            "pente_moyenne_pct": float(a * 100), "pente_max_pct": float(max((abs(l[2]) for l in locales), default=abs(a) * 100)),
            "pentes_locales": locales, "sens_montant": f"vers le {cardinal}", "cap_deg": float(cap),
            "s_batiment": float(pb) if proche < 40 else None, "distance_batiment_m": float(proche)}


def profils(pts, voies, emprise, zone_nuage):
    """`voies` : liste de (nom, géométrie L93). Un profil par tronçon dans la zone du nuage, du plus proche du bâtiment au plus loin."""
    out = []
    for nom, g in voies:
        morceaux = _morceaux(g, zone_nuage)
        for k, l in enumerate(sorted(morceaux, key=lambda x: x.distance(emprise)), 1):
            r = analyser(pts, nom if len(morceaux) == 1 else f"{nom} (tronçon {k})", l, emprise)
            if r:
                out.append(r)
    out.sort(key=lambda r: r["distance_batiment_m"])
    return out


def resume(res):
    return [{"voie": r["nom"], "longueur_m": round(r["longueur_m"], 1), "altitude_min_ngf": round(r["z_min"], 2),
             "altitude_max_ngf": round(r["z_max"], 2), "pente_moyenne_pct": round(r["pente_moyenne_pct"], 1),
             "pente_max_pct": round(r["pente_max_pct"], 1), "sens_montant": r["sens_montant"],
             "distance_batiment_m": round(r["distance_batiment_m"], 1),
             "abscisse_batiment_m": None if r["s_batiment"] is None else round(r["s_batiment"], 1)} for r in res]


def tracer(res, chemin):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = len(res)
    fig, axes = plt.subplots(n, 1, figsize=(13, 2.9 * n), squeeze=False)
    for ax, r in zip(axes[:, 0], res):
        ax.plot(r["s"], r["z"], "-", color="#8b5a2b", lw=2)
        lo, hi = np.nanmin(r["z"]), np.nanmax(r["z"])
        for a, b, pc in r["pentes_locales"]:
            ax.text((a + b) / 2, np.interp((a + b) / 2, r["s"][~np.isnan(r["z"])], r["z"][~np.isnan(r["z"])]) + 0.25,
                    f"{pc:.0f}%", ha="center", fontsize=7, color="#b00000" if abs(pc) >= 6 else "#444")
        if r["s_batiment"] is not None:
            ax.axvline(r["s_batiment"], color="r", ls="--", lw=1)
            ax.text(r["s_batiment"], lo - 0.25, " bâtiment", color="r", fontsize=8, va="bottom")
        ax.set_ylim(lo - 0.3, hi + 0.8)
        ax.set_title(f"{r['nom']} — pente moyenne {r['pente_moyenne_pct']:.1f} %, max {r['pente_max_pct']:.1f} % (par 10 m), "
                     f"montant {r['sens_montant']}", fontsize=10)
        ax.set_ylabel("altitude NGF (m)")
        ax.grid(alpha=.3)
    axes[-1, 0].set_xlabel("distance le long de l'axe de la voie, dans le sens montant (m) — échelle verticale exagérée")
    fig.tight_layout()
    fig.savefig(chemin, dpi=120)
    plt.close(fig)
    return chemin


def ecrire_dxf(res, chemin, ecart=15.0):
    """x = distance le long de l'axe (m), y = altitude NGF (m). Calques PR<n>_<VOIE>_PROFIL / PENTES / TEXTES, graduation tous les 10 m."""
    import ezdxf
    from .dwg import nom_calque
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    x_off = 0.0
    for i, r in enumerate(res, 1):
        base = f"PR{i}_{nom_calque(r['nom'])}"
        lay = {k: f"{base}_{k}" for k in ("PROFIL", "PENTES", "TEXTES")}
        for k, c in (("PROFIL", 3), ("PENTES", 1), ("TEXTES", 7)):
            doc.layers.add(lay[k], color=c)
        s, z = r["s"], r["z"]
        segs, cur = [], []
        for a, b in zip(s, z):
            if np.isnan(b):
                if len(cur) > 1:
                    segs.append(cur)
                cur = []
            else:
                cur.append((float(a) + x_off, float(b)))
        if len(cur) > 1:
            segs.append(cur)
        for sg in segs:
            msp.add_lwpolyline(sg, dxfattribs={"layer": lay["PROFIL"]})
        zb = float(np.nanmin(z)) - 1.0
        for a in np.arange(0, r["longueur_m"] + 0.1, 10.0):
            msp.add_line((a + x_off, zb), (a + x_off, zb + 0.4), dxfattribs={"layer": lay["TEXTES"]})
            msp.add_text(f"{a:.0f}", height=0.3, dxfattribs={"layer": lay["TEXTES"], "insert": (a + x_off - 0.3, zb - 0.6)})
        msp.add_line((x_off, zb), (x_off + r["longueur_m"], zb), dxfattribs={"layer": lay["TEXTES"]})
        okz = ~np.isnan(z)
        for a, b, pc in r["pentes_locales"]:
            za = float(np.interp((a + b) / 2, s[okz], z[okz]))
            msp.add_text(f"{pc:+.1f}%", height=0.3, dxfattribs={"layer": lay["PENTES"], "insert": ((a + b) / 2 + x_off - 0.8, za + 0.5)})
        if r["s_batiment"] is not None:
            xb = r["s_batiment"] + x_off
            zbat = float(np.interp(r["s_batiment"], s[okz], z[okz]))
            msp.add_line((xb, zbat), (xb, zbat + 3.0), dxfattribs={"layer": lay["TEXTES"]})
            msp.add_text(f"Bâtiment ({zbat:.2f} NGF)", height=0.35, dxfattribs={"layer": lay["TEXTES"], "insert": (xb + 0.3, zbat + 3.0)})
        msp.add_text(f"Profil en long — {r['nom']} : pente moyenne {r['pente_moyenne_pct']:.1f} %, max {r['pente_max_pct']:.1f} %, "
                     f"montant {r['sens_montant']}", height=0.5,
                     dxfattribs={"layer": lay["TEXTES"], "insert": (x_off, float(np.nanmax(z)) + 4.5)})
        x_off += r["longueur_m"] + ecart
    doc.saveas(chemin)
    return chemin
