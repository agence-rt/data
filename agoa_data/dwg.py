"""Écriture du fond de plan : DXF (ezdxf) puis conversion en DWG avec l'ODA File Converter (gratuit, installé sur le poste).

Le DWG est un format fermé : l'application écrit un DXF (R2018, mètres, Lambert 93) et le convertit si l'ODA File Converter est
trouvé ; sinon le DXF est livré tel quel (AutoCAD, BricsCAD, etc. l'enregistrent en DWG en un clic).
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

COULEURS = {"MURS": 7, "TRAITS": 8, "COTES": 4, "TEXTES_COTES": 6, "TEXTES": 3}      # couleurs AutoCAD (ACI)


def nom_calque(s):
    return re.sub(r"[^A-Z0-9_]+", "_", s.upper()).strip("_")[:40] or "PLAN"


def ecrire_dxf(chemin, plans, cadastre=None):
    """`plans` : liste de (nom, calques en Lambert 93, angle_deg) ; `cadastre` : dict {parcelle, voisines, batiments, numeros}.

    Calques créés : CADASTRE_PARCELLE_CIBLE, CADASTRE_PARCELLES, CADASTRE_NUMEROS, CADASTRE_BATIMENTS, puis <PLAN>_MURS,
    <PLAN>_TRAITS, <PLAN>_COTES, <PLAN>_TEXTES_COTES, <PLAN>_TEXTES pour chaque plan PDF.
    """
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6                                          # mètres
    msp = doc.modelspace()

    def calque(nom, couleur):
        if nom not in doc.layers:
            doc.layers.add(nom, color=couleur)
        return nom

    def polygone(g, lay):
        for ring in [g.exterior, *g.interiors]:
            msp.add_lwpolyline([(x, y) for x, y in ring.coords[:-1]], close=True, dxfattribs={"layer": lay})

    if cadastre:
        for b in cadastre.get("batiments", []):
            polygone(b, calque("CADASTRE_BATIMENTS", 8))
        for p in cadastre.get("voisines", []):
            polygone(p.geom, calque("CADASTRE_PARCELLES", 7))
        for p in cadastre.get("voisines", []):
            pt = p.geom.representative_point()
            msp.add_text(p.numero, height=1.0, dxfattribs={"layer": calque("CADASTRE_NUMEROS", 7), "insert": (pt.x, pt.y)})
        if cadastre.get("parcelle") is not None:
            polygone(cadastre["parcelle"].geom, calque("CADASTRE_PARCELLE_CIBLE", 1))
    for nom, calques, angle in plans:
        base = nom_calque(nom)
        for typ, items in calques.items():
            if not items:
                continue
            lay = calque(f"{base}_{typ}", COULEURS.get(typ, 7))
            if typ.startswith("TEXTES"):
                for (x, y), txt, h in items:
                    msp.add_text(txt, height=h, rotation=angle, dxfattribs={"layer": lay, "insert": (x, y)})
            else:
                for it in items:
                    pts, ferme = it[0], it[1]
                    largeur = it[2] if len(it) > 2 else 0.0
                    if len(pts) == 2 and not largeur:
                        msp.add_line(pts[0], pts[1], dxfattribs={"layer": lay})
                    elif len(pts) >= 2:
                        msp.add_lwpolyline(pts, close=bool(ferme), dxfattribs={"layer": lay, "const_width": round(largeur, 4)})
    doc.saveas(chemin)
    return chemin


def trouver_oda():
    """Chemin de ODAFileConverter si installé (variable AGOA_ODA, PATH, dossiers habituels de Windows)."""
    for c in (os.environ.get("AGOA_ODA"), shutil.which("ODAFileConverter")):
        if c and os.path.isfile(c):
            return c
    if sys.platform.startswith("win"):
        for racine in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files"):
            if racine:
                for c in sorted(glob.glob(os.path.join(racine, "ODA", "ODAFileConverter*", "ODAFileConverter.exe")), reverse=True):
                    return c
    return None


def commande_oda(exe, dossier_in, dossier_out, nom_dxf):
    """Ligne de commande de l'ODA File Converter : DXF → DWG AutoCAD 2018, sans récursion, avec audit."""
    return [exe, dossier_in, dossier_out, "ACAD2018", "DWG", "0", "1", nom_dxf]


