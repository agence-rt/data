"""Étapes 2, 5 et 6 du process façade : plan de la façade (PNG), trame (axes, niveaux, cases de baies) et fichier DXF livré.

Repère de l'élévation : x = abscisse le long de la façade, de gauche à droite pour quelqu'un qui la regarde depuis la rue ; y = altitude NGF.
La coupe est posée à droite de l'élévation (rue à gauche) et le plan en dessous (rue en haut), à la même échelle (1 unité = 1 m)."""
import math
import os
from shapely import transform as shp_transform

import numpy as np

from . import baies as baies_mod

RAPPORT_ARRONDI = 1


# ------------------------------------------------------------------ repère local

def _loc(f):
    ox, oy = f.origine
    d, n = f.droite, f.sortant

    def fn(xy):
        x, y = xy[:, 0] - ox, xy[:, 1] - oy
        return np.column_stack([x * d[0] + y * d[1], x * n[0] + y * n[1]])
    return fn


def preparer_plan(f, parc, bats, voies, contexte):
    """Géométries du plan dans le repère de la façade : x le long de la façade, v = distance vers la rue (>0 dehors)."""
    from shapely.geometry import box
    fn = _loc(f)
    fenetre = box(-25, -35, f.longueur + 25, 15)           # le plan se limite aux abords immédiats de la façade
    tr = lambda g: shp_transform(g, fn).intersection(fenetre)
    out = {"parcelle": tr(parc.geom), "batiments": [tr(b.geom) for b in bats], "voies": [(v.nom, tr(v.geom)) for v in voies if not tr(v.geom).is_empty],
           "voisines": [], "bati_voisin": []}
    for k, key in (("voisines", "parcelles"), ("bati_voisin", "batiments")):
        for it in (contexte or {}).get(key, []) or []:
            g = getattr(it, "geom", it)
            try:
                g2 = tr(g)
                if not g2.is_empty:
                    out[k].append(g2)
            except Exception:
                pass
    return out


def plan_cadastre_png(f, parc, bats, voies, contexte, chemin, adresse=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pl = preparer_plan(f, parc, bats, voies, contexte)
    fig, ax = plt.subplots(figsize=(11, 8))

    def trace(g, **kw):
        for p in ([g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))):
            ax.plot(*p.exterior.xy, **kw)

    for g in pl["voisines"]:
        trace(g, color="#bbbbbb", lw=0.6)
    for g in pl["bati_voisin"]:
        trace(g, color="#888888", lw=0.8)
    trace(pl["parcelle"], color="#2a7f2a", lw=1.6)
    for g in pl["batiments"]:
        trace(g, color="#c0392b", lw=1.6)
    for nom, g in pl["voies"]:
        for ls in ([g] if g.geom_type == "LineString" else list(g.geoms)):
            ax.plot(*ls.xy, color="#1f5fbf", lw=2, ls="--")
            c = ls.interpolate(0.5, normalized=True)
            ax.text(c.x, c.y, nom, color="#1f5fbf", fontsize=9, ha="center")
    L = f.longueur
    ax.plot([0, L], [0, 0], color="#e67e00", lw=4)
    ax.annotate("", xy=(0, 1.5), xytext=(L, 1.5), arrowprops=dict(arrowstyle="<->", color="#e67e00"))
    ax.text(L / 2, 2.0, f"façade sur {f.voie} : {L:.2f} m".replace(".", ","), color="#e67e00", ha="center", fontsize=11, weight="bold")
    ax.set_xlim(-18, L + 18)
    ax.set_ylim(-(L * 0.9 + 6), 22)
    ax.set_aspect("equal")
    ax.set_title(f"{adresse} — façade étudiée (orange), parcelle (vert), bâtiments (rouge)", fontsize=10)
    ax.set_xlabel("m le long de la façade (de gauche à droite, vue depuis la rue)")
    ax.set_ylabel("m vers la rue (+)")
    fig.tight_layout()
    fig.savefig(chemin, dpi=120)
    plt.close(fig)


# ------------------------------------------------------------------ étape 5 : trame

