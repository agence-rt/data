"""Coupes dans le nuage de points : une coupe perpendiculaire à chaque voie qui borde le bâtiment
(+ une coupe parallèle à la voie principale), avec lecture automatique de l'égout et du faîtage."""
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString
from shapely.ops import nearest_points
from shapely import contains_xy

LARGEUR_BANDE = 1.5     # m : épaisseur de la tranche de nuage projetée sur la coupe


@dataclass
class Coupe:
    nom: str                 # « Rue Championnet »
    type: str                # "perpendiculaire" | "parallele"
    origine: tuple           # point de la voie (x, y)
    u: tuple                 # direction de lecture (sens des distances croissantes)
    n: tuple                 # normale à la tranche
    trace: LineString        # tracé pour le plan
    voie_distance: float
    r: dict = field(default_factory=dict)   # résultats chiffrés


def _unit(v):
    v = np.asarray(v, float)
    return v / np.hypot(*v)


def definir_coupes(emprise, voies, avec_parallele=True):
    """Une coupe par voie (perpendiculaire à la voie, au droit de la façade qui lui fait face)."""
    coupes = []
    for v in voies:
        g = v.geom
        # milieu de la partie de façade la plus proche de la voie
        proche = emprise.boundary.intersection(g.buffer(v.distance + 2.5))
        cen = proche.centroid if not proche.is_empty else nearest_points(g, emprise)[1]
        s = g.project(cen)
        pc = g.interpolate(s)
        a, b = g.interpolate(max(s - 4, 0)), g.interpolate(min(s + 4, g.length))
        tg = _unit([b.x - a.x, b.y - a.y])
        n = np.array([-tg[1], tg[0]])
        if np.dot(n, [cen.x - pc.x, cen.y - pc.y]) < 0:
            n = -n
        P0 = np.array([pc.x, pc.y])
        trace = LineString([P0 - n * 3, P0 + n * 38])
        coupes.append(Coupe(v.nom, "perpendiculaire", tuple(P0), tuple(n), tuple(tg), trace, v.distance))
    if avec_parallele and voies:
        v = voies[0]
        g = v.geom
        s = g.project(emprise.centroid)
        a, b = g.interpolate(max(s - 4, 0)), g.interpolate(min(s + 4, g.length))
        tg = _unit([b.x - a.x, b.y - a.y])
        n = np.array([-tg[1], tg[0]])
        c = np.array([emprise.centroid.x, emprise.centroid.y])
        trace = LineString([c - tg * 45, c + tg * 45])
        coupes.append(Coupe(v.nom, "parallele", tuple(c), tuple(tg), tuple(n), trace, v.distance))
    return coupes


def _repere(pts, coupe):
    P0 = np.array(coupe.origine)
    u = np.array(coupe.u)       # sens de la coupe (abscisse)
    n = np.array(coupe.n)       # normale à la tranche
    dx, dy = pts[:, 0] - P0[0], pts[:, 1] - P0[1]
    return dx * u[0] + dy * u[1], dx * n[0] + dy * n[1]


def _intervalle_batiment(emprise, coupe):
    P0, u = np.array(coupe.origine), np.array(coupe.u)
    seg = emprise.intersection(LineString([P0 - u * 80, P0 + u * 80]))
    gs = [seg] if seg.geom_type == "LineString" else [g for g in getattr(seg, "geoms", []) if g.geom_type == "LineString"]
    iv = sorted((min(np.dot(np.array(p) - P0, u) for p in g.coords),
                 max(np.dot(np.array(p) - P0, u) for p in g.coords)) for g in gs if not g.is_empty)
    return iv


def _point_haut(q, z, pas=0.5, minimum=3):
    """Point haut du toit, robuste aux souches et antennes : maximum des médianes par tranche de 0,5 m."""
    return _faitage(q, z, pas, minimum)[0]


def _faitage(q, z, pas=0.5, minimum=3):
    """(altitude, abscisse) du point haut : maximum des médianes par tranche de 0,5 m."""
    bins = np.floor((q - q.min()) / pas).astype(int)
    tr = [(np.median(z[bins == b]), np.median(q[bins == b])) for b in np.unique(bins) if (bins == b).sum() >= minimum]
    if not tr:
        return float(np.percentile(z, 90)), float(np.median(q))
    z0, q0 = max(tr)
    return float(z0), float(q0)


