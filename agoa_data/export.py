"""Écriture des livrables : nuage .laz, modèle de toiture .asc, résultats .json et synthèse .txt."""
import json
import os

import numpy as np


def ecrire_laz(pts, chemin):
    import laspy
    from pyproj import CRS
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.scales = [0.001] * 3
    h.offsets = [pts[:, 0].min(), pts[:, 1].min(), pts[:, 2].min()]
    h.add_crs(CRS.from_epsg(2154))
    las = laspy.LasData(h)
    las.x, las.y, las.z = pts[:, 0], pts[:, 1], pts[:, 2]
    las.classification = pts[:, 3].astype(np.uint8)
    las.write(chemin)
    return chemin


def ecrire_asc(vol, chemin):
    """Grille ESRI ASCII (lisible par QGIS / CloudCompare) + fichier .prj Lambert 93."""
    from pyproj import CRS
    Z = vol.modele
    ny, nx = Z.shape
    x0, y1 = vol.origine
    with open(chemin, "w", encoding="ascii") as f:
        f.write(f"ncols {nx}\nnrows {ny}\nxllcorner {x0:.3f}\nyllcorner {y1 - ny * vol.pas:.3f}\n"
                f"cellsize {vol.pas}\nNODATA_value -9999\n")
        for row in Z:
            f.write(" ".join("-9999" if np.isnan(v) else f"{v:.2f}" for v in row) + "\n")
    with open(os.path.splitext(chemin)[0] + ".prj", "w", encoding="ascii") as f:
        f.write(CRS.from_epsg(2154).to_wkt(version="WKT1_ESRI"))
    return chemin


def ecrire_json(res, chemin):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=float)
    return chemin


def synthese_texte(res):
    a, p = res["adresse"], res["parcelle"]
    L = [f"AGOA VOL — volumétrie par nuage de points LiDAR HD", "",
         f"Adresse    : {a['label']}",
         f"Parcelle   : {p['section']} {p['numero']} (IDU {p['idu']}), {p['contenance']:.0f} m² au cadastre",
         f"Dalles IGN : {', '.join(res['dalles'])}",
         f"Calcul     : {res['date']}", ""]
    for i, b in enumerate(res["batiments"], 1):
        v = b["volumetrie"]
        L += [f"Bâtiment {i} — {b['usage'] or 'usage inconnu'}"
              f"{', ' + str(b['etages']) + ' étages' if b.get('etages') else ''}"
              f"{', ' + b['annee'] if b.get('annee') else ''}",
              f"  Emprise au sol              : {v['emprise_m2']:.0f} m²",
              f"  Altitude du sol             : {v['altitude_sol']:.1f} m NGF (de {v['sol_min']:.1f} à {v['sol_max']:.1f})",
              f"  Hauteur moyenne sol→toit    : {v['hauteur_moyenne']:.1f} m",
              f"  Hauteur max (98e centile)   : {v['hauteur_max']:.1f} m",
              f"  VOLUME sol→toit             : {v['volume_m3']:.0f} m³",
              f"  Comparaison BD TOPO         : {b['volume_bdtopo']:.0f} m³ (emprise × hauteur {b['hauteur_bdtopo']:.1f} m)"
              if b.get("hauteur_bdtopo") else "  Comparaison BD TOPO         : hauteur non renseignée", ""]
    if len(res["batiments"]) > 1:
        L += [f"TOTAL parcelle : {sum(b['volumetrie']['volume_m3'] for b in res['batiments']):.0f} m³", ""]
    L += ["Coupes (bâtiment principal)"]
    for c in res["coupes"]:
        r = c["resultats"]
        sens = "perpendiculaire à la " if c["type"] == "perpendiculaire" else "parallèle à la "
        if not r.get("batiment_traverse") or "faitage" not in r:
            L.append(f"  Coupe {sens}{c['voie']} : bâtiment non traversé ou toit non lisible")
            continue
        if "egout" not in r:      # coupe parallèle : l'égout n'a pas de sens, on donne la hauteur du toit
            L.append(f"  Coupe {sens}{c['voie']} : sol {r['sol']:.1f} NGF | point haut du toit {r['faitage_h']:.1f} m ({r['faitage']:.1f} NGF)")
            continue
        pente = f", pente indicative {r['pente_deg']:.0f}°" if "pente_deg" in r else ""
        L.append(f"  Coupe {sens}{c['voie']} : sol {r['sol']:.1f} NGF | égout {r['egout_h']:.1f} m ({r['egout']:.1f} NGF) | "
                 f"faîtage {r['faitage_h']:.1f} m ({r['faitage']:.1f} NGF){pente}")
    L += ["", "Limites : ordre de grandeur (≈10 points/m²). Le volume va du sol au toit (combles et niveau bas inclus, sous-sols exclus).",
          "Sol = médiane des points « sol » autour du bâtiment ; en terrain en pente, les hauteurs varient de ±0,5 m selon la façade.",
          "Sources : Géoplateforme IGN (adresse, Parcellaire Express, BD TOPO, LiDAR HD) — Licence Ouverte 2.0."]
    return "\n".join(L)