def construire_trame(L, h_haut, elev, segs, niv, f, hyp, avec_photo):
    """Axes de travées, niveaux courants (R+1 … haut de façade) et cases de baies ; le RDC reste à part (haut seulement)."""
    pl = list(elev.get("planchers_m") or [])
    li = list(elev.get("linteaux_m") or [])
    rdc = elev.get("corniche_rdc_m")
    if not (pl and li and segs):
        # sans photo exploitable : niveaux estimés d'après la hauteur et la BD TOPO ; pas de travées
        n = max(2, int(round(h_haut / 2.9)))
        h_rdc = f.r.get("h_rdc") or min(max(h_haut / n * 1.15, 2.6), 4.8)
        pas = (h_haut - h_rdc) / max(n - 1, 1)
        pl = [h_rdc + k * pas for k in range(n)]
        li = []
        rdc = h_rdc
        segs = []
        hyp.append("Pas de photo exploitable : niveaux estimés d'après la hauteur (RDC + niveaux égaux), aucune travée ni baie relevée.")
    ref = baies_mod.regulariser(segs) if segs else []
    axes = [round((a + b) / 2, 3) for a, b in ref]
    larg = [round(b - a, 3) for a, b in ref]
    niveaux = []
    for k in range(len(pl) - 1):
        zf, zd = pl[k], pl[k + 1]
        niveaux.append({"nom": f"R+{k + 1}", "plancher": round(zf, 3), "dessus_dalle": round(zd, 3), "hauteur_etage": round(zd - zf, 3),
                        "linteau": round(li[k], 3) if k < len(li) else None})
    bayes, rapports = [], []
    for nv in niveaux:
        if nv["linteau"] is None:
            continue
        hs = []
        for i, (a, b) in enumerate(ref, 1):
            h = nv["linteau"] - nv["plancher"]
            bayes.append({"niveau": nv["nom"], "travee": i, "x0": round(a, 3), "x1": round(b, 3), "z0": nv["plancher"], "z1": nv["linteau"],
                          "largeur": round(b - a, 3), "hauteur": round(h, 3), "rapport_h_l": round(h / (b - a), 2)})
            hs.append(h / (b - a))
        rapports.append({"niveau": nv["nom"], "baie_type_largeur": round(float(np.median(larg[1:] or larg)), 3), "baie_type_hauteur": round(nv["linteau"] - nv["plancher"], 3),
                         "rapport_h_l": round(float(np.median(hs)), 2)})
    pas = [n["hauteur_etage"] for n in niveaux]
    if elev.get("niveaux_extrapoles"):
        hyp.append(f"Seuls le haut du RDC et le linteau de R+1 sont lus sur la photo ; les niveaux supérieurs sont extrapolés au même pas "
                   f"({np.median(pas):.2f} m = hauteur de façade lue dans le nuage ÷ nombre de niveaux), avec une épaisseur de dalle supposée de "
                   f"{elev.get('e_dalle_supposee_m', 0.9):.2f} m.")
    if bayes:
        hyp.append("Cases de baies tracées du plancher au linteau : allèges, appuis et garde-corps ne sont pas relevés à cette étape.")
        hyp.append("Largeurs de baies régularisées : toutes égales à la médiane des travées, sauf la travée 1 qui garde sa largeur lue.")
    gauche = round(ref[0][0], 2) if ref else None
    droite = round(L - ref[-1][1], 2) if ref else None
    if ref and gauche is not None and abs(gauche - droite) > 1.2:
        hyp.append(f"Marges asymétriques entre la façade et les travées extrêmes (gauche {gauche} m, droite {droite} m) : vérifier la largeur et la dernière travée.")
    if elev.get("travees_ajoutees_par_modele"):
        hyp.append(f"{elev['travees_ajoutees_par_modele']} travée(s) ajoutée(s) par corrélation avec le gabarit moyen (non vue(s) franchement).")
    return {"longueur_m": round(L, 2), "hauteur_facade_m": round(h_haut, 2), "rdc_haut_m": None if rdc is None else round(float(rdc), 3),
            "n_travees": len(ref), "axes_m": axes, "largeurs_m": larg, "marge_gauche_m": gauche, "marge_droite_m": droite,
            "n_niveaux": len(niveaux), "niveaux": niveaux, "pas_niveau_m": [round(p, 3) for p in pas], "baies": bayes, "rapports_h_l": rapports,
            "niveaux_extrapoles": bool(elev.get("niveaux_extrapoles")), "avec_photo": bool(avec_photo)}


