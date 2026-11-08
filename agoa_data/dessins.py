"""Dessins d'architecte de la façade sur rue, calculés depuis le relevé `facade.json` (aucun générateur d'images).

Le relevé vient de la chaîne façade : nuage LiDAR (sol, planchers, terrasse), coupes (hauteurs), cadastre / BD TOPO (longueur),
vue Panoramax redressée (travées, linteaux, persiennes, balcons). Six dessins et une planche :
  elevation   l'élévation au trait, hiérarchie des épaisseurs
  relief      ombres portées géométriques, soleil conventionnel à 45° (haut gauche)
  teintes     quatre variantes de couleurs (menuiseries, garde-corps, persiennes)
  trace       trame, niveaux, cotes et rapports hauteur / largeur des baies
  annote      façade annotée, vocabulaire du métier
  calepinage  assises, joints, harpes et claveaux
Chaque dessin : `<nom>.png` 1080 × 1920 et `<nom>.mp4` (3 s, 30 i/s, H.264, yuv420p, 20 dernières images tenues).
Repère du relevé : x = m depuis la limite gauche de la façade, z = m depuis le sol.
"""
import datetime
import json
import math
import os
import shutil
import subprocess
import textwrap
import zlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                         # noqa: E402
import numpy as np                                                       # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection        # noqa: E402
from matplotlib.patches import PathPatch, Rectangle                      # noqa: E402
from matplotlib.path import Path                                         # noqa: E402
from shapely import affinity                                             # noqa: E402
from shapely.geometry import MultiPolygon, Polygon, box                  # noqa: E402
from shapely.geometry.polygon import orient                              # noqa: E402
from shapely.ops import unary_union                                      # noqa: E402

from . import APP_NAME, APP_VERSION                                      # noqa: E402
from . import baies as baies_mod                                         # noqa: E402

W, H = 1080, 1920
FPS, N_IMG, N_FIXE = 30, 90, 20           # 3 s ; les 20 dernières images sont l'image finale
PX = 0.72                                  # points par pixel à 100 dpi
NOIR = "#141414"
TH, MD, TN, HR = 3.2, 1.9, 1.1, 0.6        # épaisseurs de trait (px) : contour et sol / corniches et bandeaux / menuiseries et garde-corps / modénature fine

# Hypothèses de dessin, toutes écrites dans le cartouche ou les notes de chaque dessin
BANDEAU_H, BANDEAU_S = 0.25, 0.12          # bandeau au-dessus du RDC : hauteur et saillie (m)
CORNICHE_H, CORNICHE_S = 0.35, 0.35        # corniche haute
BALCON_S = 0.80                            # saillie d'un balcon filant
APPUI_S = 0.12
EMBRASURE = 0.20                           # profondeur d'embrasure des baies
ASSISE = 0.50                              # hauteur d'assise de la pierre


def fm(v, n=2):
    return f"{v:.{n}f}".replace(".", ",")


# --------------------------------------------------------------------------------------------------------------------
# Relevé facade.json
# --------------------------------------------------------------------------------------------------------------------
def releve(forme, bx, adresse=None, sources=None):
    """Assemble le relevé complet depuis la forme générale (`forme.construire`) et les baies (`baies.ecrire`)."""
    etat = bx["etat"]
    copies = [k + 1 for k in range(len(forme["niveaux"])) if k >= len(etat) or not etat[k]]
    suppose = [
        f"Largeur de baie = travée mesurée − 2 × {baies_mod.JAMBAGE * 100:.0f} cm de jambage (dimension type, non mesurée)",
        f"Bandeau au-dessus du RDC : {BANDEAU_H * 100:.0f} cm de haut, {BANDEAU_S * 100:.0f} cm de saillie",
        f"Corniche haute : {CORNICHE_H * 100:.0f} cm de haut, {CORNICHE_S * 100:.0f} cm de saillie ; balcon filant : {BALCON_S * 100:.0f} cm de saillie",
        f"Appuis : {APPUI_S * 100:.0f} cm de saillie ; embrasures : {EMBRASURE * 100:.0f} cm de profondeur",
        f"Assises de pierre : {ASSISE * 100:.0f} cm, joints verticaux répartis au hasard (graine fixe), sans relevé de pierre par pierre",
        "Matériaux (pierre de taille, fonte, bois) : probables, la photo ne les prouve pas",
        "Hauteur de prise de vue Panoramax supposée à 2,3 m",
    ]
    if copies:
        suppose.append("Niveaux R+" + ", R+".join(str(k) for k in copies) + " : baies copiées du dernier niveau mesuré (hors photo)")
    non_lu = ["RDC (boutiques, porches) : non relevé dans ce dessin, représenté en aplat",
              "Terrasse, attiques et étages en retrait : non visibles de la rue, non dessinés",
              "Lucarnes, souches, brisis, terrasson : sans objet (toiture-terrasse à l'alignement) ou non visibles"]
    return {"schema": 1, "voie": forme["voie"], "adresse": adresse, "longueur_m": forme["longueur"], "forme": forme,
            "baies": {"travees": [[round(a, 3), round(b, 3)] for a, b in bx["travees_regularisees"]], "etat": etat},
            "sources": sources or {"hauteurs": "nuage LiDAR HD et coupes", "longueur": "BD TOPO / cadastre", "baies": "vue Panoramax redressée"},
            "suppose": suppose, "non_lu": non_lu, "date": datetime.datetime.now().strftime("%d/%m/%Y"), "version": APP_VERSION}


def ecrire_releve(fac, chemin):
    with open(chemin, "w", encoding="utf-8") as fh:
        json.dump(fac, fh, ensure_ascii=False, indent=1)
    return chemin


def charger(chemin):
    with open(chemin, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------------------------------------------------
# Modèle géométrique
# --------------------------------------------------------------------------------------------------------------------
class Modele:
    """Géométrie de la façade en mètres, calculée depuis le relevé (mêmes règles que `baies.construire`)."""

    def __init__(self, fac):
        self.fac = fac
        fo = self.forme = fac["forme"]
        self.L = float(fo["longueur"])
        self.T = float(fo["terrasse"]["dalle"])
        self.z1 = float(fo["rdc"]["z1"])
        self.reg = [tuple(t) for t in fac["baies"]["travees"]]
        etat = fac["baies"]["etat"]
        n = self.n = len(self.reg)
        mes = [e for e in etat if e]
        defaut = {"persiennes": [False] * n, "continu": [False] * max(n - 1, 0)}
        self.niv = []
        for k, nv in enumerate(fo["niveaux"]):
            vu = k < len(etat) and etat[k]
            e = etat[k] if vu else (mes[-1] if mes else defaut)
            self.niv.append({"nom": nv["nom"], "zf": nv["plancher"], "zl": nv["linteau"], "dalle": nv["dessus_dalle"], "pers": e["persiennes"],
                             "groupes": baies_mod._groupes(e["continu"], n), "copie": not vu})
        self.d = baies_mod.construire(None, fo, etat, self.reg)
        self.cl = classer(self.d)
        J = baies_mod.JAMBAGE
        self.axes = [(a + b) / 2 for a, b in self.reg]
        self.ouvrants = [(a + J, b - J) for a, b in self.reg]

    def baies(self):
        """Toutes les baies : (niveau k, travée i, ouvrant (xa, xb), zf, zl)."""
        for k, nv in enumerate(self.niv):
            for i, (xa, xb) in enumerate(self.ouvrants):
                yield k, i, xa, xb, nv["zf"], nv["zl"]

    def encadrement(self, xa, xb, zf, zl):
        return (xa - 0.14, zf, xb + 0.14, zl + 0.20)

    def ratio(self, k):
        """Rapport hauteur / largeur de la baie type du niveau k (ouvrant)."""
        xa, xb = self.ouvrants[min(1, self.n - 1)]
        nv = self.niv[k]
        h = nv["zl"] - (nv["zf"] + 0.03)
        w = xb - xa
        return h / w, w, h

    def hauteurs_etage(self):
        pl = [0.0, self.z1] + [nv["dalle"] for nv in self.niv]
        return [pl[i + 1] - pl[i] for i in range(len(pl) - 1)]


def classer(d):
    """Range les tracés de `baies.Dessin` en couches (A sous les persiennes, B persiennes, C garde-corps / appuis / balcons) et repère les rectangles utiles."""
    cl = {"A": [], "B": [], "C": [], "FAC": [], "vitrage": [], "pers": [], "balcon": [], "encadrement": []}
    dernier = None
    for layer, kind, data in d.p:
        if layer == "TEXTES":
            continue
        role = ""
        if kind == "poly":
            if layer == "BAIE_MENUISERIE":
                role = "ext" if dernier == "BAIE_ENCADREMENT" else "int"
            dernier = layer
        grp = {"BAIE_ENCADREMENT": "A", "BAIE_MENUISERIE": "A", "BAIE_PETITS_BOIS": "A", "BAIE_PERSIENNES": "B",
               "BAIE_GARDE_CORPS": "C", "BAIE_APPUIS": "C", "BAIE_BALCON": "C", "FACADE": "FAC"}[layer]
        cl[grp].append((layer, kind, data, role))
        if kind == "poly":
            xs, zs = zip(*data[0])
            r = (min(xs), min(zs), max(xs), max(zs))
            if role == "int":
                cl["vitrage"].append(r)
            elif layer == "BAIE_PERSIENNES":
                cl["pers"].append(r)
            elif layer == "BAIE_BALCON":
                cl["balcon"].append(r)
            elif layer == "BAIE_ENCADREMENT":
                cl["encadrement"].append(r)
    return cl


def poids(layer, kind, data):
    if layer == "FACADE":
        return "TH" if kind == "poly" else "MD"
    if layer in ("BAIE_ENCADREMENT", "BAIE_BALCON"):
        return "MD"
    if layer in ("BAIE_MENUISERIE", "BAIE_APPUIS"):
        return "TN"
    if layer == "BAIE_GARDE_CORPS":
        if kind == "poly":
            return "TN"
        return "TN" if abs(data[0][1] - data[1][1]) < 1e-6 else "HR"
    return "HR"


WPX = {"TH": TH, "MD": MD, "TN": TN, "HR": HR}


# --------------------------------------------------------------------------------------------------------------------
# Page, vue et éléments communs
# --------------------------------------------------------------------------------------------------------------------
def page():
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    return fig, ax


class Vue:
    """Passage mètres → pixels (z vers le haut, y de l'image vers le bas)."""

    def __init__(self, x0, ys, ech):
        self.x0, self.ys, self.ech = x0, ys, ech

    @classmethod
    def plein(cls, L, T, rel=0.88, y_haut=215, y_sol_min=1010, y_sol_max=1260):
        ech = min(rel * W / L, (y_sol_max - y_haut) / T)
        ys = min(max(y_sol_min, y_haut + T * ech), y_sol_max)
        return cls((W - L * ech) / 2, ys, ech)

    def X(self, x):
        return self.x0 + x * self.ech

    def Y(self, z):
        return self.ys - z * self.ech

    def pts(self, lst):
        return [(self.X(x), self.Y(z)) for x, z in lst]

    def rect(self, r):
        x0, z0, x1, z1 = r
        return [(self.X(x0), self.Y(z0)), (self.X(x1), self.Y(z0)), (self.X(x1), self.Y(z1)), (self.X(x0), self.Y(z1))]


def txt(ax, x, y, s, size=9.0, color=NOIR, ha="left", va="center", weight="normal", rot=0, bbox=None, z=20, alpha=1.0, style="normal"):
    return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, fontweight=weight, rotation=rot, bbox=bbox, zorder=z, alpha=alpha,
                   fontstyle=style, family="DejaVu Sans")


