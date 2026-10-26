"""Photos de rue ouvertes Panoramax (https://panoramax.fr, licence ouverte / CC-BY-SA selon les auteurs).

API STAC : GET <instance>/search?place_position=<lon>,<lat>&place_distance=<min>-<max>&limit=<n>
Chaque image porte sa position, son azimut (`view:azimuth`), parfois son champ de vision (`pers:interior_orientation`) et des
liens `assets.hd` / `assets.sd`. Les images 360° (équirectangulaires) et plates sont gérées.
"""
import math
import urllib.request
from dataclasses import dataclass

import numpy as np

from . import geo

INSTANCES = ("https://api.panoramax.xyz/api", "https://panoramax.ign.fr/api")


@dataclass
class Photo:
    id: str
    x: float                     # Lambert 93
    y: float
    azimut: float | None         # degrés, depuis le nord, sens horaire (direction du centre de l'image)
    equirect: bool
    url: str
    date: str = ""
    producteur: str = ""
    fov: float | None = None     # champ de vision horizontal (°) si connu
    instance: str = ""


def _photo(f, instance):
    p = f.get("properties") or {}
    a = f.get("assets") or {}
    lien = (a.get("hd") or a.get("sd") or {}).get("href")
    if not lien or not f.get("geometry"):
        return None
    lon, lat = f["geometry"]["coordinates"][:2]
    x, y = geo._TO_L93(lon, lat)
    io = p.get("pers:interior_orientation") or {}
    fov = io.get("field_of_view")
    equi = p.get("type") == "equirectangular" or (fov is not None and float(fov) >= 359) or io.get("camera_type") == "equirectangular"
    az = p.get("view:azimuth")
    return Photo(f.get("id", ""), x, y, float(az) if az is not None else None, bool(equi), lien,
                 (p.get("datetimetz") or p.get("datetime") or "")[:10], p.get("geovisio:producer") or "",
                 float(fov) if fov is not None and not equi else None, instance)


def chercher(x, y, distance=(4, 45), limite=80, instances=INSTANCES):
    """Images Panoramax voyant le point (x, y) Lambert 93, à `distance` m (min, max). Première instance qui répond."""
    lon, lat = geo._TO_WGS(x, y)
    derniere = None
    for inst in instances:
        try:
            d = geo.get_json(f"{inst}/search", {"place_position": f"{lon:.6f},{lat:.6f}",
                                                 "place_distance": f"{distance[0]}-{distance[1]}", "limit": limite}, timeout=40, retries=2)
        except geo.DataError as e:
            derniere = e
            continue
        out = [q for q in (_photo(f, inst) for f in d.get("features", [])) if q]
        if out:
            return out
    if derniere:
        raise geo.DataError(f"Panoramax injoignable : {derniere}")
    return []


def _cap(a, b):
    """Azimut (°, depuis le nord) du point a vers le point b."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def _ecart(a, b):
    return abs((a - b + 180) % 360 - 180)


def selectionner(photos, facade, n=4):
    """Meilleures images pour une façade : en face (rue côté extérieur), à 6-30 m, orientées vers la façade (images plates),
    récentes ; les 360° sont acceptées de face comme de biais léger."""
    cx = facade.origine[0] + facade.droite[0] * facade.longueur / 2
    cy = facade.origine[1] + facade.droite[1] * facade.longueur / 2
    cote, notes = [], []
    for p in photos:
        v = (p.x - cx, p.y - cy)
        d = math.hypot(*v)
        if d < 3 or d > 45:
            continue
        if v[0] * facade.sortant[0] + v[1] * facade.sortant[1] <= 0:          # du mauvais côté (dans l'immeuble)
            continue
        inc = _ecart(_cap((p.x, p.y), (cx, cy)), _cap((0, 0), (-facade.sortant[0], -facade.sortant[1])))
        if inc > 40:                                                          # trop oblique
            continue
        if not p.equirect and p.azimut is not None and _ecart(p.azimut, _cap((p.x, p.y), (cx, cy))) > 35:
            continue                                                         # image plate qui ne regarde pas la façade
        note = math.cos(math.radians(inc)) * 2 - abs(d - 14) / 25 + (0.4 if p.equirect else 0.0)
        if p.date:
            note += 0.1 * (int(p.date[:4] or 2015) - 2015) / 10
        notes.append(note)
        cote.append(p)
    ordre = np.argsort(notes)[::-1] if notes else []
    out, vus = [], []
    for i in ordre:                                                           # évite deux prises quasi identiques
        p = cote[i]
        if all(math.hypot(p.x - q.x, p.y - q.y) > 2.0 for q in vus):
            out.append(p)
            vus.append(p)
        if len(out) >= n:
            break
    return out


def telecharger(photo, timeout=60):
    """Image (BGR, numpy) d'une photo Panoramax."""
    import cv2
    req = urllib.request.Request(photo.url, headers={"User-Agent": geo.UA})
    with urllib.request.urlopen(req, timeout=timeout, context=geo._CTX) as r:
        data = r.read()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise geo.DataError(f"Image Panoramax illisible : {photo.id}")
    if abs(img.shape[1] / img.shape[0] - 2.0) < 0.05:
        photo.equirect = True
    return img