def trame_png(trame, plat, ech, chemin):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    L, H = trame["longueur_m"], trame["hauteur_facade_m"] + 1.0
    fig, ax = plt.subplots(figsize=(13, 11))
    if plat is not None:
        z = plat.shape[0] / ech
        ax.imshow(plat[:, :, ::-1], extent=[0, plat.shape[1] / ech, 0, z], alpha=0.55)
    for b in trame["baies"]:
        ax.add_patch(Rectangle((b["x0"], b["z0"]), b["largeur"], b["hauteur"], fill=False, ec="#1f5fbf", lw=1.0))
    for a in trame["axes_m"]:
        ax.plot([a, a], [0, trame["hauteur_facade_m"]], color="#c0392b", lw=0.8, ls="-.")
    for i, a in enumerate(trame["axes_m"], 1):
        ax.text(a, trame["hauteur_facade_m"] + 0.15, str(i), color="#c0392b", ha="center", weight="bold")
    if trame["rdc_haut_m"]:
        ax.axhline(trame["rdc_haut_m"], color="#c0392b", lw=1.2)
        ax.text(L + 0.2, trame["rdc_haut_m"] - 0.45, "RDC étudié à part", va="center", fontsize=8)
    for n in trame["niveaux"]:
        ax.axhline(n["dessus_dalle"], color="#c0392b", lw=0.7)
        ax.text(L + 0.2, n["plancher"] + 0.1, f"{n['nom']}  {n['plancher']:.2f}", fontsize=8, va="bottom")
    for r in trame["rapports_h_l"]:
        n = next(x for x in trame["niveaux"] if x["nom"] == r["niveau"])
        ax.text(-0.2, (n["plancher"] + n["dessus_dalle"]) / 2, f"{r['niveau']}  h/l = {r['rapport_h_l']:.1f}".replace(".", ","), fontsize=8, ha="right", va="center", color="#1f5fbf")
    ax.set_xlim(-4, L + 6)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.set_title("Trame de la façade : axes de travées (rouge), niveaux, cases de baies (bleu) — z en m au-dessus du sol", fontsize=10)
    fig.tight_layout()
    fig.savefig(chemin, dpi=110)
    plt.close(fig)


# ------------------------------------------------------------------ étape 6 : DXF

LAYERS = [("FAC_CONTOUR", 7), ("FAC_RDC", 8), ("FAC_NIVEAUX", 1), ("FAC_AXES", 1), ("FAC_BAIES", 5), ("FAC_COTES", 4), ("FAC_TEXTES", 7), ("FAC_PHOTO", 9),
          ("COUPE_PROFIL", 1), ("COUPE_SOL", 3), ("COUPE_VOISINS", 8), ("COUPE_NIVEAUX", 2), ("COUPE_TEXTES", 7),
          ("PLAN_PARCELLE", 3), ("PLAN_BATI", 1), ("PLAN_VOISINS", 8), ("PLAN_VOIE", 5), ("PLAN_COTES", 4), ("PLAN_TEXTES", 7), ("CARTOUCHE", 7)]


def _txt(msp, s, x, y, h, layer, align=None):
    t = msp.add_text(s, height=h, dxfattribs={"layer": layer})
    t.dxf.insert = (x, y)
    if align:
        from ezdxf.enums import TextEntityAlignment
        t.set_placement((x, y), align=align)
    return t


def _cote_h(msp, x0, x1, y, txt, layer="FAC_COTES", h=0.22):
    msp.add_line((x0, y), (x1, y), dxfattribs={"layer": layer})
    for x in (x0, x1):
        msp.add_line((x, y - 0.12), (x, y + 0.12), dxfattribs={"layer": layer})
    from ezdxf.enums import TextEntityAlignment
    _txt(msp, txt, (x0 + x1) / 2, y + 0.1, h, layer, TextEntityAlignment.BOTTOM_CENTER)


def _cote_v(msp, x, y0, y1, txt, layer="FAC_COTES", h=0.22):
    msp.add_line((x, y0), (x, y1), dxfattribs={"layer": layer})
    for y in (y0, y1):
        msp.add_line((x - 0.12, y), (x + 0.12, y), dxfattribs={"layer": layer})
    from ezdxf.enums import TextEntityAlignment
    t = _txt(msp, txt, x - 0.1, (y0 + y1) / 2, h, layer, TextEntityAlignment.MIDDLE_RIGHT)


def _poly(msp, g, layer):
    for p in ([g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))):
        msp.add_lwpolyline(list(p.exterior.coords), dxfattribs={"layer": layer})
        for r in p.interiors:
            msp.add_lwpolyline(list(r.coords), dxfattribs={"layer": layer})


