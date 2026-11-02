"""Volume du bâtiment en maillage 3D à surfaces (OBJ et DXF 3DFACE) : toiture lue dans le nuage, façades verticales, sol.

Le toit est le modèle d'altitudes de la volumétrie (maille 0,5 m) triangulé dans l'emprise BD TOPO ; les façades sont des quadrilatères
verticaux le long du contour de l'emprise (échantillonné tous les 0,5 m), du sol au toit ; le dessous est l'emprise au sol.
Coordonnées : x, y Lambert 93 décalés de l'origine indiquée dans l'en-tête, z = altitude NGF. Z vers le haut."""
import numpy as np
import shapely
from shapely import contains_xy
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient


def _anneaux(poly, pas):
    p = orient(shapely.segmentize(poly, pas), 1.0)       # extérieur anti-horaire, trous horaires
    return [np.array(p.exterior.coords)[:-1]] + [np.array(r.coords)[:-1] for r in p.interiors], p


def maillage(emprise, vol, pas_bord=0.5, cellule=1.5):
    """Maillage fermé d'un bâtiment : dict(sommets (n,3), toit, facades, sol : triangles / quadrilatères d'indices, altitude_sol).

    L'emprise est découpée en cellules de `cellule` m ; chaque morceau, dont le contour est échantillonné tous les `pas_bord` m, est
    triangulé (Delaunay contraint). Les morceaux voisins partageant exactement les mêmes sommets, la surface est sans fente : le
    toit prend l'altitude du modèle de toiture en chaque sommet, le sol celle du terrain, et les façades sont les arêtes de bord du toit."""
    from scipy import ndimage as ndi
    from shapely import constrained_delaunay_triangles
    from shapely.geometry import box
    polys = list(emprise.geoms) if emprise.geom_type == "MultiPolygon" else [emprise]
    emprise = max(polys, key=lambda p: p.area)
    Z, (x0, y1), pas = vol.modele, vol.origine, vol.pas
    ny, nx = Z.shape
    ok = ~np.isnan(Z)
    if not ok.any():
        raise ValueError("Modèle de toiture vide.")
    idx = ndi.distance_transform_edt(~ok, return_distances=False, return_indices=True)
    Zf = Z[idx[0], idx[1]]                                   # trous comblés par la maille mesurée la plus proche

    def z_toit(x, y):
        c = int(np.clip((x - x0) / pas, 0, nx - 1))
        r = int(np.clip((y1 - y) / pas, 0, ny - 1))
        return float(Zf[r, c])

    sol = float(vol.altitude_sol)
    minx, miny, maxx, maxy = emprise.bounds
    ids, S = {}, []

    def sommet(x, y):
        k = (round(x, 5), round(y, 5))
        if k not in ids:
            ids[k] = len(S)
            S.append((x, y, z_toit(x, y)))
        return ids[k]

    tris = []
    for gx in np.arange(minx, maxx, cellule):
        for gy in np.arange(miny, maxy, cellule):
            morceau = emprise.intersection(box(gx, gy, gx + cellule, gy + cellule))
            for q in ([morceau] if morceau.geom_type == "Polygon" else list(getattr(morceau, "geoms", []))):
                if q.geom_type != "Polygon" or q.area < 1e-4:
                    continue
                for t in constrained_delaunay_triangles(shapely.segmentize(q, pas_bord)).geoms:
                    c = list(t.exterior.coords)[:3]
                    i3 = [sommet(x, y) for x, y in c]
                    if len(set(i3)) < 3:
                        continue
                    (ax, ay), (bx, by), (cx, cy) = c
                    if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:          # normale vers le haut
                        i3 = i3[::-1]
                    tris.append(i3)
    # arêtes de bord du toit = contour (arêtes utilisées une seule fois) → façades
    from collections import Counter
    n_arete = Counter()
    for t in tris:
        for i, j in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            n_arete[(i, j)] += 1
    bord = [(i, j) for (i, j) in n_arete if (j, i) not in n_arete]
    n_haut = len(S)
    S = S + [(x, y, sol) for x, y, _ in S]                   # sommets du sol : mêmes (x, y)
    facades = [(i + n_haut, j + n_haut, j, i) for i, j in bord]
    sol_tri = [[a + n_haut, c + n_haut, b + n_haut] for a, b, c in tris]    # sens inverse : normale vers le bas
    return {"sommets": np.array(S), "toit": tris, "facades": facades, "sol": sol_tri, "altitude_sol": sol}


def _fermeture(m):
    """Nombre d'arêtes appartenant à un seul triangle (0 = surface fermée)."""
    from collections import Counter
    c = Counter()
    faces = list(m["toit"]) + list(m["sol"])
    for f in m["facades"]:
        faces += [(f[0], f[1], f[2]), (f[0], f[2], f[3])]
    for f in faces:
        for a, b in zip(f, list(f[1:]) + [f[0]]):
            c[(min(a, b), max(a, b))] += 1
    return sum(1 for v in c.values() if v == 1)


def ecrire_obj(items, chemin, origine=None):
    """`items` : liste de (nom, maillage). Un objet OBJ par bâtiment, trois groupes (toiture / facades / sol)."""
    allv = np.vstack([m["sommets"] for _, m in items])
    ox, oy = origine or (round(float(allv[:, 0].mean()), 1), round(float(allv[:, 1].mean()), 1))
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("# AGOA DATA - volume du batiment (surfaces)\n# unites : metres ; Z vers le haut = altitude NGF\n")
        f.write(f"# origine Lambert 93 : X={ox} Y={oy}  (x_L93 = x + {ox}, y_L93 = y + {oy})\n")
        base = 1
        for nom, m in items:
            S = m["sommets"]
            f.write(f"o {nom}\n")
            for x, y, z in S:
                f.write(f"v {x - ox:.3f} {y - oy:.3f} {z:.3f}\n")
            f.write("g toiture\n")
            for t in m["toit"]:
                f.write("f " + " ".join(str(i + base) for i in t) + "\n")
            f.write("g facades\n")
            for q in m["facades"]:
                f.write("f " + " ".join(str(i + base) for i in q) + "\n")
            f.write("g sol\n")
            for t in m["sol"]:
                f.write("f " + " ".join(str(i + base) for i in t) + "\n")
            base += len(S)
    return chemin, (ox, oy)


def ecrire_dxf(items, chemin):
    """Même maillage en 3DFACE (Lambert 93, altitudes NGF), calques VOLUME_TOIT / VOLUME_FACADES / VOLUME_SOL."""
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    for nom, c in (("VOLUME_TOIT", 1), ("VOLUME_FACADES", 5), ("VOLUME_SOL", 3)):
        doc.layers.add(nom, color=c)
    for _, m in items:
        S = m["sommets"]
        for t in m["toit"]:
            a, b, c = (tuple(map(float, S[i])) for i in t)
            msp.add_3dface([a, b, c, c], dxfattribs={"layer": "VOLUME_TOIT"})
        for q in m["facades"]:
            msp.add_3dface([tuple(map(float, S[i])) for i in q], dxfattribs={"layer": "VOLUME_FACADES"})
        for t in m["sol"]:
            a, b, c = (tuple(map(float, S[i])) for i in t)
            msp.add_3dface([a, b, c, c], dxfattribs={"layer": "VOLUME_SOL"})
    doc.saveas(chemin)
    return chemin
