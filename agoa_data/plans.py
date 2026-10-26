"""Lecture d'un plan PDF (vectoriel ou scanné), mise à l'échelle et recalage sur le cadastre.

Un plan est transformé en « tracés » : polylignes en mètres réels (repère local du plan, y vers le haut), rangés par calque :
  MURS          traits épais et surfaces pleines (poché des murs)
  TRAITS        traits fins (cloisons, mobilier, menuiseries, hachures…)
  TEXTES_COTES  valeurs numériques (cotes), PDF vectoriels seulement
  TEXTES        autres textes, PDF vectoriels seulement
Un PDF scanné (image) est vectorisé par traitement d'image : les textes et les valeurs de cotes y restent des tracés.
"""
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

PT_MM = 25.4 / 72
ECHELLES_USUELLES = (20, 25, 50, 100, 200, 250, 500)
EPAISSEUR_MURS_MM = 0.30                 # sur le papier : au-delà, le trait est un mur
MAX_PX = 12000                           # taille maximale de l'image pour un plan scanné


class PlanError(Exception):
    """Plan illisible ou échelle introuvable (message destiné à l'utilisateur)."""


@dataclass
class Trace:
    pts: list                            # [(x, y)] en mm sur le papier, y vers le haut
    largeur_mm: float = 0.0
    ferme: bool = False
    rempli: bool = False
    role: str = ""                       # scans : "mur", "trait" ou "cote" (déterminé par la vectorisation)


@dataclass
class Texte:
    x: float                             # mm sur le papier
    y: float
    texte: str
    hauteur_mm: float


@dataclass
class PlanPDF:
    nom: str
    largeur_mm: float
    hauteur_mm: float
    traces: list = field(default_factory=list)
    textes: list = field(default_factory=list)
    scanne: bool = False
    echelle: int | None = None           # dénominateur lu dans le cartouche, sinon None


# ---------------------------------------------------------------------------------------- lecture
def echelle_dans_textes(mots):
    """Cherche « 1/50 », « 1:100 », « Échelle 1/50 » dans une liste de mots → dénominateur usuel ou None."""
    brut = " ".join(mots)
    trouves = [int(n) for n in re.findall(r"\b1\s*[/:]\s*(\d{2,3})\b", brut)]
    usuels = [n for n in trouves if n in ECHELLES_USUELLES]
    return max(set(usuels), key=usuels.count) if usuels else None


def lire_pdf(chemin, page=0):
    """PDF → PlanPDF (vectoriel si le fichier contient assez de dessin vectoriel, sinon scanné)."""
    import pdfplumber
    nom = os.path.splitext(os.path.basename(chemin))[0]
    try:
        with pdfplumber.open(chemin) as pdf:
            if not pdf.pages:
                raise PlanError(f"{nom} : PDF vide.")
            p = pdf.pages[min(page, len(pdf.pages) - 1)]
            W, H = float(p.width) * PT_MM, float(p.height) * PT_MM
            plan = PlanPDF(nom, W, H)
            _lire_vecteurs(p, plan)
            mots = p.extract_words(extra_attrs=["size", "upright"]) or []
            image_pleine = any((i["x1"] - i["x0"]) * (i["bottom"] - i["top"]) >= 0.5 * float(p.width) * float(p.height) for i in p.images)
    except PlanError:
        raise
    except Exception as e:                               # noqa: BLE001 — fichier corrompu, protégé…
        raise PlanError(f"{nom} : PDF illisible ({e}).") from e
    plan.echelle = echelle_dans_textes([m["text"] for m in mots])
    if len(plan.traces) >= 5 and not (image_pleine and len(plan.traces) < 200):   # vrai dessin vectoriel (pas une image scannée)
        for m in mots:
            if not m.get("upright", True):
                continue
            plan.textes.append(Texte(m["x0"] * PT_MM, H - m["bottom"] * PT_MM, m["text"], float(m["size"]) * PT_MM * 0.75))
        return plan
    return _lire_scan(chemin, page, plan, mots)