def ecrire_dxf(d, chemin):
    """DXF unique : élévation (x le long de la façade, y = NGF), coupe à droite (même y), plan dessous. Mètres."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    for n, c in LAYERS:
        doc.layers.add(n, color=c)
    msp = doc.modelspace()
    L, sol, tr = d["L"], d["sol"], d["trame"]
    Z = lambda z: sol + z
    top = tr["hauteur_facade_m"]

    # photo redressée en fond
    ph = d.get("photo")
    if ph:
        import cv2
        im = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(chemin)), ph["fichier"]))
        if im is not None:
            idef = doc.add_image_def(filename=ph["fichier"], size_in_pixel=(im.shape[1], im.shape[0]))
            msp.add_image(idef, insert=(0, Z(0)), size_in_units=(L, ph["z_haut"]), rotation=0, dxfattribs={"layer": "FAC_PHOTO"})

    # contour de la façade, sol, RDC, niveaux
    msp.add_lwpolyline([(0, Z(0)), (0, Z(top)), (L, Z(top)), (L, Z(0))], dxfattribs={"layer": "FAC_CONTOUR"})
    msp.add_line((-2, Z(0)), (L + 2, Z(0)), dxfattribs={"layer": "FAC_CONTOUR"})
    if tr["rdc_haut_m"]:
        msp.add_line((0, Z(tr["rdc_haut_m"])), (L, Z(tr["rdc_haut_m"])), dxfattribs={"layer": "FAC_RDC"})
        _txt(msp, f"RDC — étudié à part (haut {Z(tr['rdc_haut_m']):.2f} NGF)", L / 2, Z(tr["rdc_haut_m"] / 2), 0.3, "FAC_TEXTES", TextEntityAlignment.MIDDLE_CENTER)
    for n in tr["niveaux"]:
        msp.add_line((0, Z(n["dessus_dalle"])), (L, Z(n["dessus_dalle"])), dxfattribs={"layer": "FAC_NIVEAUX"})
        _txt(msp, f"{n['nom']}  plancher {Z(n['plancher']):.2f} NGF", L + 0.4, Z(n["plancher"]) + 0.05, 0.22, "FAC_TEXTES")
    # axes des travées et cases de baies
    for i, a in enumerate(tr["axes_m"], 1):
        msp.add_line((a, Z(0) - 0.6), (a, Z(top) + 0.5), dxfattribs={"layer": "FAC_AXES", "linetype": "CENTER"})
        _txt(msp, str(i), a, Z(top) + 0.65, 0.3, "FAC_TEXTES", TextEntityAlignment.BOTTOM_CENTER)
    for b in tr["baies"]:
        msp.add_lwpolyline([(b["x0"], Z(b["z0"])), (b["x1"], Z(b["z0"])), (b["x1"], Z(b["z1"])), (b["x0"], Z(b["z1"]))], close=True,
                           dxfattribs={"layer": "FAC_BAIES"})
    # cotes : largeurs de travées et entraxes sous le sol, hauteurs d'étage à gauche, rapports h/l
    y_c = Z(0) - 1.2
    for i, (a, w) in enumerate(zip(tr["axes_m"], tr["largeurs_m"])):
        _cote_h(msp, a - w / 2, a + w / 2, y_c, f"{w:.2f}".replace(".", ","))
    for a0, a1 in zip(tr["axes_m"][:-1], tr["axes_m"][1:]):
        _cote_h(msp, a0, a1, y_c - 0.8, f"{a1 - a0:.2f}".replace(".", ","))
    _cote_h(msp, 0, L, y_c - 1.7, f"{L:.2f} m".replace(".", ","))
    x_c = -1.2
    if tr["rdc_haut_m"]:
        _cote_v(msp, x_c, Z(0), Z(tr["rdc_haut_m"]), f"RDC {tr['rdc_haut_m']:.2f}".replace(".", ","))
    for n in tr["niveaux"]:
        _cote_v(msp, x_c, Z(n["plancher"]), Z(n["dessus_dalle"]), f"{n['nom']} {n['hauteur_etage']:.2f}".replace(".", ","))
    for r in tr["rapports_h_l"]:
        n = next(x for x in tr["niveaux"] if x["nom"] == r["niveau"])
        _txt(msp, f"baie {r['baie_type_largeur']:.2f} x {r['baie_type_hauteur']:.2f}  h/l = {r['rapport_h_l']:.1f}".replace(".", ","), -2.6,
             Z((n["plancher"] + n["dessus_dalle"]) / 2), 0.2, "FAC_COTES", TextEntityAlignment.MIDDLE_RIGHT)

    # coupe à droite : x = q - q_in + x0 (rue à gauche), y = NGF
    cp_ = d.get("coupe")
    x0c = L + 14.0
    if cp_:
        r, pr, ctx = cp_["r"], cp_["profil"], cp_["contexte"] or {}
        qi = r.get("q_in", 0.0)
        qmin = min([p[0] for p in ctx.get("sol", [])] + [v["q0"] for v in ctx.get("voisins", [])] + [p[0] for p in pr] + [qi])
        x0c = L + 10.0 + (qi - qmin)                       # la rue et les immeubles d'en face sont à gauche de la façade de la coupe, hors de l'élévation
        sc = r.get("sol", sol)
        X = lambda q: x0c + q - qi
        msp.add_lwpolyline([(X(q), h + sc) for q, h in pr], dxfattribs={"layer": "COUPE_PROFIL"})
        if len(ctx.get("sol", [])) >= 2:
            msp.add_lwpolyline([(X(q), h + sc) for q, h in ctx["sol"]], dxfattribs={"layer": "COUPE_SOL"})
        for k, v in enumerate(ctx.get("voisins", []), 1):
            msp.add_lwpolyline([(X(q), h + sc) for q, h in v["profil"]], dxfattribs={"layer": "COUPE_VOISINS"})
            _txt(msp, f"voisin {k} : {v['hmax'] + sc:.2f} NGF", X(v["q0"]), v["hmax"] + sc + 0.5, 0.3, "COUPE_TEXTES")
        for n in tr["niveaux"]:
            msp.add_line((X(qi) - 2.0, Z(n["plancher"])), (X(qi), Z(n["plancher"])), dxfattribs={"layer": "COUPE_NIVEAUX"})
        for lib, key in (("égout", "egout"), ("faîtage", "faitage")):
            v = d.get(key)                                    # altitudes NGF lues sur la façade (nuage)
            if v is not None:
                _txt(msp, f"{lib} {v:.2f} NGF", X(qi) + 0.3, v, 0.3, "COUPE_TEXTES")
        pen = r.get("pente_deg")
        _txt(msp, "COUPE perpendiculaire à la rue" + (f" — pente de toit {pen:.0f}°" if pen else ""), x0c - 10, Z(top) + 12, 0.45, "COUPE_TEXTES")
    _txt(msp, f"ÉLÉVATION — {d['voie']}", 0, Z(top) + 2.2, 0.5, "FAC_TEXTES")

    # plan sous l'élévation : façade à v = 0 ; rue vers le haut
    pl = d.get("plan")
    if pl:
        y0 = Z(0) - 8.0
        sh = lambda g: shp_transform(g, lambda a: np.column_stack([a[:, 0], a[:, 1] + y0]))
        for g in pl["voisines"]:
            _poly(msp, sh(g), "PLAN_VOISINS")
        for g in pl["bati_voisin"]:
            _poly(msp, sh(g), "PLAN_VOISINS")
        _poly(msp, sh(pl["parcelle"]), "PLAN_PARCELLE")
        for g in pl["batiments"]:
            _poly(msp, sh(g), "PLAN_BATI")
        for nom, g in pl["voies"]:
            for ls in ([g] if g.geom_type == "LineString" else list(g.geoms)):
                msp.add_lwpolyline([(a, b + y0) for a, b in ls.coords], dxfattribs={"layer": "PLAN_VOIE", "linetype": "DASHED"})
        _cote_h(msp, 0, L, y0 + 1.2, f"façade {L:.2f} m".replace(".", ","), layer="PLAN_COTES")
        _txt(msp, "PLAN (rue en haut)", 0, y0 - 37, 0.45, "PLAN_TEXTES")

    # cartouche
    yq = Z(0) - 8.0 - 42.0
    lignes = [f"{d['adresse']} — façade sur {d['voie']}", f"AGOA DATA {d['version']} — {d['date']}",
              "Sources : IGN LiDAR HD (nuage de points), IGN BD TOPO et Parcellaire Express, Panoramax (photos libres, CC-BY-SA).",
              "Repère : mètres ; élévation x le long de la façade (vue depuis la rue), y = altitude NGF ; coupe et plan à la même échelle."]
    for v in (ph or {}).get("vues", []):
        lignes.append(f"Photo Panoramax {v['id'][:8]} — {v.get('producteur') or 'auteur non indiqué'} — {v.get('date') or ''}")
    lignes += ["Hypothèses : " + h for h in d.get("hypotheses", [])]
    for i, s in enumerate(lignes):
        _txt(msp, s, 0, yq - i * 0.55, 0.28, "CARTOUCHE")
    doc.saveas(chemin)
    return chemin
