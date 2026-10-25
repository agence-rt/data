"""Interrogation des API publiques : adresse, cadastre, BD TOPO (bâtiments, voies), index des dalles LiDAR HD.

Toutes les géométries sont renvoyées en Lambert 93 (EPSG:2154), en mètres.
"""
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import linemerge, transform, unary_union

GPF = "https://data.geopf.fr"      # un seul serveur : adresse, cadastre, BD TOPO, LiDAR HD
UA = "AGOA-DATA/1.0"

_TO_L93 = Transformer.from_crs(4326, 2154, always_xy=True).transform
_TO_WGS = Transformer.from_crs(2154, 4326, always_xy=True).transform
_CTX = ssl.create_default_context()


class DataError(Exception):
    """Erreur lisible par l'utilisateur (adresse introuvable, service indisponible…)."""


def _reproj(fn, geom):
    import numpy as np
    import shapely
    g = shapely.force_2d(geom)
    return shapely.transform(g, lambda a: np.column_stack(fn(a[:, 0], a[:, 1])))


def to_l93(geom):
    return _reproj(_TO_L93, geom)


def to_wgs(geom):
    return _reproj(_TO_WGS, geom)


def get_json(url, params=None, timeout=45, retries=3):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=":,()")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1))
                continue
            raise DataError(f"Service indisponible ({e.code}) : {url.split('?')[0]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise DataError(f"Impossible de joindre {url.split('?')[0]} : {last}")


# --------------------------------------------------------------------------------------------
@dataclass
class Adresse:
    label: str
    lon: float
    lat: float
    x: float
    y: float
    citycode: str
    score: float


def _adresse_de(f, defaut=""):
    lon, lat = f["geometry"]["coordinates"]
    x, y = _TO_L93(lon, lat)
    p = f["properties"]
    return Adresse(p.get("label", defaut), lon, lat, x, y, p.get("citycode", ""), float(p.get("score", 0)))


def suggerer(texte, limite=7):
    """Suggestions d'adresses pendant la frappe (API de géocodage, mode autocomplétion).

    Recherche d'abord dans Paris (99 % des adresses) sauf si une autre ville ou un autre code postal est saisi.
    """
    texte = (texte or "").strip()
    if len(texte) < 3:
        return []
    hors_paris = bool(re.search(r"\b(?!75)\d{5}\b", texte))
    params = {"q": texte, "limit": limite, "autocomplete": 1}
    if not hors_paris:
        params["citycode"] = "75056"
    feats = get_json(f"{GPF}/geocodage/search/", params, retries=2).get("features") or []
    if not feats and not hors_paris:                       # rien à Paris : France entière
        params.pop("citycode")
        feats = get_json(f"{GPF}/geocodage/search/", params, retries=2).get("features") or []
    return [_adresse_de(f, texte) for f in feats]


def geocoder(texte):
    """Adresse libre → point (API de géocodage de la Géoplateforme, base adresse nationale)."""
    if isinstance(texte, Adresse):                         # adresse déjà choisie dans les suggestions
        return texte
    texte = (texte or "").strip()
    if not texte:
        raise DataError("Saisissez une adresse.")
    if not re.search(r"\b(paris|750\d\d)\b", texte, re.I) and not re.search(r"\b\d{5}\b", texte):
        texte += " Paris"      # 99 % des adresses sont à Paris
    d = get_json(f"{GPF}/geocodage/search/", {"q": texte, "limit": 1, "type": "housenumber"})
    feats = d.get("features") or []
    if not feats:
        d = get_json(f"{GPF}/geocodage/search/", {"q": texte, "limit": 1})
        feats = d.get("features") or []
    if not feats:
        raise DataError(f"Adresse introuvable : {texte}")
    return _adresse_de(feats[0], texte)


# --------------------------------------------------------------------------------------------
@dataclass
class Parcelle:
    idu: str
    section: str
    numero: str
    contenance: float
    geom: Polygon                      # Lambert 93


def parcelle_au_point(adr):
    """Parcelle cadastrale contenant le point de l'adresse (Parcellaire Express, WFS Géoplateforme)."""
    e = 0.00001   # ≈ 1 m
    d = get_json(f"{GPF}/wfs/ows", {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle", "OUTPUTFORMAT": "application/json", "COUNT": 10,
        "BBOX": f"{adr.lat - e},{adr.lon - e},{adr.lat + e},{adr.lon + e},urn:ogc:def:crs:EPSG::4326"})
    feats = d.get("features") or []
    if not feats:
        raise DataError("Aucune parcelle cadastrale trouvée à cette adresse.")
    pt = Point(adr.x, adr.y)
    cand = []
    for f in feats:
        geom = to_l93(shape(f["geometry"]))
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda a: a.area)
        cand.append((0 if geom.contains(pt) else geom.distance(pt), f["properties"], geom))
    _, p, geom = min(cand, key=lambda c: c[0])
    return Parcelle(p.get("idu", ""), p.get("section", ""), p.get("numero", ""), float(p.get("contenance") or 0), geom)


# --------------------------------------------------------------------------------------------
@dataclass
class Batiment:
    id: str
    geom: Polygon                      # Lambert 93, sans trous inutiles
    hauteur: float | None
    etages: int | None
    usage: str
    annee: str
    alt_toit_min: float | None
    part_sur_parcelle: float = 0.0     # 0..1