def _sombre(couleur):
    """Couleur de remplissage PDF (gris, RVB ou CMJN) → True si assez sombre pour être un poché de mur."""
    if couleur is None:
        return True
    c = couleur if isinstance(couleur, (list, tuple)) else (couleur,)
    try:
        if len(c) == 1:
            lum = float(c[0])
        elif len(c) == 3:
            lum = 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]
        elif len(c) == 4:
            lum = (1 - c[3]) * (1 - min(1.0, 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]))
        else:
            return True
    except (TypeError, ValueError):
        return True
    return lum < 0.5


def _lire_vecteurs(p, plan):
    H = float(p.height)
    def y(v):
        return (H - v) * PT_MM
    for o in p.lines:
        pts = [(a * PT_MM, y(b)) for a, b in o.get("pts", [])] or [(o["x0"] * PT_MM, y(o["top"])), (o["x1"] * PT_MM, y(o["bottom"]))]
        plan.traces.append(Trace(pts, float(o.get("linewidth") or 0) * PT_MM))
    surface_page = float(p.width) * float(p.height)

    def fond(o):
        """Surface remplie de couleur claire ou couvrant la page : fond / hachure pâle, pas un mur."""
        grand = (o["x1"] - o["x0"]) * (o["bottom"] - o["top"]) > 0.6 * surface_page
        return bool(o.get("fill")) and (grand or not _sombre(o.get("non_stroking_color")))

    for o in p.rects:
        if fond(o) and not o.get("stroke"):
            continue
        pts = [(o["x0"] * PT_MM, y(o["top"])), (o["x1"] * PT_MM, y(o["top"])), (o["x1"] * PT_MM, y(o["bottom"])), (o["x0"] * PT_MM, y(o["bottom"]))]
        plein = bool(o.get("fill")) and not o.get("stroke")
        plan.traces.append(Trace(pts, float(o.get("linewidth") or 0) * PT_MM, True, plein))
    for o in p.curves:
        if fond(o) and not o.get("stroke"):
            continue
        pts = [(a * PT_MM, y(b)) for a, b in o.get("pts", [])]
        if len(pts) >= 2:
            plan.traces.append(Trace(pts, float(o.get("linewidth") or 0) * PT_MM, bool(o.get("fill")) or pts[0] == pts[-1],
                                     bool(o.get("fill")) and not o.get("stroke")))


def _lire_scan(chemin, page, plan, mots):
    """Plan scanné : binarisation, nettoyage des bords de feuille, squelette des traits, vectorisation en polylignes.

    Chaque trait est réduit à son axe (squelette) et garde son épaisseur mesurée : les traits épais deviennent des MURS (polyligne
    avec largeur), les traits fins des TRAITS, les petits tracés (écriture à la main, flèches, tirets de cote) des COTES.
    """
    import cv2
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(chemin)
    pg = pdf[min(page, len(pdf) - 1)]
    w_pt, h_pt = pg.get_size()
    k = min(300 / 72, MAX_PX / max(w_pt, h_pt))          # 300 dpi, plafonné
    img = np.array(pg.render(scale=k).to_pil().convert("L"))
    pdf.close()
    if img.size == 0 or img.std() < 3:
        raise PlanError(f"{plan.nom} : page vide ou sans dessin lisible.")
    H, W = img.shape
    mm_px = 25.4 / (72 * k)                              # millimètres de papier par pixel
    seuil_binaire, _ = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    noir = (img < min(seuil_binaire + 20, 170)).astype(np.uint8)
    del img
    noir = _nettoyer_bords(noir)
    polylignes = _squelette_polylignes(noir, mm_px)
    del noir

    largeurs = [e for _, e, lg in polylignes if lg >= 8.0]            # épaisseurs des tracés longs (mm)
    seuil = _seuil_epaisseur(largeurs)
    bx, by = 0.035 * W, 0.035 * H                         # cadre de la feuille : bande de 3,5 % le long des bords
    for pts, epais, longueur in polylignes:
        if longueur < 0.8:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if max(xs) < bx or min(xs) > W - bx or max(ys) < by or min(ys) > H - by:
            continue
        if longueur < 4.0:
            role = "cote"                                  # tirets de cote, pointes de flèche, bouts d'écriture
        elif epais >= seuil:
            role = "mur" if longueur >= 12.0 else "cote"   # chiffres écrits en gras : épais mais courts
        else:
            role = "trait"
        plan.traces.append(Trace([(x * mm_px, (H - y) * mm_px) for x, y in pts], epais, False, False, role))
    plan.scanne = True
    if not plan.traces:
        raise PlanError(f"{plan.nom} : aucun trait exploitable dans ce PDF.")
    return plan


