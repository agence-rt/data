"""Dessin des baies d'une façade sur rue à partir de l'élévation redressée : menuiseries, petits-bois, persiennes, garde-corps, appuis, balcons.

Les baies d'un même immeuble se ressemblent : on mesure sur l'image les traits qui varient (état des persiennes, balcons continus,
largeurs), on applique des types de baies réguliers, puis on étend aux niveaux non visibles sur la photo (copie du dernier niveau mesuré).
Repère : x = m depuis la limite gauche, z = m depuis le sol ; dessin vectoriel écrit en DXF (calques BAIE_*) et en aperçu PNG.
"""
import math
import os

import cv2
import numpy as np

# Dimensions types (m) d'une porte-fenêtre à persiennes de l'époque ; ajustables
JAMBAGE = 0.15          # pierre de chaque côté entre la travée mesurée et l'ouvrant
DORMANT = 0.055
GC_HAUT = 0.95          # garde-corps individuel
GC_COLLECTIF = 1.02     # balcon continu
ENTRAXE_BARREAUX = 0.115
DALLE_BALCON = 0.40
VOLET_LARGEUR = 0.42


def regulariser(travees):
    """Largeurs des travées : toutes semblables sauf la première (plus large) ; centres conservés."""
    if not travees:
        return []
    larg = [b - a for a, b in travees]
    autres = larg[1:] if len(larg) > 2 else larg
    wm = float(np.median(autres))
    out = []
    for i, (a, b) in enumerate(travees):
        c = (a + b) / 2
        w = (b - a) if i == 0 else wm
        out.append((c - w / 2, c + w / 2))
    return out


def _sombre(img, ech, x0, x1, z0, z1, seuil=95):
    H = img.shape[0]
    ya, yb = int(H - z1 * ech), int(H - z0 * ech)
    if ya < 0 or yb > H or yb <= ya or x1 <= x0:
        return None
    hsv = cv2.cvtColor(img[ya:yb, int(x0 * ech):int(x1 * ech)], cv2.COLOR_BGR2HSV)
    v, s = hsv[..., 2], hsv[..., 1]
    return float((v < seuil).mean()), float(((v > 150) & (s < 60)).mean())


def mesurer(img, ech, travees, planchers, linteaux):
    """Pour chaque niveau mesurable (jusqu'en haut de l'image) : persiennes fermées par travée, continuité des balcons.
    Renvoie une liste de dicts {niveau, persiennes: [bool], continu: [bool entre travées i et i+1]} ; None si hors image."""
    res = []
    H = img.shape[0]
    z_max = H / ech
    for k in range(len(linteaux)):
        zf, zl = planchers[k], linteaux[k]
        if zl + 0.2 > z_max:
            res.append(None)
            continue
        pers = []
        for a, b in travees:
            m = _sombre(img, ech, a + 0.25, b - 0.25, zf + 0.9, zl - 0.15)
            pers.append(bool(m and m[0] < 0.55 and m[1] < 0.2))
        cont = []
        for (a0, b0), (a1, b1) in zip(travees[:-1], travees[1:]):
            m = _sombre(img, ech, b0 + 0.1, a1 - 0.1, zf + 0.3, zf + 0.85, seuil=115)
            cont.append(bool(m and m[0] > 0.20))
        res.append({"niveau": k + 1, "persiennes": pers, "continu": cont})
    return res


def _groupes(continu, n):
    g, cur = [], [0]
    for i in range(1, n):
        if i - 1 < len(continu) and continu[i - 1]:
            cur.append(i)
        else:
            g.append(cur)
            cur = [i]
    g.append(cur)
    return g


class Dessin:
    def __init__(self):
        self.p = []          # (calque, type, données)

    def poly(self, layer, pts, close=True):
        self.p.append((layer, "poly", (list(pts), close)))

    def line(self, layer, a, b):
        self.p.append((layer, "line", (a, b)))

    def rect(self, layer, x0, z0, x1, z1):
        self.poly(layer, [(x0, z0), (x1, z0), (x1, z1), (x0, z1)], True)

    def text(self, layer, s, x, z, h=0.25):
        self.p.append((layer, "text", (s, x, z, h)))


COUL = {"BAIE_ENCADREMENT": "#9b8f7e", "BAIE_MENUISERIE": "#1b4f8a", "BAIE_PETITS_BOIS": "#4b86c4", "BAIE_PERSIENNES": "#b5791f",
        "BAIE_GARDE_CORPS": "#1d6b3a", "BAIE_APPUIS": "#7a4b2a", "BAIE_BALCON": "#444444", "FACADE": "#222222", "TEXTES": "#b00020"}