def ligne(ax, pts, lw=1.0, color=NOIR, z=5, ls="-", alpha=1.0, cap="butt"):
    xs, ys = zip(*pts)
    return ax.plot(xs, ys, color=color, lw=lw * PX, zorder=z, ls=ls, alpha=alpha, solid_capstyle=cap, dash_capstyle=cap)[0]


def lc(ax, segs, lw, color=NOIR, z=5, ls="-", alpha=1.0):
    if not segs:
        return None
    c = LineCollection(segs, linewidths=lw * PX, colors=color, zorder=z, capstyle="round", linestyles=ls, alpha=alpha)
    ax.add_collection(c)
    return c


def pc(ax, polys, fc, z=2, ec="none", lw=0.0, alpha=1.0):
    if not polys:
        return None
    c = PolyCollection(polys, facecolors=fc, edgecolors=ec, linewidths=lw * PX, zorder=z, alpha=alpha)
    ax.add_collection(c)
    return c


def segs_poly(pts, close=True):
    s = list(zip(pts[:-1], pts[1:]))
    if close:
        s.append((pts[-1], pts[0]))
    return s


def geom_patch(geom, vue, **kw):
    """Géométrie shapely (m) → PathPatch (px), avec trous."""
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    paths = []
    for p in polys:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        p = orient(p, 1.0)
        for ring in [p.exterior] + list(p.interiors):
            v = vue.pts(list(ring.coords))
            paths.append(Path(v, [Path.MOVETO] + [Path.LINETO] * (len(v) - 2) + [Path.CLOSEPOLY]))
    if not paths:
        return None
    return PathPatch(Path.make_compound_path(*paths), **kw)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def seg_t(p, a, b):
    return min(max((p - a) / (b - a), 0.0), 1.0)


def entete(ax, fac, titre):
    txt(ax, 40, 52, titre.upper(), size=11, weight="bold")
    adr = fac.get("adresse") or ""
    txt(ax, 40, 78, f"{fac['voie']}" + (f" — {adr}" if adr else ""), size=9, color="#444444")
    ligne(ax, [(40, 98), (W - 40, 98)], lw=HR, color="#888888")


def echelle_graphique(ax, x, y, ech, metres=5, hauteur=7):
    """Barre d'échelle graphique alternée noir / blanc, tous les mètres."""
    for i in range(metres):
        ax.add_patch(Rectangle((x + i * ech, y), ech, hauteur, fc=NOIR if i % 2 == 0 else "white", ec=NOIR, lw=0.7 * PX, zorder=20))
        txt(ax, x + i * ech, y - 9, str(i), size=7, ha="center")
    txt(ax, x + metres * ech, y - 9, f"{metres} m", size=7, ha="center")


def cartouche(ax, fac, titre, ech, extra=None, y=1812):
    ax.add_patch(Rectangle((40, y), W - 80, 78, fc="white", ec=NOIR, lw=0.9 * PX, zorder=19))
    ligne(ax, [(560, y), (560, y + 78)], lw=HR, z=20)
    txt(ax, 52, y + 18, titre.upper(), size=9.5, weight="bold")
    txt(ax, 52, y + 38, f"{fac['voie']} — façade sur rue", size=8)
    txt(ax, 52, y + 58, f"{APP_NAME} {fac.get('version', APP_VERSION)} · {fac.get('date', '')}" + (f" · {extra}" if extra else ""), size=7.5, color="#555555")
    m = 5 if ech * 5 < 330 else 2
    echelle_graphique(ax, 580, y + 45, ech, m)
    txt(ax, 580, y + 14, "échelle graphique", size=7, color="#555555")


def tableau(ax, y0, colonnes, lignes, xs, h=29, size=8.4, entete_fond="#efefef", x_fin=W - 40):
    """Tableau simple : colonnes = titres, lignes = listes de textes, xs = abscisses des colonnes (alignées à gauche)."""
    n = len(lignes) + 1
    ax.add_patch(Rectangle((40, y0), x_fin - 40, h, fc=entete_fond, ec="none", zorder=3))
    for r in range(n + 1):
        ligne(ax, [(40, y0 + r * h), (x_fin, y0 + r * h)], lw=HR, color="#777777", z=4)
    for x, c in zip(xs, colonnes):
        txt(ax, x, y0 + h / 2, c, size=size, weight="bold")
    for r, row in enumerate(lignes, 1):
        for x, c in zip(xs, row):
            txt(ax, x, y0 + r * h + h / 2, c, size=size)
    return y0 + n * h


def notes(ax, y0, titre, lignes, size=8.2, largeur=118, couleur="#222222"):
    txt(ax, 40, y0, titre, size=8.6, weight="bold")
    y = y0 + 20
    for ln in lignes:
        for j, part in enumerate(textwrap.wrap(ln, largeur) or [""]):
            txt(ax, 52 if j == 0 else 64, y, ("• " if j == 0 else "") + part, size=size, color=couleur)
            y += 15
    return y


def rdc_texte(ax, vue, mo, fond=None):
    """Mention discrète sur le RDC, qui n'est pas relevé."""
    txt(ax, vue.X(mo.L / 2), vue.Y(mo.z1 / 2), "RDC — boutiques : non relevé", size=9.5, color="#7a7a7a", ha="center", style="italic")


def pied(ax, fac, y, supposes=None, largeur=124):
    """Blocs « Supposé » et « Non lu » communs aux dessins."""
    y = notes(ax, y, "Supposé", supposes if supposes is not None else fac["suppose"], largeur=largeur, size=8.6)
    return notes(ax, y + 10, "Non lu", fac["non_lu"], largeur=largeur, size=8.6)


