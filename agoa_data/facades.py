"""Façades sur rue : longueur (BD TOPO / cadastre), hauteurs et profils (nuage LiDAR HD), niveaux estimés → élévation DXF + aperçu PNG.

Niveau 1 : dessin « enveloppe » (sol, nus de façade, ligne d'égout, silhouette du toit, niveaux estimés). Les ouvertures ne sont pas dessinées.
Repère de l'élévation : x = abscisse le long de la façade, de gauche à droite pour quelqu'un qui la regarde depuis la rue ; y = altitude NGF.
"""
import math
import re
from dataclasses import dataclass, field

import numpy as np
from shapely import contains_xy
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

HAUTEUR_ETAGE_DEFAUT = 2.9           # m, pour estimer le nombre de niveaux quand la BD TOPO ne le donne pas


@dataclass
class Facade:
    voie: str
    origine: tuple                   # (x, y) Lambert 93 : extrémité gauche de la façade, vue depuis la rue
    droite: tuple                    # vecteur unitaire le long de la façade, vers la droite
    sortant: tuple                   # vecteur unitaire vers la rue
    longueur: float
    front_parcelle: float | None = None
    r: dict = field(default_factory=dict)


def _poly(g):
    return max(g.geoms, key=lambda a: a.area) if g.geom_type == "MultiPolygon" else g