def _wfs_bbox(layer, geom_l93, marge=60, count=200):
    x0, y0, x1, y1 = geom_l93.buffer(marge).bounds
    lon0, lat0 = _TO_WGS(x0, y0)
    lon1, lat1 = _TO_WGS(x1, y1)
    return get_json(f"{GPF}/wfs/ows", {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": layer,
        "OUTPUTFORMAT": "application/json", "COUNT": count,
        "BBOX": f"{lat0},{lon0},{lat1},{lon1},urn:ogc:def:crs:EPSG::4326"})


def _drop_z(geom):
    from shapely import force_2d
    return force_2d(geom)


def batiments_de_la_parcelle(parcelle, seuil=0.25):
    """Bâtiments BD TOPO dont au moins `seuil` de l'emprise est sur la parcelle."""
    d = _wfs_bbox("BDTOPO_V3:batiment", parcelle.geom, marge=30)
    out = []
    for f in d.get("features", []):
        g = to_l93(_drop_z(shape(f["geometry"])))
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda a: a.area)
        if g.area < 1:
            continue
        part = g.intersection(parcelle.geom).area / g.area
        if part < seuil:
            continue
        p = f["properties"]
        out.append(Batiment(
            id=p.get("cleabs") or f.get("id", ""), geom=g,
            hauteur=p.get("hauteur"), etages=p.get("nombre_d_etages"),
            usage=p.get("usage_1") or "", annee=(p.get("date_d_apparition") or "")[:4],
            alt_toit_min=p.get("altitude_minimale_toit"), part_sur_parcelle=part))
    out.sort(key=lambda b: -b.part_sur_parcelle * b.geom.area)
    return out


@dataclass
class Voie:
    nom: str
    geom: LineString                   # Lambert 93
    distance: float                    # distance au bâtiment (m)


def voies_du_batiment(batiment, rayon=25.0):
    """Voies nommées (BD TOPO) situées à moins de `rayon` m du bâtiment, regroupées par nom."""
    d = _wfs_bbox("BDTOPO_V3:troncon_de_route", batiment.geom, marge=rayon + 40)
    par_nom = {}
    for f in d.get("features", []):
        p = f["properties"]
        nom = p.get("nom_voie_ban_gauche") or p.get("nom_voie_ban_droite")
        if not nom:
            continue
        par_nom.setdefault(nom, []).append(to_l93(shape(f["geometry"])))
    out = []
    for nom, lignes in par_nom.items():
        g = unary_union(lignes)
        if g.geom_type == "MultiLineString":
            m = linemerge(g)
            g = m
        dist = g.distance(batiment.geom)
        if dist <= rayon:
            out.append(Voie(nom, g, dist))
    out.sort(key=lambda v: v.distance)
    return out


# --------------------------------------------------------------------------------------------
@dataclass
class Dalle:
    nom: str
    url_nuage: str
    date_edition: str
    nb_points: int


def dalles_lidar(geom_l93, marge=45.0):
    """Dalles LiDAR HD (nuage classifié COPC) couvrant l'emprise + marge."""
    d = _wfs_bbox("IGNF_LIDAR-HD_METADONNEE:metadata", geom_l93, marge=marge, count=20)
    out = []
    for f in d.get("features", []):
        p = f["properties"]
        url = p.get("url_npl")
        if not url:
            continue
        out.append(Dalle(url.rsplit("/", 1)[-1], url, (p.get("date_edition") or "")[:10], int(p.get("nombre_points") or 0)))
    if not out:
        raise DataError("Pas de dalle LiDAR HD publiée pour ce secteur.")
    return sorted({d.nom: d for d in out}.values(), key=lambda a: a.nom)


# --------------------------------------------------------------------------------------------
# Données de contexte pour le plan cadastral
def _polygones(geom):
    return list(geom.geoms) if geom.geom_type in ("MultiPolygon", "GeometryCollection") else [geom]


def parcelles_dans(emprise):
    """Toutes les parcelles du Parcellaire Express touchant l'emprise (Polygon Lambert 93) → liste de Parcelle."""
    d = _wfs_bbox("CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle", emprise, marge=0, count=1000)
    out = []
    for f in d.get("features", []):
        p = f["properties"]
        g = to_l93(shape(f["geometry"]))
        for part in _polygones(g):
            if part.geom_type == "Polygon" and not part.is_empty:
                out.append(Parcelle(p.get("idu", ""), p.get("section", ""), str(p.get("numero", "")).lstrip("0") or "0",
                                    float(p.get("contenance") or 0), part))
    return out


def batiments_dans(emprise):
    """Emprises des bâtiments BD TOPO touchant l'emprise → liste de Polygon Lambert 93."""
    d = _wfs_bbox("BDTOPO_V3:batiment", emprise, marge=0, count=1000)
    out = []
    for f in d.get("features", []):
        g = to_l93(_drop_z(shape(f["geometry"])))
        out += [a for a in _polygones(g) if a.geom_type == "Polygon" and a.area > 1]
    return out


def voies_dans(emprise):
    """Voies nommées (BD TOPO) touchant l'emprise, regroupées par nom → liste de (nom, géométrie Lambert 93)."""
    d = _wfs_bbox("BDTOPO_V3:troncon_de_route", emprise, marge=0, count=1000)
    par_nom = {}
    for f in d.get("features", []):
        p = f["properties"]
        nom = p.get("nom_voie_ban_gauche") or p.get("nom_voie_ban_droite")
        if nom:
            par_nom.setdefault(nom, []).append(to_l93(shape(f["geometry"])))
    return [(n, unary_union(l)) for n, l in par_nom.items()]