def sol_trottoir(ax, vue, L, lw=TH):
    """Sol épais, bordure et hachures de trottoir."""
    x0, x1, y = vue.X(-1.2), vue.X(L + 1.2), vue.ys
    ligne(ax, [(x0, y), (x1, y)], lw=lw, z=8)
    ligne(ax, [(x0, y + 20), (x1, y + 20)], lw=TN, z=8)
    segs = [((x, y + 20), (x - 14, y + 4)) for x in np.arange(x0 + 14, x1 + 1, 17)]
    lc(ax, segs, HR, "#555555", z=7)
    ligne(ax, [(x0, y + 34), (x1, y + 34)], lw=HR, color="#555555", z=7)


# --------------------------------------------------------------------------------------------------------------------
# Tracé au trait (élévation, base des autres dessins)
# --------------------------------------------------------------------------------------------------------------------
def extras(mo):
    """Éléments d'ordonnancement ajoutés au relevé : bandeau au-dessus du RDC, corniche haute (hypothèses déclarées)."""
    L, T, z1 = mo.L, mo.T, mo.z1
    return {"bandeau": (0.0, z1 - BANDEAU_H, L, z1), "corniche": (0.0, T - CORNICHE_H, L, T)}


def trace_lignes(ax, vue, mo, echelle_trait=1.0, couleur=NOIR, vitrage=None, contour=True, z0=0):
    """Dessine l'élévation au trait par couches. Retourne {'contour': [...], 'corps': [artistes]} (le contour est à part pour l'animer)."""
    cl = mo.cl
    corps = []
    if vitrage:
        corps.append(pc(ax, [vue.rect(r) for r in cl["vitrage"]], vitrage, z=z0 + 2))
    couches = {"A": (3, None), "B": (5, "pers"), "C": (6, "balcon")}
    for g, (z, fond) in couches.items():
        if fond == "pers":
            corps.append(pc(ax, [vue.rect(r) for r in cl["pers"]], "white", z=z0 + 4))
        if fond == "balcon":
            corps.append(pc(ax, [vue.rect(r) for r in cl["balcon"]], "white", z=z0 + 5.5))
        par_poids = {}
        for layer, kind, data, role in cl[g]:
            p = poids(layer, kind, data)
            if kind == "poly":
                s = segs_poly(vue.pts(data[0]), data[1])
            else:
                s = [tuple(vue.pts([data[0], data[1]]))]
            par_poids.setdefault(p, []).extend(s)
        for p, s in par_poids.items():
            corps.append(lc(ax, s, WPX[p] * echelle_trait, couleur, z=z0 + z))
    ex = extras(mo)
    s_md = []
    for r in ex.values():
        s_md += segs_poly(vue.rect(r))
    corps.append(lc(ax, s_md, MD * echelle_trait, couleur, z=z0 + 3))
    s_fac_md = [tuple(vue.pts([(0, mo.z1), (mo.L, mo.z1)]))]
    corps.append(lc(ax, s_fac_md, MD * echelle_trait, couleur, z=z0 + 3))
    cont = vue.pts([(0, 0), (0, mo.T), (mo.L, mo.T), (mo.L, 0), (0, 0)]) if contour else None
    return {"contour": cont, "corps": [c for c in corps if c is not None]}


class Scene:
    def __init__(self):
        self.fig, self.ax = page()

    def maj(self, p):
        pass

    def rgba(self, p):
        self.maj(p)
        self.fig.canvas.draw()
        return np.asarray(self.fig.canvas.buffer_rgba()).copy()

    def png(self, chemin):
        self.maj(1.0)
        self.fig.savefig(chemin, dpi=100, facecolor="white")

    def fermer(self):
        plt.close(self.fig)


def long_poly(pts):
    d = [0.0]
    for a, b in zip(pts[:-1], pts[1:]):
        d.append(d[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return d


def partiel(pts, q):
    """Début de la polyligne `pts` sur la fraction q de sa longueur."""
    d = long_poly(pts)
    cible = d[-1] * min(max(q, 0.0), 1.0)
    out = [pts[0]]
    for i in range(1, len(pts)):
        if d[i] <= cible:
            out.append(pts[i])
        else:
            t = (cible - d[i - 1]) / max(d[i] - d[i - 1], 1e-9)
            out.append((pts[i - 1][0] + t * (pts[i][0] - pts[i - 1][0]), pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1])))
            break
    return out


class Reveal:
    """Fenêtre de découpe : tout ce qui est sous la hauteur z (en px : y plus grand) est visible."""

    def __init__(self, ax, artistes):
        self.ax = ax
        self.artistes = list(artistes)
        self.rect = Rectangle((0, 0), W, H, transform=ax.transData, fc="none", ec="none")
        ax.add_patch(self.rect)
        self.jusqua(0)

    def jusqua(self, y_px):
        self.rect.set_bounds(0, y_px, W, H - y_px)
        for a in self.artistes:                       # le chemin de découpe est mis en cache : on le redonne à chaque changement
            a.set_clip_path(self.rect)


def bornes_niveaux(mo):
    return [0.0, mo.z1] + [nv["dalle"] for nv in mo.niv]


def table_niveaux(ax, mo, y0, h=31, size=9.0):
    lignes = [["RDC", "0,00", "—", fm(mo.z1), "non relevé", "—"]]
    hs = mo.hauteurs_etage()
    for k, nv in enumerate(mo.niv):
        r, w, hb = mo.ratio(k)
        lignes.append([nv["nom"] + (" (copie)" if nv["copie"] else ""), fm(nv["zf"]), fm(nv["zl"]), fm(hs[k + 1]), f"{fm(w)} × {fm(hb)}", fm(r)])
    lignes.append(["Terrasse", fm(mo.T), "—", "—", "", ""])
    return tableau(ax, y0, ["Niveau", "Plancher (m)", "Linteau (m)", "Hauteur d'étage (m)", "Baie type L × H (m)", "h / l"], lignes,
                   [52, 215, 380, 545, 760, 960], h=h, size=size)


# ---- 1. élévation ---------------------------------------------------------------------------------------------------
class Elevation(Scene):
    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        self.fac = fac
        self.vue = vue = Vue.plein(mo.L, mo.T)
        entete(self.ax, fac, "Élévation au trait")
        sol_trottoir(self.ax, vue, mo.L)
        t = trace_lignes(self.ax, vue, mo)
        self.cont_pts = t["contour"]
        self.cont = ligne(self.ax, self.cont_pts, lw=TH, z=8, cap="round")
        self.reveal = Reveal(self.ax, t["corps"])
        self.zs = bornes_niveaux(mo)
        rdc_texte(self.ax, vue, mo)
        y = table_niveaux(self.ax, mo, vue.ys + 62)
        pied(self.ax, fac, y + 30)
        cartouche(self.ax, fac, "Élévation au trait", vue.ech)
        self.reveal.jusqua(vue.ys)

    def maj(self, p):
        v, mo = self.vue, self.mo
        q = ease(seg_t(p, 0.0, 0.22))
        self.cont.set_data(*zip(*partiel(self.cont_pts, max(q, 0.04))))
        zs = self.zs
        S = len(zs) - 1
        x = seg_t(p, 0.20, 1.0) * S
        i = min(int(x), S - 1)
        zr = zs[i] + (zs[i + 1] - zs[i]) * ease(x - i) if p < 1 else zs[-1] + 1
        zr = zs[0] if p <= 0.20 else zr
        self.reveal.jusqua(v.Y(zr))


# ---- 2. relief ------------------------------------------------------------------------------------------------------
def saillies(mo):
    """Éléments saillants : liste de (rectangle (x0, z0, x1, z1) en m, saillie en m, opacité relative)."""
    ex = extras(mo)
    out = [(ex["bandeau"], BANDEAU_S, 1.0), (ex["corniche"], CORNICHE_S, 1.0)]
    for r in mo.cl["balcon"]:
        out.append((r, BALCON_S, 1.0))
    for layer, kind, data, role in mo.cl["C"]:
        if kind == "poly" and layer == "BAIE_GARDE_CORPS":
            xs, zs = zip(*data[0])
            if max(xs) - min(xs) > 1.0 * 1.0 and (max(zs) - min(zs)) > 0.9 and abs(max(zs) - min(zs) - baies_mod.GC_COLLECTIF + 0.06) < 0.02:
                out.append(((min(xs), min(zs), max(xs), max(zs)), BALCON_S, 0.30))      # garde-corps de balcon filant : ajouré
        if kind == "line" and layer == "BAIE_APPUIS" and abs(data[0][1] - data[1][1]) < 1e-6 and abs(data[0][0] - data[1][0]) > 0.5:
            out.append(((min(data[0][0], data[1][0]), data[0][1] - 0.05, max(data[0][0], data[1][0]), data[0][1]), APPUI_S, 1.0))
    return out


