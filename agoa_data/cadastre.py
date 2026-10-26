"""Plans cadastraux PDF vectoriels, A3 paysage, aux échelles 1/500, 1/1000 et 1/2000.

Pas de mise en page : la feuille est entièrement occupée par le plan, centré sur la parcelle. Le dessin est
purement vectoriel (lignes et polygones sans remplissage, textes en vrais textes) pour être récupéré dans un
logiciel de dessin / DAO : 1 mm sur la feuille = (échelle / 1000) m sur le terrain, et le plan est en Lambert 93.
Données : Parcellaire Express et BD TOPO (IGN, Licence Ouverte 2.0) — ce n'est pas l'extrait officiel DGFiP.
"""
import math
import re

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt                                    # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages               # noqa: E402
from matplotlib.patches import PathPatch                           # noqa: E402
from matplotlib.path import Path                                   # noqa: E402
from shapely.geometry import box                                   # noqa: E402

from . import geo                                                  # noqa: E402

ECHELLES = (500, 1000, 2000)
A3_MM = (420.0, 297.0)
RED = "#B3261E"


def fenetre(parcelle, echelle):
    """(centre_x, centre_y, demi_largeur_m, demi_hauteur_m) de la feuille A3 centrée sur la parcelle."""
    x0, y0, x1, y1 = parcelle.geom.bounds
    return (x0 + x1) / 2, (y0 + y1) / 2, A3_MM[0] / 2000 * echelle, A3_MM[1] / 2000 * echelle


def zone_maximale(parcelle):
    """Emprise (Lambert 93) de la feuille à la plus petite échelle : les données sont lues une seule fois pour les trois plans."""
    cx, cy, dx, dy = fenetre(parcelle, max(ECHELLES))
    return box(cx - dx, cy - dy, cx + dx, cy + dy)


def _patch(poly, **kw):
    """Polygone shapely (avec trous) → patch matplotlib."""
    verts, codes = [], []
    for ring in [poly.exterior, *poly.interiors]:
        c = list(ring.coords)
        verts += c
        codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
    return PathPatch(Path(verts, codes), facecolor="none", **kw)