def _seuil_epaisseur(largeurs):
    """Épaisseur (mm) séparant traits fins et traits épais : coupure d'Otsu si les deux familles sont nettes, sinon 0,33 mm."""
    if len(largeurs) < 20:
        return 0.33
    v = np.sort(np.asarray(largeurs))
    meilleur, seuil = -1.0, 0.33
    for t in np.quantile(v, np.linspace(0.1, 0.9, 33)):
        bas, haut = v[v < t], v[v >= t]
        if len(bas) < 5 or len(haut) < 5:
            continue
        var = len(bas) * len(haut) * (haut.mean() - bas.mean()) ** 2
        if var > meilleur:
            meilleur, seuil, rapport = var, float(t), haut.mean() / max(bas.mean(), 1e-6)
    return min(max(seuil, 0.25), 0.8) if meilleur > 0 and rapport >= 1.6 else 0.33


def _nettoyer_bords(noir):
    """Supprime les poussières, les composantes qui touchent le bord de la feuille (cadre, bandes noires, déchirures)."""
    import cv2
    H, W = noir.shape
    n, lab, st, _ = cv2.connectedComponentsWithStats(noir, connectivity=8)
    bx, by = int(0.012 * W), int(0.012 * H)
    x, y, w, h, aire = st[:, 0], st[:, 1], st[:, 2], st[:, 3], st[:, 4]
    touche = (x <= bx) | (y <= by) | (x + w >= W - bx) | (y + h >= H - by)
    mauvais = touche | (aire < 6)
    mauvais[0] = False                                   # fond
    noir[mauvais[lab]] = 0
    return noir


