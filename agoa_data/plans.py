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
MAX_PX = 5200                            # taille maximale de l'image pour un plan scanné


class PlanError(Exception):
    """Plan illisible ou échelle introuvable (message destiné à l'utilisateur)."""


@dataclass
class Trace:
    pts: list                            # [(x, y)] en mm sur le papier, y vers le haut
    largeur_mm: float = 0.0
    ferme: bool = False
    rempli: bool = False


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
    """Plan scanné : rendu en image, séparation traits épais (murs) / traits fins, vectorisation."""
    import cv2
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(chemin)
    pg = pdf[min(page, len(pdf) - 1)]
    w_pt, h_pt = pg.get_size()
    k = min(200 / 72, MAX_PX / max(w_pt, h_pt))          # 200 dpi, plafonné
    img = np.array(pg.render(scale=k).to_pil().convert("L"))
    if img.size == 0 or img.std() < 3:
        raise PlanError(f"{plan.nom} : page vide ou sans dessin lisible.")
    _, noir = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    mm_px = 25.4 / (72 * k)                              # millimètres de papier par pixel
    H = img.shape[0]
    seuil = max(3, int(round(EPAISSEUR_MURS_MM / mm_px)))
    epais = cv2.morphologyEx(noir, cv2.MORPH_OPEN, np.ones((seuil, seuil), np.uint8))
    epais = cv2.dilate(epais, np.ones((3, 3), np.uint8))
    clair = (img < 215).astype(np.uint8) * 255                    # traits fins / pâles de la numérisation
    fins = cv2.bitwise_and(clair, cv2.bitwise_not(cv2.dilate(epais, np.ones((3, 3), np.uint8))))

    contours, _ = cv2.findContours(epais, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        if cv2.contourArea(c) * mm_px ** 2 < 0.2:               # < 0,2 mm² sur le papier : poussière
            continue
        a = cv2.approxPolyDP(c, 0.8, True).reshape(-1, 2)
        if len(a) >= 3:
            plan.traces.append(Trace([(float(px) * mm_px, (H - float(py)) * mm_px) for px, py in a], 0.0, True, True))

    seg = _segments(fins, mm_px)
    for x0, y0, x1, y1 in seg:
        if math.hypot(x1 - x0, y1 - y0) * mm_px >= 1.0:          # ≥ 1 mm sur le papier
            plan.traces.append(Trace([(x0 * mm_px, (H - y0) * mm_px), (x1 * mm_px, (H - y1) * mm_px)], 0.1))
    pdf.close()
    plan.scanne = True
    if len(plan.traces) < 5:
        raise PlanError(f"{plan.nom} : aucun trait exploitable dans ce PDF.")
    return plan


def _segments(masque, mm_px):
    """Segments (pixels) d'un masque de traits fins : détecteur LSD, à défaut transformée de Hough."""
    import cv2
    out = []
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        res = lsd.detect(255 - masque)[0]
        if res is not None:
            out = [tuple(map(float, s)) for s in np.asarray(res).reshape(-1, 4)]
    except Exception:                                    # noqa: BLE001
        out = []
    if not out:
        res = cv2.HoughLinesP(masque, 1, np.pi / 180, threshold=25, minLineLength=int(2 / mm_px), maxLineGap=3)
        if res is not None:
            out = [tuple(map(float, s)) for s in np.asarray(res).reshape(-1, 4)]
    return out


# ---------------------------------------------------------------------------------------- mise à l'échelle
def classer(plan, echelle):
    """PlanPDF + échelle (1/x) → dict calque → liste de polylignes en mètres (repère local, y vers le haut) et textes."""
    m = echelle / 1000.0                                 # mm papier → m terrain
    largeurs = [t.largeur_mm for t in plan.traces if t.largeur_mm > 0 and not t.rempli]
    seuil = max(EPAISSEUR_MURS_MM, 1.8 * float(np.percentile(largeurs, 20))) if largeurs and not plan.scanne else EPAISSEUR_MURS_MM
    calques = {"MURS": [], "TRAITS": [], "TEXTES_COTES": [], "TEXTES": []}
    for t in plan.traces:
        pts = [(x * m, y * m) for x, y in t.pts]
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
    for ligne, _ in lignes:
        xs, ys = [p[0] for p in ligne], [p[1] for p in ligne]
        etendues.append(math.hypot(max(xs) - min(xs), max(ys) - min(ys)) if ligne else 0.0)
    if not etendues:
        return []
    seuil = 0.25 * max(etendues)
    return [p for (ligne, _), e in zip(lignes, etendues) if e >= seuil for p in ligne]


def recaler(calques, cible):
    """Place le plan sur l'emprise cadastrale `cible` (Polygon Lambert 93) : rotation, légère mise à l'échelle, translation.

    L'enveloppe convexe des murs du plan est comparée à celle de l'emprise (bâtiment BD TOPO / parcelle) pour les quatre
    orientations possibles ; on retient la meilleure (rapport d'intersection / réunion). La dimension n'est corrigée que si le
    recouvrement est bon et si l'écart d'échelle reste raisonnable (±15 %).
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
    echelles = [1.0] + ([s_aire] if abs(s_aire - 1) <= 0.15 else [])

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
    r.angle_deg, r.echelle, r.iou = ang, 1.0, iou_de(ang, 1.0)
    if s_best != 1.0 and iou_best >= 0.6 and iou_best >= r.iou + 0.03:
        r.echelle, r.iou = s_best, iou_best
        r.avertissements.append(f"Dimensions corrigées de {100 * (s_best - 1):+.1f} % pour coller à l'emprise cadastrale.")
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
            out[nom] = [([rec.point(x, y) for x, y in ligne], ferme) for ligne, ferme in items]
    return out