def tracer_plan(parcelle, chemin_pdf, echelle, voisines, batiments, voies, titre=""):
    """Écrit un plan A3 à l'échelle 1/`echelle`."""
    plt.rcParams["pdf.fonttype"] = 42                              # textes conservés comme textes
    cx, cy, dx, dy = fenetre(parcelle, echelle)
    zone = box(cx - dx, cy - dy, cx + dx, cy + dy)
    fig = plt.figure(figsize=(A3_MM[0] / 25.4, A3_MM[1] / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(cx - dx, cx + dx)
    ax.set_ylim(cy - dy, cy + dy)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    lw = 0.35 if echelle >= 1000 else 0.5

    for b in batiments:
        ax.add_patch(_patch(b, edgecolor="#808080", lw=lw, clip_on=False))
    cible = (parcelle.section, parcelle.numero)
    for p in voisines:
        if (p.section, p.numero) == cible and p.geom.distance(parcelle.geom) < 0.5:
            continue
        ax.add_patch(_patch(p.geom, edgecolor="#000000", lw=lw, clip_on=False))
    ax.add_patch(_patch(parcelle.geom, edgecolor=RED, lw=1.2, clip_on=False))

    for p in voisines:                                             # numéros de parcelles
        pt = p.geom.representative_point()
        est_cible = p.geom.distance(parcelle.geom) < 0.5 and (p.section, p.numero) == cible
        if not zone.contains(pt):
            continue
        if (p.geom.bounds[2] - p.geom.bounds[0]) / echelle * 1000 < 4 and not est_cible:   # < 4 mm sur le papier
            continue
        ax.text(pt.x, pt.y, p.numero, ha="center", va="center", fontsize=8 if est_cible else 6,
                color=RED if est_cible else "#000000", clip_on=False)

    for nom, g in voies:                                           # axes et noms de voies
        g = g.intersection(zone)
        if g.is_empty:
            continue
        lignes = list(g.geoms) if g.geom_type.startswith("Multi") else [g]
        for ln in lignes:
            if ln.geom_type == "LineString":
                ax.plot(*ln.xy, color="#2F6DB5", lw=0.3, ls=(0, (8, 4)), clip_on=False)
        ligne = max((ln for ln in lignes if ln.geom_type == "LineString"), key=lambda a: a.length, default=None)
        if ligne is None or ligne.length / echelle * 1000 < 25:    # voie < 25 mm sur le papier : nom omis
            continue
        a, b = ligne.interpolate(0.45, normalized=True), ligne.interpolate(0.55, normalized=True)
        ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
        ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
        m = ligne.interpolate(0.5, normalized=True)
        ax.text(m.x, m.y, nom.upper(), rotation=ang, rotation_mode="anchor", ha="center", va="bottom", fontsize=7,
                color="#2F6DB5", clip_on=False)

    with PdfPages(chemin_pdf) as pdf:
        pdf.infodict().update(Title=titre or f"Plan cadastral 1/{echelle}", Author="AGOA DATA",
                              Subject=f"Parcelle {parcelle.idu} — échelle 1/{echelle} — Lambert 93")
        pdf.savefig(fig)
    plt.close(fig)


def nom_fichier(parcelle, echelle):
    idu = re.sub(r"[^A-Za-z0-9]+", "", parcelle.idu) or f"{parcelle.section}{parcelle.numero}"
    return f"plan_cadastral_{idu}_1-{echelle}.pdf"


def plans_cadastraux(adr, parcelle, dossier, voisines=None, batiments=None, voies=None, progress=None):
    """Écrit les trois plans A3 (1/500, 1/1000, 1/2000) dans `dossier` ; renvoie la liste des noms de fichiers."""
    import os
    zone = zone_maximale(parcelle)
    if voisines is None:
        voisines = geo.parcelles_dans(zone)
    if batiments is None:
        batiments = geo.batiments_dans(zone)
    if voies is None:
        voies = geo.voies_dans(zone)
    noms = []
    for e in ECHELLES:
        n = nom_fichier(parcelle, e)
        tracer_plan(parcelle, os.path.join(dossier, n), e, voisines, batiments, voies,
                    titre=f"Plan cadastral 1/{e} — {adr.label}")
        noms.append(n)
        if progress:
            progress(f"Plan cadastral A3 1/{e} : {n}")
    return noms


def apercu(chemin_png, plans_places, contexte):
    """Aperçu PNG du recalage : cadastre (parcelle en rouge, bâtiments) et murs des plans superposés."""
    fig, ax = plt.subplots(figsize=(9, 7), dpi=110)
    xs, ys = [], []
    if contexte:
        for b in contexte["batiments"]:
            ax.add_patch(_patch(b, edgecolor="#808080", lw=0.8))
        for p in contexte["voisines"]:
            ax.add_patch(_patch(p.geom, edgecolor="#555555", lw=0.5))
        ax.add_patch(_patch(contexte["parcelle"].geom, edgecolor=RED, lw=1.8))
        x0, y0, x1, y1 = contexte["parcelle"].geom.bounds
        xs += [x0 - 8, x1 + 8]
        ys += [y0 - 8, y1 + 8]
    couleurs = ["#0B6E6E", "#C2410C", "#6D28D9", "#15803D", "#B45309"]
    for i, (nom, calques, _) in enumerate(plans_places):
        c = couleurs[i % len(couleurs)]
        for typ, lw in (("TRAITS", 0.3), ("MURS", 0.9)):
            for pts, ferme in calques.get(typ, []):
                a = np.array(pts + ([pts[0]] if ferme else []))
                ax.plot(a[:, 0], a[:, 1], color=c, lw=lw, alpha=0.9 if typ == "MURS" else 0.6)
                if not contexte:
                    xs += [a[:, 0].min(), a[:, 0].max()]
                    ys += [a[:, 1].min(), a[:, 1].max()]
        ax.plot([], [], color=c, label=nom)
    if xs:
        ax.set_xlim(min(xs), max(xs))
        ax.set_ylim(min(ys), max(ys))
    ax.set_aspect("equal")
    ax.set_title("Recalage des plans sur le cadastre (Lambert 93)", fontsize=10)
    if plans_places:
        ax.legend(loc="upper right", fontsize=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(chemin_png)
    plt.close(fig)