def _squelette_polylignes(noir, mm_px):
    """Masque binaire → [(points pixels simplifiés, épaisseur mm, longueur mm)]."""
    import cv2
    from skimage.morphology import skeletonize
    dt = cv2.distanceTransform(noir, cv2.DIST_L2, 3)
    sk = skeletonize(noir.astype(bool))
    ys, xs = np.nonzero(sk)
    N = len(ys)
    if N == 0:
        return []
    W = noir.shape[1]
    cle = ys.astype(np.int64) * W + xs
    ordre = np.argsort(cle)
    cle_tri = cle[ordre]
    # voisins dans l'ordre cyclique autour du pixel
    decal = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
    adj = np.full((N, 8), -1, dtype=np.int32)
    for j, (dy, dx) in enumerate(decal):
        c = (ys + dy).astype(np.int64) * W + (xs + dx)
        pos = np.searchsorted(cle_tri, c)
        pos[pos >= N] = N - 1
        ok = cle_tri[pos] == c
        ok &= (xs + dx >= 0) & (xs + dx < W)
        adj[ok, j] = ordre[pos[ok]]
    pres = adj >= 0
    cn = ((~pres) & np.roll(pres, -1, axis=1)).sum(axis=1)       # nombre de branches autour du pixel
    cn = np.where(pres.all(axis=1), 0, cn)
    noeud = (cn != 2)
    demi = dt[ys, xs]                                             # demi-épaisseur en pixels
    voisins = [[int(v) for v in row if v >= 0] for row in adj]
    visite = bytearray(N)
    chemins = []

    def suivre(debut, premier):
        chemin, prev, cur = [debut, premier], debut, premier
        while not noeud[cur]:
            visite[cur] = 1
            vp = set(voisins[prev])
            suite = [v for v in voisins[cur] if v != prev and v not in vp]
            for v in voisins[cur]:                                # coins en escalier : pixels redondants
                if v != prev and v in vp and not noeud[v]:
                    visite[v] = 1
            if not suite:
                suite = [v for v in voisins[cur] if v != prev and not visite[v]]
            if not suite:
                break
            prev, cur = cur, suite[0]
            chemin.append(cur)
            if visite[cur]:
                break
        return chemin

    for n in np.nonzero(noeud)[0]:
        for m in voisins[n]:
            if noeud[m]:
                if n < m:
                    chemins.append([int(n), m])
            elif not visite[m]:
                chemins.append(suivre(int(n), m))
    for n in np.nonzero(~noeud)[0]:                               # boucles fermées (cercles de numérotation…)
        if not visite[n]:
            visite[n] = 1
            nb = voisins[n]
            if nb:
                c = suivre(int(n), nb[0])
                chemins.append(c)
    sortie = []
    for c in chemins:
        pts = np.stack([xs[c], ys[c]], axis=1).astype(np.float32)
        if len(pts) >= 3:
            pts = cv2.approxPolyDP(pts.reshape(-1, 1, 2), 1.6, False).reshape(-1, 2)
        longueur = float(np.sum(np.hypot(*np.diff(pts, axis=0).T))) * mm_px if len(pts) > 1 else 0.0
        epais = float(np.median(2 * demi[c])) * mm_px
        sortie.append(([(float(x), float(y)) for x, y in pts], epais, longueur))
    return sortie


# ---------------------------------------------------------------------------------------- mise à l'échelle
def classer(plan, echelle):
    """PlanPDF + échelle (1/x) → dict calque → listes, en mètres (repère local, y vers le haut).

    Calques de polylignes : MURS (pts, fermé, largeur m), TRAITS, COTES (petits tracés des scans : tirets de cote, chiffres écrits à
    la main) ; calques de textes (PDF vectoriels) : TEXTES_COTES et TEXTES.
    """
    m = echelle / 1000.0                                 # mm papier → m terrain
    largeurs = [t.largeur_mm for t in plan.traces if t.largeur_mm > 0 and not t.rempli and not t.role]
    seuil = max(EPAISSEUR_MURS_MM, 1.8 * float(np.percentile(largeurs, 20))) if largeurs else EPAISSEUR_MURS_MM
    calques = {"MURS": [], "TRAITS": [], "COTES": [], "TEXTES_COTES": [], "TEXTES": []}
    for t in plan.traces:
        pts = [(x * m, y * m) for x, y in t.pts]
        if t.role:                                       # plan scanné
            if t.role == "mur":
                calques["MURS"].append((pts, t.ferme, t.largeur_mm * m))
            else:
                calques["COTES" if t.role == "cote" else "TRAITS"].append((pts, t.ferme))
            continue
        mur = t.rempli or t.largeur_mm >= seuil
        calques["MURS" if mur else "TRAITS"].append((pts, t.ferme))
    for tx in plan.textes:
        cote = re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?(?:\s?(?:m|cm|mm|m²))?", tx.texte) is not None
        calques["TEXTES_COTES" if cote else "TEXTES"].append(((tx.x * m, tx.y * m), tx.texte, max(tx.hauteur_mm * m, 0.05)))
    return calques


