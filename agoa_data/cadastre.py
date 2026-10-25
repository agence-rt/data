"""Plan cadastral au format PDF (vectoriel, A4 paysage, échelle normalisée).

Le plan est dessiné à partir des données ouvertes de la Géoplateforme (Parcellaire Express, BD TOPO).
Ce n'est pas l'extrait officiel de la DGFiP : celui-ci s'obtient sur https://www.cadastre.gouv.fr.
"""
import datetime
import math
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                                    # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages               # noqa: E402
from matplotlib.patches import PathPatch   # noqa: E402
from matplotlib.path import Path                                   # noqa: E402
from shapely.geometry import box                                   # noqa: E402

from . import geo                                                  # noqa: E402

ECHELLES = (200, 250, 500, 1000, 2000, 5000)
PAGE_MM = (297.0, 210.0)
CARTE_MM = (277.0, 158.0)             # zone de carte (marges 10 mm ; cartouche en dessous)
CARTE_ORIGINE_MM = (10.0, 40.0)
RED = "#B3261E"
INK = "#111111"


def choisir_echelle(largeur_m, hauteur_m):
    """Plus grande échelle normalisée (1/200 … 1/5000) pour laquelle l'emprise tient sur la carte."""
    for e in ECHELLES:
        if largeur_m <= CARTE_MM[0] / 1000 * e and hauteur_m <= CARTE_MM[1] / 1000 * e:
            return e
    return ECHELLES[-1]


def fenetre(parcelle, echelle=None, marge=15.0):
    """(centre_x, centre_y, demi_largeur_m, demi_hauteur_m, échelle) de la carte autour de la parcelle."""
    x0, y0, x1, y1 = parcelle.geom.bounds
    e = echelle or choisir_echelle(x1 - x0 + 2 * marge, y1 - y0 + 2 * marge)
    return (x0 + x1) / 2, (y0 + y1) / 2, CARTE_MM[0] / 2000 * e, CARTE_MM[1] / 2000 * e, e


def _patch(poly, **kw):
    """Polygone shapely (avec trous) → patch matplotlib."""
    verts, codes = [], []
    for ring in [poly.exterior, *poly.interiors]:
        c = list(ring.coords)
        verts += c
        codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
    return PathPatch(Path(verts, codes), **kw)


def _barre_echelle(ax, echelle, x, y, cx, cy):
    longueur = next((n for n in (1, 2, 5, 10, 20, 50, 100, 200, 500) if n / echelle * 1000 >= 25), 500)
    ax.plot([x, x + longueur], [y, y], color=INK, lw=1.6, solid_capstyle="butt", zorder=9)
    for k in (0, longueur):
        ax.plot([x + k, x + k], [y - 0.0015 * echelle, y + 0.0015 * echelle], color=INK, lw=0.9, zorder=9)
    ax.text(x + longueur / 2, y + 0.003 * echelle, f"{longueur} m", ha="center", va="bottom", fontsize=7, zorder=9)


def _fleche_nord(ax, x, y, echelle):
    """Flèche de 10 mm de long sur le papier, base en (x, y) ; nord = haut de la feuille (Lambert 93)."""
    mm = echelle / 1000
    ax.annotate("", xy=(x, y + 10 * mm), xytext=(x, y), zorder=9,
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2, mutation_scale=11))
    ax.text(x, y + 11 * mm, "N", ha="center", va="bottom", fontsize=9, fontweight="bold", zorder=9)