def _pente(q, z, egout, q_in, q_faitage):
    """Pente indicative du versant côté voie, du nu de façade au faîtage : droite ajustée en écartant
    les points aberrants (souches, annexes basses). None si le versant est trop court pour être mesuré."""
    m = (z > egout - 1.5) & (q >= q_in) & (q <= q_faitage + 0.25)    # écarte annexes basses et second versant
    if m.sum() < 12 or np.ptp(q[m]) < 2.0:
        return None
    qq, zz = q[m], z[m]
    keep = np.ones(len(qq), bool)
    for _ in range(4):
        a, b = np.polyfit(qq[keep], zz[keep], 1)
        res = np.abs(zz - (a * qq + b))
        nk = res < max(0.35, 2 * np.std(res[keep]))
        if nk.sum() < 10 or (nk == keep).all():
            break
        keep = nk
    return float(np.degrees(np.arctan(abs(a)))), float(a), float(b)


def analyser_coupe(pts, emprise, coupe, sol_global):
    """Remplit coupe.r et renvoie les points de la tranche (abscisse, altitude, classe)."""
    q, t = _repere(pts, coupe)
    tranche = np.abs(t) < LARGEUR_BANDE / 2
    iv = _intervalle_batiment(emprise, coupe)
    r = {"batiment_traverse": bool(iv)}
    if not iv:
        coupe.r = r
        return q[tranche], pts[tranche, 2], pts[tranche, 3]
    q_in, q_out = iv[0][0], iv[-1][1]
    sol_m = tranche & (pts[:, 3] == 2)
    if coupe.type == "perpendiculaire":
        pied = sol_m & (q < q_in - 0.3) & (q > q_in - 6)
    else:
        pied = sol_m & (q > q_in - 3) & (q < q_out + 3)
    sol = float(np.median(pts[pied, 2])) if pied.sum() > 3 else (
        float(np.median(pts[sol_m, 2])) if sol_m.sum() > 3 else sol_global)
    dedans = contains_xy(emprise.buffer(0.3), pts[:, 0], pts[:, 1])
    toit_m = tranche & dedans & (pts[:, 3] == 6)      # classe « bâtiment » uniquement (les non classés incluent souches et antennes)
    # on écarte les points de façade (verticale) : 0,6 m autour des limites de l'emprise
    for a, b in iv:
        toit_m &= ~(((q > a - 0.1) & (q < a + 0.6)) | ((q > b - 0.6) & (q < b + 0.1)))
    toit_m &= pts[:, 2] > sol + 2.5
    qt, zt = q[toit_m], pts[toit_m, 2]
    r.update(q_in=float(q_in), q_out=float(q_out), sol=sol, largeur_batiment=float(sum(b - a for a, b in iv)))
    if len(zt) >= 5:
        faitage, q_faitage = _faitage(qt, zt)
        r.update(faitage=faitage, faitage_h=faitage - sol, point_haut=float(zt.max()), n_points_toit=int(len(zt)))
        if coupe.type == "perpendiculaire":
            pres = (qt > q_in) & (qt < q_in + 2.5)
            egout = float(np.median(zt[pres])) if pres.sum() >= 2 else float(np.percentile(zt, 10))
            fit = _pente(qt, zt, egout, q_in, q_faitage)
            if fit is not None:
                r["pente_deg"] = fit[0]
                egout = fit[1] * q_in + fit[2]      # le versant prolongé jusqu'au nu de façade
            r.update(egout=egout, egout_h=egout - sol)
    coupe.r = r
    return q[tranche], pts[tranche, 2], pts[tranche, 3]