def ombre_rect(r, s):
    """Ombre portée sur la façade d'un élément saillant : rectangle balayé le long de (s, −s) (lumière à 45° en plan et en élévation), moins l'élément."""
    if s <= 1e-6:
        return None
    rr = box(*r)
    g = unary_union([rr, affinity.translate(rr, s, -s)]).convex_hull.difference(rr)
    return g if not g.is_empty else None


def ombres(mo, k):
    """Ombres du niveau d'avancement k (0 → 1) : (ombre pleine, ombre ajourée, ombres d'embrasure), clippées à la façade."""
    fa = box(0, 0, mo.L, mo.T)
    pleines, ajourees = [], []
    for r, s, o in saillies(mo):
        g = ombre_rect(r, s * k)
        if g is not None:
            (pleines if o >= 1.0 else ajourees).append(g)
    emb = []
    e = EMBRASURE * k
    for (x0, z0, x1, z1) in mo.cl["vitrage"]:
        if e > 1e-6:
            emb.append(box(x0, z0, min(x0 + e, x1), z1))
            emb.append(box(x0, max(z1 - e, z0), x1, z1))
    def clip(lst):
        return unary_union(lst).intersection(fa) if lst else None
    return clip(pleines), clip(ajourees), clip(emb)


LAVIS = "#3f5f86"


class Relief(Scene):
    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        self.fac = fac
        self.vue = vue = Vue.plein(mo.L, mo.T)
        entete(self.ax, fac, "Façade en relief — ombres portées")
        sol_trottoir(self.ax, vue, mo.L, lw=2.4)
        pc(self.ax, [vue.rect(r) for r in mo.cl["vitrage"]], "#e4eaf0", z=2)
        t = trace_lignes(self.ax, vue, mo, echelle_trait=0.75, couleur="#222222", z0=4)
        ligne(self.ax, t["contour"], lw=2.4, z=12, cap="round")
        rdc_texte(self.ax, vue, mo)
        self.patches = []
        y = vue.ys + 62
        # légende : soleil et saillies supposées
        self.sun(y)
        lignes = [["Bandeau au-dessus du RDC", fm(BANDEAU_S), fm(BANDEAU_S)], ["Corniche haute", fm(CORNICHE_S), fm(CORNICHE_S)],
                  ["Balcon filant et son garde-corps", fm(BALCON_S), fm(BALCON_S)], ["Appuis de baies", fm(APPUI_S), fm(APPUI_S)],
                  ["Embrasures (ombre dans la baie)", fm(EMBRASURE), fm(EMBRASURE)]]
        y2 = tableau(self.ax, y + 112, ["Élément (saillies supposées)", "Saillie (m)", "Ombre (m)"], lignes, [52, 640, 840])
        y3 = notes(self.ax, y2 + 28, "Lumière conventionnelle",
                   ["Lumière venant d'en haut à gauche, à 45° en plan et en élévation : une ombre portée a la longueur de la saillie qui la porte.",
                    "Toutes les saillies et profondeurs d'embrasure sont supposées (aucune ne se lit sur une photo de face). Ciel blanc."], size=8.6)
        notes(self.ax, y3 + 10, "Non lu", fac["non_lu"], size=8.6)
        cartouche(self.ax, fac, "Ombres portées", vue.ech, extra="saillies supposées")

    def sun(self, y):
        ax = self.ax
        txt(ax, 40, y + 10, "Lumière", size=8.6, weight="bold")
        ax.annotate("", xy=(78, y + 38), xytext=(150, y + 82), arrowprops=dict(arrowstyle="-|>", color="#c98a00", lw=1.4 * PX), zorder=20)
        txt(ax, 162, y + 70, "soleil conventionnel 45°, d'en haut à gauche", size=8, color="#555555")

    def maj(self, p):
        for a in self.patches:
            a.remove()
        self.patches = []
        k = ease(p)
        pl, aj, em = ombres(self.mo, k)
        for g, al in ((pl, 0.42), (aj, 0.20), (em, 0.50)):
            if g is not None and not g.is_empty:
                patch = geom_patch(g, self.vue, fc=LAVIS, ec="none", alpha=al, zorder=9)
                if patch is not None:
                    self.ax.add_patch(patch)
                    self.patches.append(patch)


# ---- 3. teintes -----------------------------------------------------------------------------------------------------
VARIANTES = [
    {"nom": "Vert wagon", "menu": ("vert wagon", "#2F5B49"), "gc": ("vert noir", "#1E2B26"), "pers": ("vert tilleul", "#4C7C63")},
    {"nom": "Bleu de Prusse", "menu": ("bleu de Prusse", "#1D3F5E"), "gc": ("bleu nuit", "#12222F"), "pers": ("bleu pigeon", "#3C6384")},
    {"nom": "Rouge basque", "menu": ("rouge basque", "#8B2E24"), "gc": ("brun noir", "#2B1B18"), "pers": ("rouge brique", "#A8483A")},
    {"nom": "Gris anthracite", "menu": ("gris anthracite", "#3A4046"), "gc": ("noir de fer", "#1C1F22"), "pers": ("gris ardoise", "#5C656D")},
]
PIERRE, PIERRE_RDC, PIERRE_CLAIRE, PIERRE_FONCEE = "#E8DCC3", "#D8CBB0", "#F1E8D3", "#C9BCA3"
VITRAGE = "#2F3E4C"


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])


def melange(a, b, t):
    return tuple(hex_rgb(a) * (1 - t) + hex_rgb(b) * t)


def assombrir(c, f=0.78):
    return tuple(np.clip(np.array(c if not isinstance(c, str) else hex_rgb(c)) * f, 0, 1))


class AplatFacade:
    """Façade en couleurs plates : pierre, toiture-terrasse non visible, vitrages bleu-gris avec reflet ; menuiseries, garde-corps et persiennes selon la variante."""

    def __init__(self, ax, vue, mo, z0=0):
        self.mo, self.vue, self.ax = mo, vue, ax
        R = vue.rect
        cl = mo.cl
        pc(ax, [R((0, 0, mo.L, mo.T))], PIERRE, z=z0 + 1)
        pc(ax, [R((0, 0, mo.L, mo.z1))], PIERRE_RDC, z=z0 + 1.2)
        ex = extras(mo)
        pc(ax, [R(ex["bandeau"]), R(ex["corniche"])], PIERRE_CLAIRE, z=z0 + 1.3, ec="#BFB197", lw=0.6)
        pc(ax, [R(r) for r in cl["encadrement"]], PIERRE_CLAIRE, z=z0 + 1.5)
        ext = [R(r) for layer, kind, data, role in cl["A"] if kind == "poly" and role == "ext" for r in [self._r(data)]]
        inn = [R(r) for r in cl["vitrage"]]
        self.menu = pc(ax, ext, "#000000", z=z0 + 2)
        pc(ax, inn, VITRAGE, z=z0 + 2.2)
        refl = []
        for (x0, zb, x1, zh) in cl["vitrage"]:
            w, h = x1 - x0, zh - zb
            refl.append(vue.pts([(x0 + 0.25 * w, zh), (x0 + 0.55 * w, zh), (x0, zb + 0.45 * h), (x0, zb + 0.80 * h)]))
        pc(ax, refl, "white", z=z0 + 2.3, alpha=0.13)
        pb = [seg for layer, kind, data, role in cl["A"] if layer in ("BAIE_PETITS_BOIS", "BAIE_MENUISERIE") and kind == "line"
              for seg in [tuple(vue.pts([data[0], data[1]]))]]
        self.pb = lc(ax, pb, 0.7, "#000000", z=z0 + 2.5)
        self.pers = pc(ax, [R(r) for r in cl["pers"]], "#000000", z=z0 + 4)
        sl = [tuple(vue.pts([data[0], data[1]])) for layer, kind, data, role in cl["B"] if kind == "line"]
        self.slats = lc(ax, sl, 0.6, "#000000", z=z0 + 4.5)
        pc(ax, [R(r) for r in cl["balcon"]], PIERRE_FONCEE, z=z0 + 5)
        gc = [(layer, kind, data) for layer, kind, data, role in cl["C"] if layer == "BAIE_GARDE_CORPS"]
        sg = [segs for layer, kind, data in gc for segs in ([*segs_poly(vue.pts(data[0]))] if kind == "poly" else [tuple(vue.pts([data[0], data[1]]))])]
        self.gc = lc(ax, sg, 0.9, "#000000", z=z0 + 6)
        ap = [tuple(vue.pts([data[0], data[1]])) for layer, kind, data, role in cl["C"] if layer == "BAIE_APPUIS"]
        lc(ax, ap, 1.1, "#B3A58A", z=z0 + 5.5)

    @staticmethod
    def _r(data):
        xs, zs = zip(*data[0])
        return (min(xs), min(zs), max(xs), max(zs))

    def teinte(self, var):
        """`var` : dict {menu, gc, pers} de codes hexadécimaux, ou couleurs RVB déjà mélangées."""
        c = lambda v: v if not isinstance(v, str) else hex_rgb(v)
        self.menu.set_facecolor(c(var["menu"]))
        self.pb.set_color(c(var["menu"]))
        self.pers.set_facecolor(c(var["pers"]))
        self.slats.set_color(assombrir(c(var["pers"])))
        self.gc.set_color(c(var["gc"]))