# ---------------------------------------------------------------------------------------- recalage
@dataclass
class Recalage:
    angle_deg: float = 0.0
    echelle: float = 1.0                 # facteur appliqué aux dimensions du plan (1 = échelle lue conservée)
    tx: float = 0.0
    ty: float = 0.0
    centre_plan: tuple = (0.0, 0.0)
    iou: float = 0.0
    fiable: bool = False
    avertissements: list = field(default_factory=list)

    def point(self, x, y):
        c, s = math.cos(math.radians(self.angle_deg)), math.sin(math.radians(self.angle_deg))
        dx, dy = x - self.centre_plan[0], y - self.centre_plan[1]
        return self.echelle * (c * dx - s * dy) + self.tx, self.echelle * (s * dx + c * dy) + self.ty


def _axe_principal(poly):
    """Angle (degrés, modulo 180) du grand côté du rectangle englobant orienté."""
    r = poly.minimum_rotated_rectangle
    pts = list(r.exterior.coords)[:4]
    cotes = [(math.dist(pts[i], pts[(i + 1) % 4]), pts[i], pts[(i + 1) % 4]) for i in range(4)]
    _, a, b = max(cotes)
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180


def _transformee(coords, angle, s, centre, cible_c):
    c, sn = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [(s * (c * (x - centre[0]) - sn * (y - centre[1])) + cible_c[0],
             s * (sn * (x - centre[0]) + c * (y - centre[1])) + cible_c[1]) for x, y in coords]


def _points_principaux(lignes):
    """Points des grands tracés seulement (≥ 25 % de la plus grande étendue) : les lettres et les petits objets n'entrent pas
    dans l'emprise comparée au cadastre."""
    etendues = []
    for it in lignes:
        ligne = it[0]
        xs, ys = [p[0] for p in ligne], [p[1] for p in ligne]
        etendues.append(math.hypot(max(xs) - min(xs), max(ys) - min(ys)) if ligne else 0.0)
    if not etendues:
        return []
    seuil = 0.25 * max(etendues)
    return [p for it, e in zip(lignes, etendues) if e >= seuil for p in it[0]]


def _direction_dominante(segments):
    """Direction dominante (degrés, modulo 90) d'un ensemble de segments ((x0, y0), (x1, y1)), pondérée par la longueur ;
    renvoie (angle, concentration 0..1). Les bâtiments parisiens ont surtout des murs orthogonaux."""
    z, tot = 0j, 0.0
    for (x0, y0), (x1, y1) in segments:
        L = math.hypot(x1 - x0, y1 - y0)
        if L > 0:
            z += L * complex(math.cos(4 * math.atan2(y1 - y0, x1 - x0)), math.sin(4 * math.atan2(y1 - y0, x1 - x0)))
            tot += L
    if tot == 0:
        return 0.0, 0.0
    return (math.degrees(math.atan2(z.imag, z.real)) / 4) % 90, abs(z) / tot


def _segments_de(lignes):
    return [(l[i], l[i + 1]) for it in lignes for l in [it[0]] for i in range(len(l) - 1)]