def convertir_dwg(chemin_dxf, exe=None, timeout=180):
    """DXF → DWG à côté du DXF. Renvoie le chemin du DWG, ou None (convertisseur absent ou échec)."""
    exe = exe or trouver_oda()
    if not exe:
        return None
    dossier = os.path.dirname(os.path.abspath(chemin_dxf))
    nom = os.path.basename(chemin_dxf)
    with tempfile.TemporaryDirectory() as tmp_in, tempfile.TemporaryDirectory() as tmp_out:
        shutil.copy(chemin_dxf, os.path.join(tmp_in, nom))      # le convertisseur traite un dossier entier : on l'isole
        try:
            subprocess.run(commande_oda(exe, tmp_in, tmp_out, nom), check=False, timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return None
        sortie = os.path.join(tmp_out, os.path.splitext(nom)[0] + ".dwg")
        if not os.path.isfile(sortie) or os.path.getsize(sortie) < 100:
            return None
        cible = os.path.join(dossier, os.path.basename(sortie))
        shutil.copy(sortie, cible)
        return cible


# --------------------------------------------------------------------------------------------
# Volumétrie : nuage de points et coupes en DXF
CLASSES_DXF = {2: ("SOL", 8), 6: ("BATI", 1), 3: ("VEGETATION", 3), 4: ("VEGETATION", 3), 5: ("VEGETATION", 3),
               1: ("NON_CLASSE", 7)}
NUAGE_MAX_POINTS = 600_000           # au-delà, les classes autres que « bâtiment » puis le bâti sont éclaircis (fichier DXF lourd)


def _eclaircir(pts, maximum):
    """Garde au plus `maximum` points : tous les points « bâtiment » d'abord, les autres classes éclaircies régulièrement."""
    if len(pts) <= maximum:
        return pts
    import numpy as np
    bati = pts[pts[:, 3] == 6]
    autres = pts[pts[:, 3] != 6]
    place = max(maximum - len(bati), maximum // 4)
    if len(autres) > place:
        autres = autres[np.linspace(0, len(autres) - 1, place).astype(int)]
    sortie = np.vstack([bati, autres])
    if len(sortie) > maximum:
        sortie = sortie[np.linspace(0, len(sortie) - 1, maximum).astype(int)]
    return sortie


def ecrire_nuage_dxf(pts, chemin, emprise=None, coupes=None, maximum=NUAGE_MAX_POINTS):
    """Nuage de points (x, y, z NGF, classe) → DXF en POINT 3D, un calque par famille de classes ; en plus, l'emprise du bâtiment
    et le tracé des coupes (calque COUPES_TRACES) dans le même repère Lambert 93.

    Renvoie (chemin, nombre de points écrits).
    """
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    pts = _eclaircir(pts, maximum)
    familles = {}
    for cl in sorted(set(int(c) for c in pts[:, 3])):
        nom, couleur = CLASSES_DXF.get(cl, ("AUTRES", 5))
        familles.setdefault(nom, (couleur, []))[1].append(cl)
    for nom, (couleur, classes) in familles.items():
        lay = f"NUAGE_{nom}"
        doc.layers.add(lay, color=couleur)
        sel = pts[[int(c) in classes for c in pts[:, 3]]]
        for x, y, z in sel[:, :3]:
            msp.add_point((float(x), float(y), float(z)), dxfattribs={"layer": lay})
    if emprise is not None:
        doc.layers.add("EMPRISE_BATIMENT", color=2)
        for g in (emprise.geoms if emprise.geom_type == "MultiPolygon" else [emprise]):
            msp.add_lwpolyline([(x, y) for x, y in g.exterior.coords[:-1]], close=True, dxfattribs={"layer": "EMPRISE_BATIMENT"})
    if coupes:
        doc.layers.add("COUPES_TRACES", color=4)
        for i, c in enumerate(coupes, 1):
            xy = list(c.trace.coords)
            msp.add_lwpolyline(xy, dxfattribs={"layer": "COUPES_TRACES"})
            msp.add_text(f"Coupe {i} - {c.nom}", height=0.8, dxfattribs={"layer": "COUPES_TRACES", "insert": xy[-1]})
    doc.saveas(chemin)
    return chemin, int(len(pts))


def ecrire_coupes_dxf(donnees, chemin, ecart=15.0):
    """Coupes en élévation, côte à côte : x = distance le long de la coupe (m), y = altitude NGF (m).

    `donnees` : liste de (Coupe analysée, q, z, classes) telle que renvoyée par coupes.analyser_coupe.
    Par coupe, calques C<n>_<TYPE>_<VOIE>_ NUAGE (points de la tranche), PROFIL_TOIT, REPERES (sol, égout, faîtage, nus de façade)
    et TEXTES (titre et valeurs chiffrées).
    """
    import ezdxf
    import numpy as np
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    x_off = 0.0
    for i, (c, q, z, cl) in enumerate(donnees, 1):
        if c.r.get("batiment_traverse") and "q_in" in c.r:            # on garde le bâtiment et 15 m de part et d'autre
            garde = (q > c.r["q_in"] - 15) & (q < c.r["q_out"] + 15)
            q, z, cl = q[garde], z[garde], cl[garde]
        if len(q) == 0:
            continue
        sens = "PERP" if c.type == "perpendiculaire" else "PARA"
        base = f"C{i}_{sens}_{nom_calque(c.nom)}"
        lays = {k: f"{base}_{k}" for k in ("NUAGE", "PROFIL_TOIT", "REPERES", "TEXTES")}
        for k, couleur in (("NUAGE", 8), ("PROFIL_TOIT", 1), ("REPERES", 4), ("TEXTES", 7)):
            doc.layers.add(lays[k], color=couleur)
        qmin, qmax = float(q.min()), float(q.max())
        ox = x_off - qmin                                          # abscisse 0 de la coupe → x_off
        for qq, zz in zip(q, z):
            msp.add_point((float(qq) + ox, float(zz), 0.0), dxfattribs={"layer": lays["NUAGE"]})
        r = c.r
        if r.get("batiment_traverse") and "sol" in r:
            a, b = r["q_in"] + ox, r["q_out"] + ox
            toit = (cl == 6) & (q > r["q_in"] + 0.3) & (q < r["q_out"] - 0.3) & (z > r["sol"] + 2.5)
            if toit.sum() > 5:                                     # profil du toit : médianes par pas de 0,25 m
                bins = np.floor((q[toit] - r["q_in"]) / 0.25).astype(int)
                prof = [(float(np.median(q[toit][bins == k])) + ox, float(np.median(z[toit][bins == k])))
                        for k in np.unique(bins) if (bins == k).sum() >= 2]
                if len(prof) >= 2:
                    msp.add_lwpolyline(prof, dxfattribs={"layer": lays["PROFIL_TOIT"]})
            msp.add_line((x_off, r["sol"]), (x_off + qmax - qmin, r["sol"]), dxfattribs={"layer": lays["REPERES"]})
            msp.add_text(f"Sol {r['sol']:.2f} NGF", height=0.35, dxfattribs={"layer": lays["TEXTES"], "insert": (x_off, r["sol"] - 0.6)})
            haut = max([r.get("faitage", r["sol"]), r.get("egout", r["sol"])])
            for xf in (a, b):
                msp.add_line((xf, r["sol"]), (xf, haut), dxfattribs={"layer": lays["REPERES"]})
            for cle, lib in (("egout", "Egout"), ("faitage", "Faitage")):
                if cle in r:
                    msp.add_line((a, r[cle]), (b, r[cle]), dxfattribs={"layer": lays["REPERES"], "linetype": "DASHED"})
                    msp.add_text(f"{lib} {r[cle + '_h']:.2f} m ({r[cle]:.2f} NGF)", height=0.4,
                                 dxfattribs={"layer": lays["TEXTES"], "insert": (b + 0.4, r[cle])})
            if "pente_deg" in r:
                msp.add_text(f"Pente {r['pente_deg']:.0f} deg", height=0.35,
                             dxfattribs={"layer": lays["TEXTES"], "insert": (a, haut + 1.0)})
        titre = f"Coupe {i} {'perpendiculaire' if sens == 'PERP' else 'parallele'} - {c.nom}"
        msp.add_text(titre, height=0.6, dxfattribs={"layer": lays["TEXTES"], "insert": (x_off, float(z.max()) + 2.5)})
        x_off += (qmax - qmin) + ecart + 12.0                      # marge pour les textes à droite des repères
    doc.saveas(chemin)
    return chemin


def ecrire_coupes_simplifiees_dxf(items, chemin, ecart=12.0):
    """Coupes simplifiées (contour du bâtiment, lignes réduites aux sommets utiles), côte à côte.

    `items` : liste de (coupe, profil, r) avec `profil` = points (distance, hauteur au-dessus du sol) de coupes.profil.
    x = distance le long de la coupe (m), y = altitude NGF (m). Calques C<n>_<TYPE>_<VOIE>_ PROFIL (polyligne du bâtiment),
    SOL, FAÇADE (nu de façade), COTES et TEXTES (hauteurs des redans, sol, égout/faîtage)."""
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    x_off = 0.0
    for i, (c, pr, r) in enumerate(items, 1):
        if not pr:
            continue
        sol = r["sol"]
        sens = "PERP" if c.type == "perpendiculaire" else "PARA"
        base = f"C{i}_{sens}_{nom_calque(c.nom)}"
        lays = {k: f"{base}_{k}" for k in ("PROFIL", "SOL", "FACADE", "COTES", "TEXTES")}
        for k, couleur in (("PROFIL", 1), ("SOL", 3), ("FACADE", 5), ("COTES", 4), ("TEXTES", 7)):
            doc.layers.add(lays[k], color=couleur)
        q0 = min(p[0] for p in pr)
        q1 = max(p[0] for p in pr)
        ox = x_off - q0
        pts = [(p[0] + ox, p[1] + sol) for p in pr]
        msp.add_lwpolyline(pts, dxfattribs={"layer": lays["PROFIL"]})
        msp.add_line((x_off - 4, sol), (x_off + (q1 - q0) + 4, sol), dxfattribs={"layer": lays["SOL"]})
        msp.add_text(f"Sol {sol:.2f} NGF", height=0.35, dxfattribs={"layer": lays["TEXTES"], "insert": (x_off - 4, sol - 0.7)})
        msp.add_line((pts[0][0], sol), pts[1][:2], dxfattribs={"layer": lays["FACADE"]})
        vus = set()
        for (qq, hh), (x, y) in zip(pr, pts):                       # cote chaque palier (sommet de redan) une seule fois
            clef = round(hh, 1)
            if hh > 1.0 and clef not in vus:
                vus.add(clef)
                msp.add_line((x, sol), (x, y), dxfattribs={"layer": lays["COTES"], "linetype": "DASHED"})
                msp.add_text(f"{hh:.2f} m", height=0.3, dxfattribs={"layer": lays["TEXTES"], "insert": (x + 0.15, y + 0.2)})
        msp.add_text(f"Coupe {c.type} — {c.nom} (contour simplifié)", height=0.5,
                     dxfattribs={"layer": lays["TEXTES"], "insert": (x_off - 4, sol + max(p[1] for p in pr) + 2.0)})
        x_off += (q1 - q0) + ecart + 8
    doc.saveas(chemin)
    return chemin