def teinte_de(v):
    return {"menu": v["menu"][1], "gc": v["gc"][1], "pers": v["pers"][1]}


class TeintesGrande(Scene):
    """Une seule façade en grand ; sert à la vidéo."""

    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        self.fac = fac
        self.vue = vue = Vue.plein(mo.L, mo.T)
        entete(self.ax, fac, "Variantes de teintes")
        sol_trottoir(self.ax, vue, mo.L, lw=2.4)
        self.f = AplatFacade(self.ax, vue, mo)
        self.nom = txt(self.ax, W / 2, vue.ys + 92, "", size=15, weight="bold", ha="center")
        self.sw = []
        for j in range(3):
            self.sw.append((Rectangle((0, 0), 1, 1, fc="white", ec=NOIR, lw=0.8 * PX, zorder=20), txt(self.ax, 0, 0, "", size=8.4, ha="center")))
            self.ax.add_patch(self.sw[-1][0])
        cartouche(self.ax, fac, "Variantes de teintes", vue.ech, extra="aplats")
        self.idx(0, 0.0)

    def idx(self, i, t):
        a, b = VARIANTES[i % 4], VARIANTES[(i + 1) % 4]
        mix = {k: melange(teinte_de(a)[k], teinte_de(b)[k], t) for k in ("menu", "gc", "pers")}
        self.f.teinte(mix)
        v = a if t < 0.5 else b
        self.nom.set_text(v["nom"])
        for j, (cle, lab) in enumerate((("menu", "menuiseries"), ("gc", "garde-corps"), ("pers", "persiennes"))):
            x = W / 2 + (j - 1) * 250
            r, tx = self.sw[j]
            r.set_bounds(x - 90, self.vue.ys + 125, 180, 46)
            r.set_facecolor(mix[cle])
            tx.set_position((x, self.vue.ys + 188))
            tx.set_text(f"{lab}\n{v[cle][0]} {v[cle][1]}")

    def maj(self, p):
        pass


class TeintesGrille(Scene):
    """Planche 2 × 2 : la même élévation, quatre variantes."""

    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        ax = self.ax
        entete(ax, fac, "Quatre variantes de couleurs")
        cw = 460
        ech = cw / mo.L
        ligne_h = 640
        for n, var in enumerate(VARIANTES):
            c, r = n % 2, n // 2
            x0 = 40 + c * 520 + (500 - mo.L * ech) / 2
            ys = 125 + r * ligne_h + 120 + mo.T * ech + 6
            vue = Vue(x0, ys, ech)
            ligne(ax, [(vue.X(-0.6), ys), (vue.X(mo.L + 0.6), ys)], lw=2.2, z=8)
            f = AplatFacade(ax, vue, mo)
            f.teinte(teinte_de(var))
            txt(ax, 40 + c * 520 + 250, 125 + r * ligne_h + 22, f"{n + 1}. {var['nom']}", size=11.5, weight="bold", ha="center")
            for j, cle in enumerate(("menu", "gc", "pers")):
                xs = 40 + c * 520 + 28 + j * 160
                ax.add_patch(Rectangle((xs, 125 + r * ligne_h + 48), 26, 26, fc=var[cle][1], ec=NOIR, lw=0.7 * PX, zorder=20))
                txt(ax, xs + 32, 125 + r * ligne_h + 56, var[cle][0], size=7.4)
                txt(ax, xs + 32, 125 + r * ligne_h + 69, var[cle][1], size=7.4, color="#555555")
        y = 125 + 2 * ligne_h + 6
        lignes = []
        for n, var in enumerate(VARIANTES):
            lignes.append([f"{n + 1}. {var['nom']}", f"{var['menu'][0]}  {var['menu'][1]}", f"{var['gc'][0]}  {var['gc'][1]}", f"{var['pers'][0]}  {var['pers'][1]}"])
        y = tableau(ax, y, ["Variante", "Menuiseries", "Garde-corps", "Persiennes (volets)"], lignes, [52, 330, 600, 830], h=27)
        notes(ax, y + 24, "Dessin",
              ["Aplats : pierre (#E8DCC3), vitrages bleu-gris sombre (#2F3E4C) avec un reflet léger ; seules les teintes ci-dessus changent d'une variante à l'autre.",
               "RDC non relevé : laissé en pierre, sans devanture. Toiture-terrasse à l'alignement, non visible."], size=7.8, largeur=125)
        cartouche(ax, fac, "Variantes de teintes", ech, extra="aplats")


# ---- 4. trame et proportions ------------------------------------------------------------------------------------------
ROUGE, BLEU = "#D0021B", "#1F5FBF"


def cote_h(ax, x0, x1, y, texte, couleur=BLEU, size=7.6, haut=5, z=14):
    """Cote horizontale à traits obliques d'architecte ; renvoie les artistes."""
    a = [ligne(ax, [(x0, y), (x1, y)], lw=0.9, color=couleur, z=z)]
    for x in (x0, x1):
        a.append(ligne(ax, [(x, y - haut), (x, y + haut)], lw=0.7, color=couleur, z=z))
        a.append(ligne(ax, [(x - 3.5, y + 3.5), (x + 3.5, y - 3.5)], lw=1.4, color=couleur, z=z))
    a.append(txt(ax, (x0 + x1) / 2, y - 8, texte, size=size, color=couleur, ha="center", z=z + 1,
                 bbox=dict(fc="white", ec="none", pad=0.6, alpha=0.85)))
    return a


def cote_v(ax, x, y0, y1, texte, couleur=BLEU, size=7.6, haut=5, z=14, ha_txt="right", dx=-5, bbox=True):
    a = [ligne(ax, [(x, y0), (x, y1)], lw=0.9, color=couleur, z=z)]
    for y in (y0, y1):
        a.append(ligne(ax, [(x - haut, y), (x + haut, y)], lw=0.7, color=couleur, z=z))
        a.append(ligne(ax, [(x - 3.5, y + 3.5), (x + 3.5, y - 3.5)], lw=1.4, color=couleur, z=z))
    a.append(txt(ax, x + dx, (y0 + y1) / 2, texte, size=size, color=couleur, ha=ha_txt, rot=90, z=z + 1, va="center",
                 bbox=dict(fc="white", ec="none", pad=0.5, alpha=0.85) if bbox else None))
    return a