def _baie(d, x0, x1, zf, zl, persienne, large):
    """Une porte-fenêtre : encadrement, dormant, deux ouvrants, petits-bois, persiennes (fermées ou ouvertes), appui."""
    wo = x1 - x0 - 2 * JAMBAGE
    xa, xb = x0 + JAMBAGE, x1 - JAMBAGE
    z0 = zf + 0.03
    d.rect("BAIE_ENCADREMENT", xa - 0.14, zf, xb + 0.14, zl + 0.20)                  # encadrement / embrasure en pierre
    d.rect("BAIE_MENUISERIE", xa, z0, xb, zl)                                          # dormant
    d.rect("BAIE_MENUISERIE", xa + DORMANT, z0 + DORMANT, xb - DORMANT, zl - DORMANT)
    xm = (xa + xb) / 2
    d.line("BAIE_MENUISERIE", (xm, z0 + DORMANT), (xm, zl - DORMANT))                  # rencontre des deux ouvrants
    cols = 3 if large else 2
    rows = max(3, int(round((zl - z0) / 0.36)))
    for half in ((xa + DORMANT, xm), (xm, xb - DORMANT)):                              # petits-bois de chaque ouvrant
        for c in range(1, cols):
            x = half[0] + (half[1] - half[0]) * c / cols
            d.line("BAIE_PETITS_BOIS", (x, z0 + DORMANT), (x, zl - DORMANT))
        for r in range(1, rows):
            z = z0 + DORMANT + (zl - z0 - 2 * DORMANT) * r / rows
            d.line("BAIE_PETITS_BOIS", (half[0], z), (half[1], z))
    if persienne:                                                                      # persiennes repliées : lames verticales
        n = int(wo / 0.075)
        d.rect("BAIE_PERSIENNES", xa, z0, xb, zl)
        for i in range(1, n):
            x = xa + wo * i / n
            d.line("BAIE_PERSIENNES", (x, z0), (x, zl))
    else:                                                                              # volets ouverts de part et d'autre
        for xs in ((xa - VOLET_LARGEUR, xa), (xb, xb + VOLET_LARGEUR)):
            d.rect("BAIE_PERSIENNES", xs[0], z0, xs[1], zl)
            z = z0 + 0.05
            while z < zl - 0.03:
                d.line("BAIE_PERSIENNES", (xs[0], z), (xs[1], z))
                z += 0.045
    d.line("BAIE_APPUIS", (xa - 0.18, zf), (xb + 0.18, zf))                            # seuil / appui
    d.line("BAIE_APPUIS", (xa - 0.18, zf - 0.05), (xb + 0.18, zf - 0.05))
    d.line("BAIE_APPUIS", (xa - 0.18, zf - 0.05), (xa - 0.18, zf))
    d.line("BAIE_APPUIS", (xb + 0.18, zf - 0.05), (xb + 0.18, zf))


def _garde_corps(d, x0, x1, zf, haut):
    """Garde-corps : lisse haute, lisse basse, montants d'extrémité, barreaux à entraxe régulier."""
    zb, zh = zf + 0.06, zf + haut
    d.rect("BAIE_GARDE_CORPS", x0, zb, x1, zh)
    d.line("BAIE_GARDE_CORPS", (x0, zh - 0.045), (x1, zh - 0.045))
    d.line("BAIE_GARDE_CORPS", (x0, zb + 0.09), (x1, zb + 0.09))
    n = max(2, int((x1 - x0) / ENTRAXE_BARREAUX))
    for i in range(1, n):
        x = x0 + (x1 - x0) * i / n
        d.line("BAIE_GARDE_CORPS", (x, zb + 0.09), (x, zh - 0.045))