def recaler(calques, cible):
    """Place le plan sur l'emprise cadastrale `cible` (Polygon Lambert 93) : rotation, légère mise à l'échelle, translation.

    L'enveloppe convexe des murs du plan est comparée à celle de l'emprise (bâtiment BD TOPO / parcelle) pour les quatre
    orientations possibles ; on retient la meilleure (rapport d'intersection / réunion). La dimension n'est corrigée que si le
    recouvrement est bon et si l'écart d'échelle reste faible (±6 %, la dérive d'une photocopie ou d'un scan).
    """
    pts = _points_principaux(calques["MURS"]) or _points_principaux(calques["TRAITS"])
    if len(pts) < 3:
        raise PlanError("Le plan ne contient aucun mur exploitable.")
    hull = MultiPoint(pts).convex_hull
    if hull.geom_type != "Polygon" or hull.area < 1:
        raise PlanError("Emprise du plan trop petite ou dégénérée (échelle erronée ?).")
    r = Recalage(centre_plan=(hull.centroid.x, hull.centroid.y))
    if cible is None or cible.is_empty:
        r.avertissements.append("Aucune adresse : plan laissé dans son repère local (non recalé sur le cadastre).")
        return r
    ch = cible.convex_hull
    cc = (ch.centroid.x, ch.centroid.y)
    s_aire = math.sqrt(ch.area / hull.area)
    echelles = [1.0] + ([s_aire] if abs(s_aire - 1) <= 0.06 else [])

    def iou_de(ang, s):
        h = Polygon(_transformee(hull.exterior.coords, ang, s, r.centre_plan, cc))
        return h.intersection(ch).area / max(h.union(ch).area, 1e-9)

    # recherche de la rotation : pas de 3° sur 360°, puis affinage à 0,25° autour des meilleurs candidats
    grossier = sorted(((iou_de(a, s), a, s) for a in range(0, 360, 3) for s in echelles), reverse=True)[:4]
    meilleur = (-1.0, 0.0, 1.0)
    for _, a0, s0 in grossier:
        for k in range(-12, 13):
            a = (a0 + 0.25 * k) % 360
            for s in echelles:
                v = iou_de(a, s)
                if v > meilleur[0]:
                    meilleur = (v, a, s)
    iou_best, ang, s_best = meilleur
    # affinage par les murs : leur direction dominante doit épouser celle des façades de l'emprise cadastrale
    dp, cp = _direction_dominante(_segments_de(calques["MURS"] or calques["TRAITS"]))
    polys = list(cible.geoms) if cible.geom_type == "MultiPolygon" else [cible]
    dc, cc_ = _direction_dominante([(a, b) for p in polys for a, b in zip(list(p.exterior.coords)[:-1], list(p.exterior.coords)[1:])])
    if cp >= 0.35 and cc_ >= 0.35:
        voulu = (dc - dp) % 90
        k_ = round((ang - voulu) / 90)
        ang2 = (voulu + 90 * k_) % 360
        ecart = abs((ang2 - ang + 180) % 360 - 180)
        if 0.3 < ecart <= 12:
            r.avertissements.append(f"Rotation affinée de {ecart:.1f}° d'après la direction des murs.")
            ang = ang2
            iou_best = iou_de(ang, s_best)
    r.angle_deg, r.echelle, r.iou = ang, 1.0, iou_de(ang, 1.0)
    if s_best != 1.0 and iou_best >= 0.6 and iou_best >= r.iou + 0.03:
        r.echelle, r.iou = s_best, iou_best
        r.avertissements.append(f"Dimensions corrigées de {100 * (s_best - 1):+.1f} % pour coller à l'emprise cadastrale.")
    if abs(s_aire - 1) > 0.06:
        r.avertissements.append(f"Écart de {100 * (s_aire - 1):+.0f} % entre l'emprise du plan et celle du cadastre, non corrigé : "
                                "plan partiel ou échelle différente ? Vérifiez l'échelle indiquée.")
    r.tx, r.ty = ch.centroid.x, ch.centroid.y
    r.fiable = r.iou >= 0.6
    if not r.fiable:
        r.avertissements.append(f"Recalage peu fiable (recouvrement {100 * r.iou:.0f} %) : le plan couvre sans doute une partie "
                                "du bâtiment seulement ; vérifiez son placement sur l'aperçu.")
    return r


def appliquer(calques, rec):
    """Applique le recalage à tous les calques → mêmes structures, en Lambert 93."""
    out = {}
    for nom, items in calques.items():
        if nom.startswith("TEXTES"):
            out[nom] = [(rec.point(*pos), txt, h * rec.echelle) for pos, txt, h in items]
        else:
            out[nom] = [([rec.point(x, y) for x, y in it[0]], it[1], *([it[2] * rec.echelle] if len(it) > 2 else []))
                        for it in items]
    return out