def _front_parcelle(parcelle, orig, droite, sortant):
    """Longueur (m) du front de parcelle sur rue : côtés de la parcelle parallèles à la façade (±12°), situés entre 1 m derrière
    et 8 m devant la façade, projetés sur l'axe de la façade (les recouvrements ne comptent qu'une fois)."""
    ring = list(_poly(parcelle).exterior.coords)
    plages = []
    for a, b in zip(ring[:-1], ring[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 0.5:
            continue
        if abs((dx * sortant[0] + dy * sortant[1]) / L) > 0.21:            # pas parallèle à la façade
            continue
        m = ((a[0] + b[0]) / 2 - orig[0], (a[1] + b[1]) / 2 - orig[1])
        decalage = m[0] * sortant[0] + m[1] * sortant[1]
        if not -1.0 <= decalage <= 8.0:
            continue
        ta = (a[0] - orig[0]) * droite[0] + (a[1] - orig[1]) * droite[1]
        tb = (b[0] - orig[0]) * droite[0] + (b[1] - orig[1]) * droite[1]
        plages.append((min(ta, tb), max(ta, tb)))
    total, fin = 0.0, -1e9
    for d, f in sorted(plages):
        if f > fin:
            total += f - max(d, fin)
            fin = f
    return float(total) if total > 0 else None


def definir_facades(emprise, parcelle, voies, rayon=4.0):
    """Une façade par voie riveraine : le côté de l'emprise qui lui fait face (pièces alignées regroupées)."""
    ring = list(orient(_poly(emprise), 1.0).exterior.coords)      # sens antihoraire : normale sortante = (dy, -dx)
    segs = []
    for a, b in zip(ring[:-1], ring[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L >= 1.0:
            segs.append((a, b, L, (dx / L, dy / L), (dy / L, -dx / L)))
    out = []
    for v in voies:
        cand = []
        for a, b, L, u, n in segs:
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            from shapely.geometry import Point
            p = Point(m)
            sp = v.geom.interpolate(v.geom.project(p))
            vers = (sp.x - m[0], sp.y - m[1])
            d = math.hypot(*vers)
            if d > 0 and (vers[0] * n[0] + vers[1] * n[1]) / d > 0.64:      # normale à moins de ~50° de la direction de la rue
                cand.append((d, a, b, L, u, n, m))
        if not cand:
            continue
        dmin = min(c[0] for c in cand)
        cand = [c for c in cand if c[0] <= dmin + rayon]
        ref = max(cand, key=lambda c: c[3])
        _, a0, b0, L0, u0, n0, m0 = ref
        groupe = []
        for d, a, b, L, u, n, m in cand:
            ang = abs(u[0] * u0[1] - u[1] * u0[0])                       # sin de l'écart angulaire
            decalage = abs((m[0] - m0[0]) * n0[0] + (m[1] - m0[1]) * n0[1])
            if ang < 0.21 and decalage <= 1.5:
                groupe.append((a, b))
        ts = [((p[0] - m0[0]) * u0[0] + (p[1] - m0[1]) * u0[1]) for a, b in groupe for p in (a, b)]
        # « droite » pour un observateur dans la rue qui regarde la façade (direction -n) : (dy, -dx) de -n
        droite = (-n0[1], n0[0])
        if droite[0] * u0[0] + droite[1] * u0[1] < 0:
            ts = [-t for t in ts]
        tmin, tmax = min(ts), max(ts)
        orig = (m0[0] + droite[0] * tmin, m0[1] + droite[1] * tmin)
        front = None
        if parcelle is not None:
            front = _front_parcelle(parcelle, orig, droite, n0)
        out.append(Facade(v.nom, orig, droite, n0, float(tmax - tmin), front))
    return out


def _lisser(t, z, pas):
    """Série (t, z) à pas régulier : valeurs manquantes interpolées ; None si trop peu de mesures."""
    z = np.asarray(z, float)
    ok = ~np.isnan(z)
    if ok.sum() < 3:
        return None
    return np.interp(t, t[ok], z[ok])


def profils(pts, f, emprise, sol_global, etages=None, pas=0.5):
    """Remplit f.r avec le sol, la ligne d'égout et la silhouette du toit le long de la façade, d'après le nuage (x, y, z, classe)."""
    ox, oy = f.origine
    rx, ry = f.droite
    nx, ny = f.sortant
    X, Y, Z, C = pts[:, 0], pts[:, 1], pts[:, 2], pts[:, 3]
    t = (X - ox) * rx + (Y - oy) * ry              # le long de la façade
    s = (X - ox) * nx + (Y - oy) * ny              # vers la rue (>0 dehors, <0 dans le bâtiment)
    L = f.longueur
    T = np.arange(0.0, L + 1e-6, pas)
    centres = T
    dedans = contains_xy(emprise.buffer(0.3), X, Y)

    sol = np.full(len(T), np.nan)
    m_sol = (C == 2) & (s > 0.3) & (s < 6.0) & (t > -1) & (t < L + 1)
    for i, c in enumerate(centres):
        sel = m_sol & (np.abs(t - c) < 1.0)
        if sel.sum() >= 3:
            sol[i] = np.median(Z[sel])
    sol_i = _lisser(T, sol, pas)
    sol_i = sol_i if sol_i is not None else np.full(len(T), sol_global)
    sol_med = float(np.median(sol_i))

    eg = np.full(len(T), np.nan)
    m_toit = (C == 6) & dedans & (s < -0.3) & (s > -4.5) & (t > -0.5) & (t < L + 0.5) & (Z > sol_med + 2.5)
    for i, c in enumerate(centres):
        sel = m_toit & (np.abs(t - c) < 1.5)
        if sel.sum() >= 8 and np.ptp(s[sel]) > 1.0:
            a, b = np.polyfit(-s[sel], Z[sel], 1)   # z = b + a·profondeur ; on revient au nu de façade (profondeur 0)
            res = Z[sel] - (a * -s[sel] + b)
            bon = np.abs(res) < max(0.25, 2.5 * np.std(res))
            if bon.sum() >= 6:
                a, b = np.polyfit(-s[sel][bon], Z[sel][bon], 1)
            eg[i] = b
        elif sel.sum() >= 4:
            eg[i] = float(np.median(Z[sel]))
    eg_i = _lisser(T, eg, pas)

    sil = np.full(len(T), np.nan)
    m_sil = (C == 6) & dedans & (s < 0.2) & (s > -20.0) & (t > -0.5) & (t < L + 0.5) & (Z > sol_med + 2.5)
    for i, c in enumerate(centres):
        sel = m_sil & (np.abs(t - c) < 0.75)
        if sel.sum() >= 4:
            sil[i] = float(np.percentile(Z[sel], 95))
    sil_i = _lisser(T, sil, pas)

    r = {"abscisses": T, "sol": sol_i, "sol_med": sol_med}
    if eg_i is not None:
        r["egout"] = eg_i
        r["egout_med"] = float(np.median(eg_i))
        r["egout_h"] = r["egout_med"] - sol_med
    if sil_i is not None:
        r["silhouette"] = sil_i
        r["faitage"] = float(sil_i.max())
        r["faitage_h"] = r["faitage"] - sol_med
    haut = r.get("egout_h")
    if haut:
        n = etages if etages and etages > 0 else max(1, round(haut / HAUTEUR_ETAGE_DEFAUT))
        r["niveaux_n"] = int(n)
        r["niveaux_source"] = "BD TOPO" if etages and etages > 0 else "estimé"
        r["niveaux"] = [sol_med + k * haut / n for k in range(1, int(n))]      # planchers intermédiaires, répartition régulière
    f.r = r
    return r


def definir_niveaux(f, n_visibles, h_rdc=None):
    """Répartit la hauteur sol → égout : un RDC (hauteur `h_rdc`, sinon estimée) puis `n_visibles` niveaux courants égaux.
    `n_visibles` = niveaux au-dessus du RDC visibles depuis la rue (les attiques en retrait n'en font pas partie).
    Renseigne r["niveaux"] (altitudes des planchers R+1 … R+n), r["h_rdc"], r["h_niveau"]."""
    r = f.r
    if "egout_med" not in r or n_visibles is None or n_visibles < 1:
        return None
    H = r["egout_med"] - r["sol_med"]
    h0 = h_rdc if h_rdc else min(max(H / (n_visibles + 1) * 1.05, 2.6), 4.8)
    hn = (H - h0) / n_visibles
    r["h_rdc"] = float(h0)
    r["h_niveau"] = float(hn)
    r["niveaux_n"] = int(n_visibles) + 1
    r["niveaux_source"] = "saisi"
    r["niveaux"] = [r["sol_med"] + h0 + k * hn for k in range(n_visibles)]
    return r["niveaux"]


def resume(f):
    """Dictionnaire sérialisable (JSON) des résultats d'une façade."""
    r = f.r
    d = {"voie": f.voie, "longueur_m": round(f.longueur, 2),
         "front_parcelle_m": round(f.front_parcelle, 2) if f.front_parcelle else None}
    for k in ("sol_med", "egout_med", "egout_h", "faitage", "faitage_h", "niveaux_n", "niveaux_source", "h_rdc", "h_niveau"):
        if k in r:
            d[k] = round(r[k], 2) if isinstance(r[k], float) else r[k]
    return d


def tracer_facade(f, chemin):
    """Aperçu PNG de l'élévation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    r = f.r
    T = r["abscisses"]
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=110)
    ax.plot(T, r["sol"], color="#6b5b3e", lw=1.4, label="sol")
    if "egout" in r:
        ax.plot(T, r["egout"], color="#111", lw=1.6, label="égout")
        for x in (0, f.longueur):
            ax.plot([x, x], [np.interp(x, T, r["sol"]), np.interp(x, T, r["egout"])], color="#111", lw=1.6)
    if "silhouette" in r:
        ax.plot(T, r["silhouette"], color="#B3261E", lw=1.2, label="silhouette du toit")
    for k, z in enumerate(r.get("niveaux", []), 1):
        ax.plot([0, f.longueur], [z, z], color="#0b7285", lw=0.8, ls="--")
        ax.text(f.longueur + 0.3, z, f"R+{k}  {z:.2f}", fontsize=7, color="#0b7285", va="center")
    ax.set_aspect("equal")
    ax.set_xlabel("abscisse le long de la façade (m), de gauche à droite depuis la rue")
    ax.set_ylabel("altitude NGF (m)")
    t = f"Façade sur {f.voie} — longueur {f.longueur:.2f} m"
    if f.front_parcelle:
        t += f" (front de parcelle ≈ {f.front_parcelle:.1f} m)"
    if "egout_h" in r:
        t += f"\nsol {r['sol_med']:.2f} | égout {r['egout_med']:.2f} ({r['egout_h']:.1f} m)"
    if "faitage_h" in r:
        t += f" | point haut {r['faitage']:.2f} ({r['faitage_h']:.1f} m) | {r['niveaux_n']} niveaux ({r['niveaux_source']})"
    ax.set_title(t, fontsize=9)
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(chemin)
    plt.close(fig)


def ecrire_facades_dxf(facades, chemin, ecart=12.0):
    """Élévations côte à côte (x = abscisse le long de la façade, y = altitude NGF) : calques par façade
    F<n>_<VOIE>_SOL / _FACADE / _TOITURE / _NIVEAUX / _COTES / _TEXTES."""
    import ezdxf
    from .dwg import nom_calque
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    x0 = 0.0
    for i, f in enumerate(facades, 1):
        r = f.r
        if "abscisses" not in r:
            continue
        base = f"F{i}_{nom_calque(f.voie)}"
        for k, c in (("SOL", 8), ("FACADE", 7), ("TOITURE", 1), ("NIVEAUX", 4), ("COTES", 3), ("TEXTES", 7), ("BAIES", 5), ("BALCONS", 6), ("RDC", 2)):
            doc.layers.add(f"{base}_{k}", color=c)
        T, sol = r["abscisses"], r["sol"]
        ext = np.concatenate([[-2.0], T, [f.longueur + 2.0]])
        msp.add_lwpolyline([(x0 + float(a), float(b)) for a, b in zip(ext, np.concatenate([[sol[0]], sol, [sol[-1]]]))],
                           dxfattribs={"layer": f"{base}_SOL"})
        if "egout" in r:
            eg = r["egout"]
            msp.add_lwpolyline([(x0 + float(a), float(b)) for a, b in zip(T, eg)], dxfattribs={"layer": f"{base}_FACADE"})
            for x, j in ((0.0, 0), (f.longueur, -1)):
                msp.add_line((x0 + x, float(sol[j])), (x0 + x, float(eg[j])), dxfattribs={"layer": f"{base}_FACADE"})
            msp.add_line((x0, float(sol[0])), (x0 + f.longueur, float(sol[-1])), dxfattribs={"layer": f"{base}_FACADE"})
        if "silhouette" in r:
            msp.add_lwpolyline([(x0 + float(a), float(b)) for a, b in zip(T, r["silhouette"])], dxfattribs={"layer": f"{base}_TOITURE"})
        for k, z in enumerate(r.get("niveaux", []), 1):
            msp.add_line((x0, float(z)), (x0 + f.longueur, float(z)), dxfattribs={"layer": f"{base}_NIVEAUX", "linetype": "DASHED"})
            msp.add_text(f"R+{k} ({'saisi' if r.get('niveaux_source') == 'saisi' else 'estime'}) {z:.2f} NGF", height=0.3, dxfattribs={"layer": f"{base}_TEXTES", "insert": (x0 + f.longueur + 0.5, float(z))})
        for o in r.get("ouvertures", []):
            x, w_, zb, hh = x0 + o["x"], o["largeur"], o["z_bas"], o["hauteur"]
            msp.add_lwpolyline([(x, zb), (x + w_, zb), (x + w_, zb + hh), (x, zb + hh)], close=True,
                               dxfattribs={"layer": f"{base}_" + {"balcon": "BALCONS", "rdc": "RDC"}.get(o["type"], "BAIES")})
        yb = float(sol.min()) - 2.0
        try:                                                           # cote de longueur (vraie entité de cote)
            d = msp.add_linear_dim(base=(x0, yb), p1=(x0, yb + 1.0), p2=(x0 + f.longueur, yb + 1.0),
                                   dimstyle="EZDXF", override={"dimtxt": 0.35}, dxfattribs={"layer": f"{base}_COTES"})
            d.render()
        except Exception:                                              # noqa: BLE001 — repli : trait et texte
            msp.add_line((x0, yb), (x0 + f.longueur, yb), dxfattribs={"layer": f"{base}_COTES"})
            msp.add_text(f"{f.longueur:.2f} m", height=0.35, dxfattribs={"layer": f"{base}_COTES", "insert": (x0 + f.longueur / 2, yb + 0.2)})
        xr = x0 + f.longueur + 6.0
        for lib, a, b in (("Hauteur sol-egout", r["sol_med"], r.get("egout_med")), ("Hauteur sol-point haut", r["sol_med"], r.get("faitage"))):
            if b is not None:
                msp.add_text(f"{lib} {b - a:.2f} m", height=0.3, dxfattribs={"layer": f"{base}_TEXTES", "insert": (xr, a + (b - a) / 2 - (0.6 if "point" in lib else 0))})
        titre = f"Facade sur {f.voie} - longueur BD TOPO {f.longueur:.2f} m"
        if f.front_parcelle:
            titre += f" (front de parcelle {f.front_parcelle:.1f} m)"
        msp.add_text(titre, height=0.6, dxfattribs={"layer": f"{base}_TEXTES",
                                                    "insert": (x0, float(max(r.get("faitage", 0), r.get("egout_med", 0))) + 2.5)})
        msp.add_text("Niveau 2 : forme generale (niveaux, rythme des baies, RDC) d'apres photos - a valider" if r.get("ouvertures") else "Niveau 1 : enveloppe et niveaux estimes, sans ouvertures", height=0.3,
                     dxfattribs={"layer": f"{base}_TEXTES", "insert": (x0, float(max(r.get("faitage", 0), r.get("egout_med", 0))) + 1.8)})
        x0 += f.longueur + ecart + 14.0
    doc.saveas(chemin)
    return chemin