def construire(f, forme, etat, travees_reg):
    """Dessin vectoriel des baies de tous les niveaux. `etat` : mesures par niveau (None = hors photo → copie du dernier niveau mesuré)."""
    d = Dessin()
    L = forme["longueur"]
    d.rect("FACADE", 0, 0, L, forme["terrasse"]["dalle"])
    d.line("FACADE", (0, forme["rdc"]["z1"]), (L, forme["rdc"]["z1"]))
    d.text("TEXTES", "RDC (étudié à part)", L / 2 - 2, forme["rdc"]["z1"] / 2)
    n = len(travees_reg)
    mesures = [e for e in etat if e]
    for k, nv in enumerate(forme["niveaux"]):
        zf, zl = nv["plancher"], nv["linteau"]
        e = etat[k] if k < len(etat) and etat[k] else (mesures[-1] if mesures else {"persiennes": [False] * n, "continu": [False] * (n - 1)})
        # k = 0 : garde-corps individuels ; niveaux suivants : balcons continus tels que mesurés (ou copie du dernier niveau mesuré)
        for i, (a, b) in enumerate(travees_reg):
            _baie(d, a, b, zf, zl, e["persiennes"][i], large=(i == 0))
        grp = _groupes(e["continu"], n)
        for g in grp:
            a, b = travees_reg[g[0]][0] + JAMBAGE - 0.06, travees_reg[g[-1]][1] - JAMBAGE + 0.06
            if len(g) > 1:
                _garde_corps(d, a - 0.1, b + 0.1, zf, GC_COLLECTIF)
                d.rect("BAIE_BALCON", a - 0.15, zf - DALLE_BALCON, b + 0.15, zf)       # dalle de balcon filante
            else:
                _garde_corps(d, a - 0.05, b + 0.05, zf, GC_HAUT)
        d.text("TEXTES", f"{nv['nom']}  plancher {zf:.2f}  linteau {zl:.2f}", L + 0.4, zf + 0.2, 0.22)
    for i, (a, b) in enumerate(travees_reg, 1):
        d.text("TEXTES", str(i), (a + b) / 2 - 0.1, forme["rdc"]["z1"] + 0.15, 0.35)
    return d


def ecrire_dxf(d, chemin):
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    for i, (nom, _) in enumerate(COUL.items(), 1):
        doc.layers.add(nom, color=i)
    for layer, kind, data in d.p:
        if kind == "poly":
            msp.add_lwpolyline(data[0], close=data[1], dxfattribs={"layer": layer})
        elif kind == "line":
            msp.add_line(data[0], data[1], dxfattribs={"layer": layer})
        else:
            msp.add_text(data[0], height=data[3], dxfattribs={"layer": layer, "insert": (data[1], data[2])})
    doc.saveas(chemin)


def apercu(d, chemin, photo=None, ech=80, zmax=None, longueur=None):
    """PNG du dessin ; avec `photo` (élévation redressée), photo et dessin côte à côte sur la même emprise."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    L = longueur or 21.0
    zmax = zmax or 19.0
    fig, axes = plt.subplots(1, 2 if photo is not None else 1, figsize=(24 if photo is not None else 13, 11), squeeze=False)
    axes = axes[0]
    ax = axes[-1]
    for layer, kind, data in d.p:
        c = COUL.get(layer, "k")
        if kind == "poly":
            xs, zs = zip(*data[0])
            ax.plot(list(xs) + ([xs[0]] if data[1] else []), list(zs) + ([zs[0]] if data[1] else []), color=c, lw=0.8)
        elif kind == "line":
            ax.plot([data[0][0], data[1][0]], [data[0][1], data[1][1]], color=c, lw=0.6)
        else:
            ax.text(data[1], data[2], data[0], fontsize=7, color=c)
    ax.set_xlim(-1, L + 8)
    ax.set_ylim(0, zmax)
    ax.set_aspect("equal")
    ax.set_title("Dessin des baies (menuiseries, persiennes, garde-corps, appuis)")
    if photo is not None:
        H, W = photo.shape[:2]
        axes[0].imshow(cv2.cvtColor(photo, cv2.COLOR_BGR2RGB), extent=(0, W / ech, 0, H / ech))
        axes[0].set_xlim(-1, L + 8)
        axes[0].set_ylim(0, zmax)
        axes[0].set_title("Photo redressée")
    fig.tight_layout()
    fig.savefig(chemin, dpi=85)
    plt.close(fig)


def ecrire(f, forme, img, ech, travees, planchers, linteaux, dossier):
    reg = regulariser(travees)
    etat = mesurer(img, ech, reg, planchers, linteaux)
    d = construire(f, forme, etat, reg)
    ecrire_dxf(d, os.path.join(dossier, "baies.dxf"))
    apercu(d, os.path.join(dossier, "5_baies_dessin.png"), photo=img, ech=ech, zmax=min(img.shape[0] / ech, 12.5), longueur=f.longueur)
    apercu(d, os.path.join(dossier, "6_baies_facade_entiere.png"), longueur=f.longueur, zmax=forme["terrasse"]["dalle"] + 1)
    return {"travees_regularisees": [[round(a, 2), round(b, 2)] for a, b in reg], "etat": etat}
