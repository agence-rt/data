"""Planche : tous les dessins PNG d'un dossier de façade sur une seule feuille, sans les redessiner ni les déformer.

Deux sorties de la même mise en page (grille stricte, marges égales, petit titre par dessin, cartouche en bas) :
  planche.png  1080 × 1920 (portrait téléphone) ;
  planche.pdf  vrai format A3 portrait, 297 × 420 mm.
"""
import datetime
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                          # noqa: E402
import numpy as np                                                       # noqa: E402
from matplotlib.patches import Rectangle                                 # noqa: E402
from PIL import Image                                                    # noqa: E402

from . import APP_NAME, APP_VERSION                                      # noqa: E402

ORDRE = ["elevation", "relief", "teintes", "trace", "annote", "calepinage"]
TITRES = {"elevation": "Élévation au trait", "relief": "Ombres portées", "teintes": "Variantes de teintes", "trace": "Trame et proportions",
          "annote": "Façade annotée", "calepinage": "Calepinage de la pierre"}
EXCLUS = ("controle", "planche", "facade-redressee", "photo", "pano")      # photo de départ et images de contrôle


def dessins_du_dossier(dossier):
    """PNG du dossier hors photo de départ et contrôles ; ordre des dessins connus, puis ordre alphabétique."""
    tous = []
    for p in glob.glob(os.path.join(dossier, "*.png")):
        stem = os.path.splitext(os.path.basename(p))[0]
        if any(stem.lower().startswith(x) for x in EXCLUS):
            continue
        tous.append(p)
    cle = lambda p: (ORDRE.index(os.path.splitext(os.path.basename(p))[0]) if os.path.splitext(os.path.basename(p))[0] in ORDRE else 99, os.path.basename(p))
    return sorted(tous, key=cle)


def grille(n, ratios, W, H, m, g, ht, hc):
    """Choisit colonnes × lignes pour que les images (rapport conservé) soient le plus grandes possible. Retourne (cols, rows, cw, ch)."""
    zone_w = W - 2 * m
    zone_h = H - 2 * m - hc - g
    meilleur = None
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        cw = (zone_w - (cols - 1) * g) / cols
        ch = (zone_h - rows * ht - (rows - 1) * g) / rows
        if ch <= 0:
            continue
        surf = min(cw, ch * min(ratios)) * min(ch, cw / min(ratios))      # surface utile de la case la plus contrainte
        if meilleur is None or surf > meilleur[0]:
            meilleur = (surf, cols, rows, cw, ch)
    return meilleur[1:]


def planche(dossier, projet="", date=None, fichiers=None):
    """Assemble la planche. `fichiers` : liste de PNG (sinon tous ceux du dossier). Retourne {'png', 'pdf', 'fichiers'}."""
    chemins = list(fichiers) if fichiers else dessins_du_dossier(dossier)
    if not chemins:
        raise ValueError("Aucun dessin PNG dans le dossier.")
    imgs = [np.asarray(Image.open(p).convert("RGB")) for p in chemins]
    ratios = [im.shape[1] / im.shape[0] for im in imgs]
    date = date or datetime.datetime.now().strftime("%d/%m/%Y")
    sorties = {}
    for nom, (wp, hp, dpi, unit_pt) in {"png": (1080, 1920, 100, 0.72), "pdf": (297.0, 420.0, 100, 72 / 25.4)}.items():
        fig = plt.figure(figsize=(wp / 100, hp / 100) if nom == "png" else (297 / 25.4, 420 / 25.4), dpi=dpi, facecolor="white")
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, wp)
        ax.set_ylim(hp, 0)
        ax.axis("off")
        m, g, ht, hc = 0.04 * wp, 0.02 * wp, 0.03 * wp, 0.10 * wp
        cols, rows, cw, ch = grille(len(imgs), ratios, wp, hp, m, g, ht, hc)
        ch = min(ch, cw / min(ratios))                  # la case épouse la plus haute image : le titre reste collé à son dessin
        zone_h = hp - 2 * m - hc - g
        grille_h = rows * (ht + ch) + (rows - 1) * g
        y_top = m                                        # grille calée sous la marge haute, cartouche en bas
        grille_w = cols * cw + (cols - 1) * g
        x_left = m + ((wp - 2 * m) - grille_w) / 2
        for i, (p, im, ar) in enumerate(zip(chemins, imgs, ratios)):
            c, r = i % cols, i // cols
            x0 = x_left + c * (cw + g)
            y0 = y_top + r * (ht + ch + g)
            stem = os.path.splitext(os.path.basename(p))[0]
            ax.text(x0, y0 + ht * 0.62, f"{i + 1}. {TITRES.get(stem, stem)}", fontsize=min(ht * 0.5, cw * 0.062) * unit_pt, va="center", ha="left",
                    fontweight="bold", family="DejaVu Sans")
            h_img = min(ch, cw / ar)
            w_img = h_img * ar
            xi, yi = x0 + (cw - w_img) / 2, y0 + ht
            ax.imshow(im, extent=(xi, xi + w_img, yi + h_img, yi), aspect="auto", interpolation="lanczos" if nom == "png" else "none", zorder=2)
            ax.add_patch(Rectangle((xi, yi), w_img, h_img, fill=False, ec="#999999", lw=0.5 * unit_pt, zorder=3))
        ax.set_xlim(0, wp)
        ax.set_ylim(hp, 0)
        # cartouche
        yc = hp - m - hc
        ax.add_patch(Rectangle((m, yc), wp - 2 * m, hc, fc="white", ec="#111111", lw=0.9 * unit_pt, zorder=5))
        ax.plot([m + (wp - 2 * m) * 0.62] * 2, [yc, yc + hc], color="#111111", lw=0.6 * unit_pt, zorder=6)
        f1 = hc * 0.20 * unit_pt
        ax.text(m + hc * 0.15, yc + hc * 0.28, f"PROJET : {projet}".upper(), fontsize=f1 * 1.0, fontweight="bold", va="center", family="DejaVu Sans", zorder=7)
        ax.text(m + hc * 0.15, yc + hc * 0.56, "Planche de façade sur rue — élévation, ombres, teintes, trame, annotations, calepinage",
                fontsize=f1 * 0.55, va="center", family="DejaVu Sans", zorder=7)
        liste = ", ".join(os.path.splitext(os.path.basename(p))[0] for p in chemins)
        ligne = f"{APP_NAME} {APP_VERSION} · {len(imgs)} dessins : {liste}"
        ligne = ligne if len(ligne) <= 105 else ligne[:102] + "…"
        ax.text(m + hc * 0.15, yc + hc * 0.80, ligne, fontsize=f1 * 0.42, va="center", color="#555555", family="DejaVu Sans", zorder=7)
        ax.text(m + (wp - 2 * m) * 0.62 + hc * 0.15, yc + hc * 0.35, f"Date : {date}", fontsize=f1 * 0.8, va="center", family="DejaVu Sans", zorder=7)
        ax.text(m + (wp - 2 * m) * 0.62 + hc * 0.15, yc + hc * 0.70, "A3 portrait" if nom == "pdf" else "1080 × 1920", fontsize=f1 * 0.55, va="center",
                color="#555555", family="DejaVu Sans", zorder=7)
        chemin = os.path.join(dossier, "planche." + nom)
        fig.savefig(chemin, dpi=dpi if nom == "png" else 300, facecolor="white")
        plt.close(fig)
        sorties[nom] = "planche." + nom
    sorties["fichiers"] = [os.path.basename(p) for p in chemins]
    return sorties
