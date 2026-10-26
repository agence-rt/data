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