def tracer_coupe(coupe, q, z, c, emprise, chemin):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .lidar import CLASSES
    r = coupe.r
    sol = r.get("sol", float(np.median(z[c == 2])) if (c == 2).any() else float(z.min()))
    couleurs = {1: "#999999", 2: "#8b5a2b", 3: "#66aa66", 4: "#66aa66", 5: "#22aa22", 6: "#dd3333", 65: "#bbbbbb"}
    fig, ax = plt.subplots(figsize=(13, 6.5))
    if coupe.type == "perpendiculaire":
        xmin, xmax = -3, (r.get("q_out", 20) + 8)
    else:
        xmin, xmax = (r.get("q_in", -20) - 8), (r.get("q_out", 20) + 8)
    ok = (q > xmin) & (q < xmax)
    for k in np.unique(c[ok]):
        m = ok & (c == k)
        ax.scatter(q[m], z[m] - sol, s=4, c=couleurs.get(int(k), "k"), label=CLASSES.get(int(k), f"classe {int(k)}"))
    if r.get("batiment_traverse"):
        for a, b in _intervalle_batiment(emprise, coupe):
            ax.axvspan(a, b, color="#f5c6c6", alpha=.25, lw=0)
        for cle, lib in (("egout", "égout"), ("faitage", "faîtage")):
            if cle in r:
                ax.axhline(r[cle + "_h"], color="#333", ls=":", lw=1)
                ax.text(xmin + .2, r[cle + "_h"] + .15, f"{lib} {r[cle + '_h']:.1f} m ({r[cle]:.1f} NGF)",
                        ha="left", fontsize=9, bbox=dict(fc="white", ec="none", alpha=.8, pad=1))
    if coupe.type == "perpendiculaire":
        ax.axvline(0, color="b", ls="--", lw=1)
        ax.text(.15, ax.get_ylim()[1] * .96, f"axe de la {coupe.nom}", color="b", fontsize=9, va="top")
        titre = f"Coupe perpendiculaire à la {coupe.nom}"
        xl = f"distance depuis l'axe de la {coupe.nom} (m)"
    else:
        titre = f"Coupe parallèle à la {coupe.nom}"
        xl = f"distance le long de la {coupe.nom} (m)"
    ax.axhline(0, c="k", lw=.6)
    ax.set_aspect("equal")
    ax.grid(alpha=.3)
    ax.set_title(f"{titre} — tranche de {LARGEUR_BANDE:g} m (zone rose = emprise du bâtiment)")
    ax.set_xlabel(xl)
    ax.set_ylabel(f"hauteur / sol au pied de façade ({sol:.1f} m NGF)")
    ax.legend(markerscale=3, fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    fig.tight_layout()
    fig.savefig(chemin, dpi=130)
    plt.close(fig)
    return chemin


def tracer_plan(pts, emprise, parcelle, voies, coupes, chemin, marge=40):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x0, y0, x1, y1 = emprise.buffer(marge).bounds
    res = 0.5
    nx, ny = int((x1 - x0) / res) + 1, int((y1 - y0) / res) + 1
    H = np.full((ny, nx), np.nan)
    ix = np.clip(((pts[:, 0] - x0) / res).astype(int), 0, nx - 1)
    iy = np.clip(((pts[:, 1] - y0) / res).astype(int), 0, ny - 1)
    np.fmax.at(H, (iy, ix), pts[:, 2])
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(H, extent=[x0, x1, y0, y1], origin="lower", cmap="Greys", alpha=.85)
    for g in ([parcelle.geom] if parcelle else []):
        ax.plot(*g.exterior.xy, color="#e0a000", lw=1.2, ls="--")
    ax.plot(*emprise.exterior.xy, "r-", lw=1.8)
    for v in voies:
        for g in (v.geom.geoms if v.geom.geom_type == "MultiLineString" else [v.geom]):
            ax.plot(*g.xy, "b-", lw=3, alpha=.55)
        p = v.geom.interpolate(v.geom.project(emprise.centroid))
        ax.text(p.x - 8, p.y - 6, v.nom, color="b", fontsize=10, fontweight="bold")
    cols = ["#e08000", "#008060", "#8000a0", "#0070c0"]
    for k, c in enumerate(coupes):
        ax.plot(*c.trace.xy, "-", color=cols[k % 4], lw=2)
        e = c.trace.coords[-1]
        ax.text(e[0], e[1], f"coupe {'perp.' if c.type == 'perpendiculaire' else 'parallèle'} {c.nom}", fontsize=9)
    cx, cy = emprise.centroid.x, emprise.centroid.y
    ax.set_xlim(cx - 60, cx + 60)
    ax.set_ylim(cy - 50, cy + 50)
    ax.set_aspect("equal")
    ax.set_title("Plan de situation des coupes (fond : altitude maximale du nuage ; pointillé : parcelle)")
    fig.tight_layout()
    fig.savefig(chemin, dpi=130)
    plt.close(fig)
    return chemin