class Trace(Scene):
    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        self.fac = fac
        ax = self.ax
        self.vue = v = Vue.plein(mo.L, mo.T, rel=0.82, y_haut=235)
        entete(ax, fac, "Trame et proportions")
        sol_trottoir(ax, v, mo.L, lw=2.0)
        t = trace_lignes(ax, v, mo, echelle_trait=0.8, couleur="#B9B9B9", z0=0)
        ligne(ax, t["contour"], lw=2.0, color="#9A9A9A", z=8)
        self.anim = []                                   # (artistes, t0, t1, mode)
        # axes des travées (rouge, traits-points) : tombent du haut vers le bas
        self.axes_l = []
        for i, xc in enumerate(mo.axes):
            ln = ligne(ax, [(v.X(xc), v.Y(mo.T) - 22), (v.X(xc), v.ys)], lw=1.1, color=ROUGE, z=15, ls=(0, (9, 3, 1.5, 3)))
            lab = txt(ax, v.X(xc), v.Y(mo.T) - 34, str(i + 1), size=9, color=ROUGE, ha="center", weight="bold")
            self.axes_l.append((ln, lab, i))
        # niveaux (rouge) : planchers pleins, linteaux en tirets
        self.niv_l = []
        xg, xd = v.X(-0.8), v.X(mo.L + 0.7)
        for k, nv in enumerate(mo.niv):
            l1 = ligne(ax, [(xg, v.Y(nv["zf"])), (xd, v.Y(nv["zf"]))], lw=1.0, color=ROUGE, z=15)
            l2 = ligne(ax, [(xg, v.Y(nv["zl"])), (xd, v.Y(nv["zl"]))], lw=0.8, color=ROUGE, z=15, ls=(0, (6, 3)))
            lab = txt(ax, xd + 4, v.Y(nv["zf"]) - 8, nv["nom"], size=7.4, color=ROUGE)
            self.niv_l.append((l1, l2, lab, k, xg, xd))
        l1 = ligne(ax, [(xg, v.Y(mo.T)), (xd, v.Y(mo.T))], lw=1.0, color=ROUGE, z=15)
        self.niv_l.append((l1, None, txt(ax, xd + 4, v.Y(mo.T) - 8, "terrasse", size=7.4, color=ROUGE), len(mo.niv), xg, xd))
        # cotes : entraxes en bas, hauteurs d'étage à gauche, baie type par niveau
        self.cotes = []
        bords = [0.0] + list(mo.axes) + [mo.L]
        yc = v.ys + 62
        for a_, b_ in zip(bords[:-1], bords[1:]):
            self.cotes.append((cote_h(ax, v.X(a_), v.X(b_), yc, fm(b_ - a_)), 0.0))
        self.cotes.append((cote_h(ax, v.X(0), v.X(mo.L), yc + 30, f"longueur {fm(mo.L)}", size=8.2), 0.0))
        hs = mo.hauteurs_etage()
        zs = bornes_niveaux(mo)
        xv = v.X(0) - 22
        for k, h in enumerate(hs):
            self.cotes.append((cote_v(ax, xv, v.Y(zs[k]), v.Y(zs[k + 1]), fm(h)), 0.03 * k))
        self.ratios = []
        ib = min(1, mo.n - 1)
        xa, xb = mo.ouvrants[ib]
        for k, nv in enumerate(mo.niv):
            r, w, h = mo.ratio(k)
            zf, zl = nv["zf"] + 0.03, nv["zl"]
            a = cote_h(ax, v.X(xa), v.X(xb), v.Y(zl + 0.45), fm(w), size=7.2)
            b = cote_v(ax, v.X(xa) - 12, v.Y(zf), v.Y(zl), fm(h), size=7.2, bbox=False)
            tr = txt(ax, v.X((xa + xb) / 2), v.Y((zf + zl) / 2), f"h/l = {fm(r)}", size=8.2, color=BLEU, ha="center", weight="bold",
                     bbox=dict(fc="white", ec=BLEU, lw=0.6 * PX, pad=1.8, alpha=0.95))
            self.ratios.append((a + b, tr))
        # décroissance des hauteurs d'étage : un trait
        self.graph(v, hs, mo)
        rdc_texte(ax, v, mo)
        # légende
        yl = 1190
        txt(ax, 40, yl, "Légende", size=8.6, weight="bold")
        for j, (c, s, ls) in enumerate(((ROUGE, "axes de travées (traits-points), niveaux : planchers pleins, linteaux en tirets", None),
                                         (BLEU, "cotes en mètres : entraxes, hauteurs d'étage, baie type par niveau ; h / l = hauteur sur largeur", None),
                                         ("#9A9A9A", "élévation au trait, en gris clair", None))):
            ligne(ax, [(52, yl + 24 + j * 20), (92, yl + 24 + j * 20)], lw=1.4, color=c, z=14)
            txt(ax, 102, yl + 24 + j * 20, s, size=7.8)
        y = table_niveaux(ax, mo, 1410, h=29, size=8.6)
        notes(ax, y + 24, "Supposé", fac["suppose"][:2], size=8.4)
        cartouche(ax, fac, "Trame et proportions", v.ech, extra="axes, niveaux, cotes")

    def graph(self, v, hs, mo):
        """Hauteurs d'étage du bas vers le haut : un trait, et le verdict."""
        ax = self.ax
        gx0, gy0, gw, gh = 40, 1395, 520, 105
        ax.add_patch(Rectangle((gx0, gy0 - gh), gw, gh, fc="white", ec="#777777", lw=0.6 * PX, zorder=10))
        lo, hi = min(hs) - 0.15, max(hs) + 0.15
        pts = [(gx0 + 20 + i * (gw - 40) / max(len(hs) - 1, 1), gy0 - 10 - (h - lo) / (hi - lo) * (gh - 28)) for i, h in enumerate(hs)]
        ligne(ax, pts, lw=1.6, color=ROUGE, z=14)
        for (x, y), h, nom in zip(pts, hs, ["RDC"] + [n["nom"] for n in mo.niv]):
            ax.plot([x], [y], "o", ms=3, color=ROUGE, zorder=15)
            txt(ax, x, y - 9, fm(h), size=6.8, color=ROUGE, ha="center")
        etages = hs[1:]
        dec = etages[0] - etages[-1] if etages else 0
        verdict = ("hauteurs d'étage constantes (écart " + fm(abs(dec)) + " m)") if abs(dec) < 0.03 else \
                  (f"décroissance du bas vers le haut : {fm(etages[0])} → {fm(etages[-1])} m") if dec > 0 else f"croissance : {fm(etages[0])} → {fm(etages[-1])} m"
        txt(ax, gx0, gy0 - gh - 12, "Hauteurs d'étage, du RDC à R+n : " + verdict, size=7.6, color="#333333")

    def maj(self, p):
        # 0–0.35 : les axes tombent ; 0.30–0.58 : les niveaux ; 0.55–1 : cotes, puis rapports
        v = self.vue
        n = max(len(self.axes_l), 1)
        for ln, lab, i in self.axes_l:
            t = ease(seg_t(p, 0.0 + 0.18 * i / n, 0.17 + 0.18 * i / n))
            x = ln.get_xdata()[0]
            top = v.Y(self.mo.T) - 22
            y0 = top - 90 * (1 - t)
            ln.set_data([x, x], [y0, y0 + (v.ys - top) * t + 0 * (1 - t)])
            ln.set_alpha(min(1.0, t * 3 + 0.05))
            lab.set_alpha(t)
        m = max(len(self.niv_l), 1)
        for l1, l2, lab, k, xg, xd in self.niv_l:
            t = ease(seg_t(p, 0.32 + 0.22 * k / m, 0.44 + 0.22 * k / m))
            for ln in (l1, l2):
                if ln is not None:
                    xs, ys = ln.get_xdata(), ln.get_ydata()
                    ln.set_data([xg, xg + (xd - xg) * max(t, 1e-3)], [ys[0], ys[0]])
            lab.set_alpha(t)
        for arts, d in self.cotes:
            t = ease(seg_t(p, 0.58 + d * 0.8, 0.70 + d * 0.8))
            for a in arts:
                a.set_alpha(t)
        for j, (arts, tr) in enumerate(self.ratios):
            t = ease(seg_t(p, 0.74 + 0.04 * j, 0.86 + 0.04 * j))
            for a in arts:
                a.set_alpha(t)
            tr.set_alpha(t)
            tr.get_bbox_patch().set_alpha(t * 0.95)


# ---- 5. façade annotée ------------------------------------------------------------------------------------------------
GLOSSAIRE = [["Rez-de-chaussée", "Niveau de la rue ; ici des boutiques, étudié à part."],
             ["Bandeau", "Saillie horizontale peu épaisse qui marque un niveau de la façade."],
             ["Travée", "Bande verticale de la façade qui regroupe les baies superposées d'un même axe."],
             ["Trumeau", "Pan de mur plein entre deux baies voisines d'un même étage."],
             ["Baie", "Ouverture de la façade : ici des portes-fenêtres à petits-bois."],
             ["Appui", "Pièce qui ferme le bas d'une baie et rejette l'eau."],
             ["Garde-corps", "Barrière à barreaux qui protège une baie ou un balcon."],
             ["Balcon filant", "Balcon continu, porté par une dalle, qui court devant plusieurs baies."],
             ["Persiennes", "Volets à lames, repliés devant la baie ou ouverts de part et d'autre."],
             ["Corniche", "Moulure saillante qui couronne la façade."]]