def tracer_plan_cadastral(adr, parcelle, chemin_pdf, voisines=None, batiments=None, voies=None, echelle=None,
                          date=None):
    """Écrit le plan cadastral PDF de la parcelle et renvoie l'échelle retenue (dénominateur).

    `voisines`, `batiments`, `voies` peuvent être fournis (tests) ; sinon ils sont lus sur la Géoplateforme.
    """
    cx, cy, dx, dy, e = fenetre(parcelle, echelle)
    zone = box(cx - dx, cy - dy, cx + dx, cy + dy)
    if voisines is None:
        voisines = geo.parcelles_dans(zone)
    if batiments is None:
        batiments = geo.batiments_dans(zone)
    if voies is None:
        voies = geo.voies_dans(zone)

    fig = plt.figure(figsize=(PAGE_MM[0] / 25.4, PAGE_MM[1] / 25.4))
    L, B = CARTE_ORIGINE_MM
    ax = fig.add_axes([L / PAGE_MM[0], B / PAGE_MM[1], CARTE_MM[0] / PAGE_MM[0], CARTE_MM[1] / PAGE_MM[1]])
    ax.set_xlim(cx - dx, cx + dx)
    ax.set_ylim(cy - dy, cy + dy)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_linewidth(1.0)

    # bâtiments (aplat gris), parcelles (traits), parcelle visée (rouge)
    for b in batiments:
        ax.add_patch(_patch(b, facecolor="#D9D7CF", edgecolor="none", zorder=1))
    cible = (parcelle.section, parcelle.numero)
    taille = 7.0 if e <= 500 else 6.0
    for p in voisines:
        est_cible = (p.section, p.numero) == cible and p.geom.distance(parcelle.geom) < 0.5
        if est_cible:
            continue
        ax.add_patch(_patch(p.geom, facecolor="none", edgecolor="#555555", lw=0.5, zorder=3))
    ax.add_patch(_patch(parcelle.geom, facecolor=(0.70, 0.15, 0.12, 0.14), edgecolor=RED, lw=1.6, zorder=4))

    # numéros de parcelles (si la place le permet)
    for p in voisines:
        pt = p.geom.representative_point()
        if not zone.contains(pt):
            continue
        est_cible = p.geom.distance(parcelle.geom) < 0.5 and (p.section, p.numero) == cible
        largeur = p.geom.bounds[2] - p.geom.bounds[0]
        if largeur / e * 1000 < 4 and not est_cible:        # < 4 mm sur le papier : illisible
            continue
        ax.text(pt.x, pt.y, p.numero, ha="center", va="center", fontsize=taille + (2 if est_cible else 0),
                color=RED if est_cible else "#333333", fontweight="bold" if est_cible else "normal", zorder=6,
                clip_on=True)

    # sections (lettres en grand, si plusieurs sections visibles)
    sections = {}
    for p in voisines:
        sections.setdefault(p.section, []).append(p.geom)
    if len(sections) > 1:
        from shapely.ops import unary_union
        for s, gs in sections.items():
            c = unary_union(gs).intersection(zone).representative_point()
            ax.text(c.x, c.y, s, ha="center", va="center", fontsize=40, color="#000000", alpha=0.07, zorder=0)

    # noms des voies, le long de la voie
    for nom, g in voies:
        g = g.intersection(zone)
        if g.is_empty:
            continue
        lignes = list(g.geoms) if g.geom_type.startswith("Multi") else [g]
        ligne = max(lignes, key=lambda a: a.length)
        if ligne.length < 8 or ligne.geom_type != "LineString":
            continue
        ax.plot(*ligne.xy, color="#9A9890", lw=0.6, ls=(0, (6, 3)), zorder=2)
        a, b = ligne.interpolate(0.45, normalized=True), ligne.interpolate(0.55, normalized=True)
        ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
        if ang > 90:
            ang -= 180
        elif ang < -90:
            ang += 180
        m = ligne.interpolate(0.5, normalized=True)
        ax.text(m.x, m.y, nom.upper(), rotation=ang, rotation_mode="anchor", ha="center", va="bottom", fontsize=7,
                color="#444444", zorder=7, clip_on=True,
                bbox=dict(boxstyle="square,pad=0.1", fc="white", ec="none", alpha=0.7))

    _barre_echelle(ax, e, cx - dx + 0.03 * e, cy - dy + 0.012 * e, cx, cy)
    _fleche_nord(ax, cx + dx - 0.012 * e, cy + dy - 0.026 * e, e)

    # cartouche
    date = date or datetime.date.today().strftime("%d/%m/%Y")
    lc = L / PAGE_MM[0]

    def txt(x_mm, y_mm, s, **kw):
        fig.text(x_mm / PAGE_MM[0], y_mm / PAGE_MM[1], s, **kw)

    fig.add_artist(plt.Rectangle((lc, 8 / PAGE_MM[1]), CARTE_MM[0] / PAGE_MM[0], 28 / PAGE_MM[1], fill=False,
                                 lw=1.0, ec=INK, transform=fig.transFigure))
    txt(14, 29, "PLAN CADASTRAL", fontsize=14, fontweight="bold", color=RED, va="center")
    txt(14, 22, adr.label, fontsize=11, fontweight="bold", va="center")
    txt(14, 16, f"Parcelle {parcelle.section} {parcelle.numero}  ·  identifiant {parcelle.idu}  ·  "
                f"contenance {parcelle.contenance:,.0f} m²".replace(",", " "), fontsize=9, va="center")
    txt(14, 11, "Dessiné d'après le Parcellaire Express et la BD TOPO (IGN) — Licence Ouverte 2.0. "
                "Document d'information : l'extrait officiel est délivré par la DGFiP (cadastre.gouv.fr).",
        fontsize=6.5, color="#555555", va="center")
    txt(283, 29, f"Échelle 1/{e}", fontsize=13, fontweight="bold", ha="right", va="center")
    txt(283, 22, f"Format A4 — {date}", fontsize=9, ha="right", va="center")
    txt(283, 16, "Projection Lambert 93 (EPSG:2154)", fontsize=8, ha="right", va="center", color="#555555")
    txt(283, 11, "AGOA DATA", fontsize=8, ha="right", va="center", color=RED, fontweight="bold")

    with PdfPages(chemin_pdf) as pdf:
        pdf.infodict().update(Title=f"Plan cadastral — {adr.label}", Author="AGOA DATA", Subject=f"Parcelle {parcelle.idu}")
        pdf.savefig(fig)
    plt.close(fig)
    return e


def nom_fichier(parcelle):
    return "plan_cadastral_" + (re.sub(r"[^A-Za-z0-9]+", "", parcelle.idu) or f"{parcelle.section}{parcelle.numero}") + ".pdf"