class Annote(Scene):
    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        self.fac = fac
        ax = self.ax
        self.vue = v = Vue.plein(mo.L, mo.T, rel=0.60, y_haut=215, y_sol_min=0, y_sol_max=1180)
        entete(ax, fac, "Façade annotée")
        sol_trottoir(ax, v, mo.L, lw=2.4)
        t = trace_lignes(ax, v, mo, echelle_trait=0.9)
        ligne(ax, t["contour"], lw=2.4, z=8)
        for i, xc in enumerate(mo.axes):                          # numéros de travées
            txt(ax, v.X(xc), v.Y(mo.T) - 22, str(i + 1), size=8.2, color=ROUGE, ha="center", weight="bold")
        txt(ax, v.X(mo.L / 2), v.Y(mo.T) - 44, "numéros de travées", size=7, ha="center", color=ROUGE)
        self.labels = self.construire_labels(mo, v)
        self.dessiner_labels(v)
        rdc_texte(ax, v, mo)
        y = tableau(ax, v.ys + 62, ["Terme", "Définition"], GLOSSAIRE, [52, 250], h=27, size=8.4)
        y = notes(ax, y + 26, "Lu sur la photo", ["Seuls les éléments visibles sur la photo redressée sont nommés ; « probable » signale un matériau que la photo ne prouve pas.",
                                                    "Entresol, étage noble, brisis, terrasson, lucarne et souche de cheminée ne sont pas nommés : absents ou invisibles de la rue."],
                  largeur=124, size=8.4)
        notes(ax, y + 10, "Supposé", fac["suppose"][1:4], largeur=124, size=8.4)
        cartouche(ax, fac, "Façade annotée", v.ech, extra="vocabulaire du métier")

    def construire_labels(self, mo, v):
        """Étiquettes : côté gauche ou droit selon l'élément, en visant l'exemplaire le plus proche du bord pour éviter les croisements."""
        L = []
        a, n = mo.ouvrants, mo.n
        xc = lambda i: (a[i][0] + a[i][1]) / 2
        P = lambda x, z: (v.X(x), v.Y(z))
        L.append(("Rez-de-chaussée\n(boutiques, non relevé)", P(0.3, mo.z1 * 0.5), "g"))
        L.append(("Bandeau\n(pierre de taille probable)", P(mo.L - 0.3, mo.z1 - BANDEAU_H / 2), "d"))
        for nv in mo.niv:                                                        # garde-corps individuel : travée la plus à gauche
            ind = [g[0] for g in nv["groupes"] if len(g) == 1]
            if ind:
                L.append(("Garde-corps individuel\n(fonte probable)", P(xc(min(ind)), nv["zf"] + 0.5), "g"))
                break
        for nv in mo.niv:                                                        # balcon filant : groupe le plus à droite
            fil = [g for g in nv["groupes"] if len(g) > 1]
            if fil:
                g = fil[-1]
                L.append(("Balcon filant\n(dalle de pierre, garde-corps\nde fonte probables)", P(a[g[-1]][1] + 0.1, nv["zf"] - 0.2), "d"))
                break
        nv = mo.niv[min(1, len(mo.niv) - 1)]
        L.append(("Appui de baie\n(pierre probable)", P(a[n - 1][1] + 0.12, nv["zf"] - 0.02), "d"))
        ouv = [(k, i) for k, nv_ in enumerate(mo.niv) for i in range(n) if not nv_["pers"][i]]
        if ouv:
            k, i = min(ouv, key=lambda ki: (ki[1], ki[0]))                       # baie sans persiennes fermées, la plus à gauche
            nv = mo.niv[k]
            L.append(("Baie (porte-fenêtre)\net petits-bois (bois probable)", P(a[i][0] + 0.3, (nv["zf"] + nv["zl"]) / 2 + 0.3), "g"))
        fer = [(k, i) for k, nv_ in enumerate(mo.niv) for i in range(n) if nv_["pers"][i]]
        if fer:
            k, i = max(fer, key=lambda ki: (ki[1], ki[0]))                       # persiennes fermées, les plus à droite
            nv = mo.niv[k]
            L.append(("Persiennes\n(bois probable)", P(xc(i), (nv["zf"] + nv["zl"]) / 2 - 0.2), "d"))
        if n >= 2:
            xm = (mo.reg[0][1] + mo.reg[1][0]) / 2
            L.append(("Trumeau\n(pierre de taille probable)", P(xm, mo.niv[0]["zl"] + 0.9), "g"))
        L.append(("Travée n° 1\n(axe et baies superposées)", P(mo.axes[0], mo.niv[-1]["zf"] + 0.3), "g"))
        L.append(("Corniche haute\n(pierre probable)", P(mo.L - 0.3, mo.T - CORNICHE_H / 2), "d"))
        return L

    def dessiner_labels(self, v):
        ax = self.ax
        xg, xd = v.X(0) - 24, v.X(self.mo.L) + 24
        cote = {"g": [], "d": []}
        for texte, cible, s in self.labels:
            cote[s].append((texte, cible))
        self.items = []
        pas = 54
        for s, lst in cote.items():
            lst.sort(key=lambda it: it[1][1])
            ys = []
            for texte, cible in lst:
                y = cible[1]
                if ys and y < ys[-1] + pas:
                    y = ys[-1] + pas
                ys.append(y)
            exc = ys[-1] - (v.ys - 8) if ys else 0                      # remonte si le dernier dépasse le sol
            if exc > 0:
                ys = [y - exc for y in ys]
                for j in range(len(ys) - 2, -1, -1):
                    ys[j] = min(ys[j], ys[j + 1] - pas)
            for (texte, cible), y in zip(lst, ys):
                xe = xg if s == "g" else xd
                ln = ligne(ax, [(xe, y), cible], lw=0.7, color="#333333", z=12)
                pt = ax.plot([cible[0]], [cible[1]], "o", ms=2.6, color="#333333", zorder=13)[0]
                tx = txt(ax, xe + (-6 if s == "g" else 6), y, texte, size=8.2, ha="right" if s == "g" else "left", va="center")
                self.items.append((ln, pt, tx, cible[1], (xe, y), cible))

    def maj(self, p):
        # la façade est posée ; les étiquettes sortent une à une, du bas vers le haut
        n = max(len(self.items), 1)
        ordre = sorted(range(len(self.items)), key=lambda i: -self.items[i][3])
        rang = {i: r for r, i in enumerate(ordre)}
        for i, (ln, pt, tx, _, a, b) in enumerate(self.items):
            t = ease(seg_t(p, 0.10 + 0.78 * rang[i] / n, 0.10 + 0.78 * rang[i] / n + 0.22))
            ln.set_data([a[0], a[0] + (b[0] - a[0]) * t], [a[1], a[1] + (b[1] - a[1]) * t])
            ln.set_alpha(min(1, t * 4))
            pt.set_alpha(1.0 if t >= 0.99 else 0.0)
            tx.set_alpha(min(1, t * 2.5))


# ---- 6. calepinage ----------------------------------------------------------------------------------------------------
PIERRE_CAL, JOINT, GRIS = "#F0E4CC", "#8C7F69", "#6E6E6E"


def calepinage(mo, graine):
    """Assises de pierre : liste de pierres (x0, z0, x1, z1, genre), rangées par assise du bas vers le haut ; claveaux et harpes autour des baies."""
    rng = np.random.default_rng(zlib.crc32(graine.encode("utf-8")))
    z0, z1 = mo.z1, mo.T - CORNICHE_H
    nr = max(1, round((z1 - z0) / ASSISE))
    h = (z1 - z0) / nr
    enc = [mo.encadrement(xa, xb, zf, zl) for k, i, xa, xb, zf, zl in mo.baies()]
    rows, prev = [], []
    for r in range(nr):
        za, zb = z0 + r * h, z0 + (r + 1) * h
        zm = (za + zb) / 2
        cuts = []
        for (ex0, ez0, ex1, ez1) in enc:
            if ez0 - 1e-6 <= zm <= ez1 + 1e-6:
                wl, wr = (0.55, 0.30) if r % 2 == 0 else (0.30, 0.55)
                cuts.append((ex0 - wl, ex1 + wr, ex0, ex1, wl, wr))
        br, x = [0.0], 0.0
        while x < mo.L - 0.4:
            x += float(rng.choice([0.80, 1.00, 1.20]))
            while any(abs(x - q) < 0.2 for q in prev):
                x += 0.25
            if x < mo.L - 0.3:
                br.append(x)
        br.append(mo.L)
        prev = br
        stones = []
        for s0, s1 in zip(br[:-1], br[1:]):
            stones.append((s0, za, s1, zb, "mur"))       # les encadrements, dessinés par-dessus, recouvrent les pierres sous les baies
        for c in cuts:
            stones.append((c[0], za, c[2], zb, "harpe"))
            stones.append((c[3], za, c[1], zb, "harpe"))
        rows.append(stones)
    claveaux = []
    for (ex0, ez0, ex1, ez1) in enc:
        zl = ez1 - 0.20
        w = ex1 - ex0
        xc = (ex0 + ex1) / 2
        for j in range(-2, 3):
            if j == 0:
                continue
        pts = []
        for j in range(-2, 3):
            pts.append(((xc + j * w / 6, zl), (xc + j * w * 1.3 / 6 * 1.0, ez1)))
        claveaux.append(((ex0, zl, ex1, ez1), pts))
    return rows, claveaux, h


class Calepinage(Scene):
    def __init__(self, fac):
        super().__init__()
        self.mo = mo = Modele(fac)
        self.fac = fac
        ax = self.ax
        self.vue = v = Vue.plein(mo.L, mo.T, rel=0.88, y_haut=215)
        entete(ax, fac, "Calepinage de la pierre")
        sol_trottoir(ax, v, mo.L, lw=2.4)
        rows, claveaux, h = calepinage(mo, fac["voie"])
        self.h = h
        self.rows = rows
        self.z_rows = [mo.z1 + (r + 1) * h for r in range(len(rows))]
        corps = []
        # RDC non relevé : aplat clair, sans joints
        ex = extras(mo)
        corps.append(pc(ax, [v.rect((0, 0, mo.L, mo.z1))], "#E4E4E4", z=2, ec=NOIR, lw=0.9))
        rdc_texte(ax, v, mo)
        corps.append(pc(ax, [v.rect((0, mo.z1 - BANDEAU_H, mo.L, mo.z1))], PIERRE_CAL, z=3, ec=JOINT, lw=0.8))
        # pierres : une collection par genre
        murs = [v.rect(s[:4]) for rw in rows for s in rw if s[4] == "mur"]
        harpes = [v.rect(s[:4]) for rw in rows for s in rw if s[4] == "harpe"]
        corps.append(pc(ax, murs, PIERRE_CAL, z=3, ec=JOINT, lw=0.7))
        corps.append(pc(ax, harpes, "#E6D5B4", z=3.35, ec=JOINT, lw=0.9))
        # encadrement + claveaux
        cvx = []
        for r, pts in claveaux:
            corps.append(pc(ax, [v.rect(r)], "#E6D5B4", z=3.4, ec=JOINT, lw=0.9))
            for a, b in pts:
                cvx.append(tuple(v.pts([a, b])))
        corps.append(lc(ax, cvx, 0.8, JOINT, z=3.6))
        # corniche haute
        corps.append(pc(ax, [v.rect(ex["corniche"])], "#E6D5B4", z=3, ec=JOINT, lw=1.0))
        # baies et garde-corps en gris
        corps.append(pc(ax, [v.rect(r) for r in mo.cl["encadrement"]], "#EBDDC2", z=3.3, ec=JOINT, lw=0.8))
        corps.append(pc(ax, [v.rect(r) for r in mo.cl["vitrage"]], "#DADDE0", z=4))
        corps.append(pc(ax, [v.rect(r) for r in mo.cl["pers"]], "#C3C7CC", z=4.5, ec=GRIS, lw=0.7))
        corps.append(pc(ax, [v.rect(r) for r in mo.cl["balcon"]], "#E6D5B4", z=5, ec=JOINT, lw=1.0))
        par = {}
        for g in ("A", "B", "C"):
            for layer, kind, data, role in mo.cl[g]:
                if layer in ("BAIE_ENCADREMENT", "BAIE_BALCON"):
                    continue
                if layer == "BAIE_PERSIENNES":
                    continue
                p = poids(layer, kind, data)
                s = segs_poly(v.pts(data[0]), data[1]) if kind == "poly" else [tuple(v.pts([data[0], data[1]]))]
                par.setdefault(p, []).extend(s)
        for p, s in par.items():
            corps.append(lc(ax, s, WPX[p], GRIS, z=6))
        self.corps = [c for c in corps if c is not None]
        ligne(ax, v.pts([(0, 0), (0, mo.T), (mo.L, mo.T), (mo.L, 0), (0, 0)]), lw=2.4, z=8, cap="round")
        self.reveal = Reveal(ax, self.corps)
        txt(ax, 40, v.ys + 62, "Pierre : beige pâle ; joints : trait fin sombre ; menuiseries et garde-corps : gris.", size=8.4)
        y = notes(ax, v.ys + 88, "Lu / supposé",
                  [f"Hauteur d'assise supposée : {fm(ASSISE * 100, 0)} cm, répartie à {fm(h * 100, 1)} cm entre le haut du RDC et la corniche (la photo ne permet pas de la lire).",
                   "Joints verticaux décalés d'une assise à l'autre, placés au hasard (graine fixe) : ils ne sont pas relevés pierre par pierre.",
                   "Harpes de 55 et 30 cm en alternance de part et d'autre de chaque baie ; claveaux en plate-bande au-dessus du linteau.",
                   "RDC : non relevé, donc sans joints ni refends. Aucun refend ni bossage de soubassement n'est visible sur la photo."], largeur=124)
        tableau(ax, y + 14, ["Donnée", "Valeur"],
                [["Nombre d'assises (RDC et corniche exclus)", str(len(rows))], ["Hauteur d'assise retenue", f"{fm(h * 100, 1)} cm"],
                 ["Harpes (pierres en besace, de chaque côté des baies)", str(len(harpes))], ["Claveaux par baie", "4 joints (plate-bande)"]], [52, 600], h=27)
        cartouche(ax, fac, "Calepinage", v.ech, extra="assise supposée")
        self.reveal.jusqua(v.ys)

    def maj(self, p):
        v, mo = self.vue, self.mo
        n = len(self.z_rows)
        if p >= 1:
            self.reveal.jusqua(v.Y(mo.T + 1))
            return
        k = math.ceil(seg_t(p, 0.04, 0.92) * n)
        if k <= 0:
            self.reveal.jusqua(v.Y(mo.z1))
        elif k >= n:
            self.reveal.jusqua(v.Y(mo.T + 1))
        else:
            self.reveal.jusqua(v.Y(self.z_rows[k - 1]))


# --------------------------------------------------------------------------------------------------------------------
# Vidéo et orchestration
# --------------------------------------------------------------------------------------------------------------------
def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def progression(i):
    return min(1.0, i / (N_IMG - N_FIXE - 1))


def ecrire_mp4(images, chemin):
    """`images(i)` renvoie l'image RGBA 1080 × 1920 de la i-ème image (0 à 89). Renvoie False si ffmpeg est absent."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    cmd = [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", chemin]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(N_IMG):
            pr.stdin.write(images(i).tobytes())
        pr.stdin.close()
        err = pr.stderr.read().decode("utf-8", "replace")
        pr.stderr.close()
        if pr.wait() != 0:
            raise RuntimeError("ffmpeg : " + err[-400:])
    finally:
        if pr.poll() is None:
            pr.kill()
    return True


SCENES = [("elevation", Elevation), ("relief", Relief), ("teintes", TeintesGrille), ("trace", Trace), ("annote", Annote), ("calepinage", Calepinage)]


def _images_teintes(fac, grille):
    grande = TeintesGrande(fac)
    final = grille.rgba(1.0)

    def f(i):
        if i >= N_IMG - N_FIXE:
            return final
        t = i / (N_IMG - N_FIXE)                                # 0 → 1 sur les 70 premières images
        bloc = t * 5                                            # 4 variantes puis fondu vers la grille
        if bloc >= 4.2:
            grande.idx(3, 0.0)
            a = grande.rgba(1.0).astype(np.float32)
            k = min(1.0, (bloc - 4.2) / 0.8)
            return (a * (1 - k) + final.astype(np.float32) * k).astype(np.uint8)
        j = int(bloc)
        tt = min(1.0, max(0.0, (bloc - j - 0.55) / 0.35))
        grande.idx(j, ease(tt))
        return grande.rgba(1.0)
    return f, grande


def generer(fac, dossier, videos=True, say=None, seulement=None, planche_finale=True):
    """Produit les six dessins (PNG, et MP4 si `videos`) puis la planche dans `dossier`. Retourne {nom: {'png':…, 'mp4':…}}."""
    say = say or (lambda s: None)
    os.makedirs(dossier, exist_ok=True)
    res = {}
    for nom, cls in SCENES:
        if seulement and nom not in seulement:
            continue
        say(f"Dessin « {nom} »…")
        sc = cls(fac)
        png = os.path.join(dossier, nom + ".png")
        sc.png(png)
        res[nom] = {"png": nom + ".png", "mp4": None}
        if videos:
            mp4 = os.path.join(dossier, nom + ".mp4")
            try:
                if nom == "teintes":
                    images, grande = _images_teintes(fac, sc)
                    ok = ecrire_mp4(images, mp4)
                    grande.fermer()
                else:
                    ok = ecrire_mp4(lambda i, s=sc: s.rgba(progression(i)), mp4)
                if ok:
                    res[nom]["mp4"] = nom + ".mp4"
                else:
                    say("⚠ ffmpeg introuvable : vidéos non produites (installer ffmpeg ou imageio-ffmpeg).")
                    videos = False
            except Exception as ex:                       # noqa: BLE001
                say(f"⚠ Vidéo « {nom} » : {ex}")
        sc.fermer()
    if planche_finale:
        from . import planche
        try:
            res["planche"] = planche.planche(dossier, fac.get("voie", ""), fac.get("date"))
        except Exception as ex:                           # noqa: BLE001
            say(f"⚠ Planche : {ex}")
    return res


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Dessins d'architecte de la façade depuis facade.json")
    ap.add_argument("facade_json")
    ap.add_argument("dossier", nargs="?")
    ap.add_argument("--sans-video", action="store_true")
    ap.add_argument("--seulement", nargs="*")
    a = ap.parse_args(argv)
    fac = charger(a.facade_json)
    dossier = a.dossier or os.path.dirname(os.path.abspath(a.facade_json))
    print(json.dumps(generer(fac, dossier, videos=not a.sans_video, say=print, seulement=a.seulement), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
