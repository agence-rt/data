"""Tests de la fonction « Plans DWG » : lecture PDF vectoriel et scanné, recalage, DXF, convertisseur DWG (sans réseau)."""
import math
import os
import sys
import tempfile
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                              # noqa: E402
from PIL import Image                                         # noqa: E402
from shapely.affinity import rotate, scale                    # noqa: E402
from shapely.geometry import Polygon                          # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agoa_data import dwg, plans                              # noqa: E402

# bâtiment « cadastral » en L93 (non rectangulaire, pour lever les ambiguïtés de rotation)
CIBLE = Polygon([(651000, 6866200), (651040, 6866200), (651040, 6866215), (651022, 6866215), (651022, 6866230), (651000, 6866230)])


def fabrique_pdf(dossier, angle=0.0, reduction=1.0, ech=200, texte=True):
    """Plan A3 de CIBLE tourné de `angle`°, réduit de `reduction`, à l'échelle 1/ech : PDF vectoriel + version scannée."""
    c = CIBLE.centroid
    g = scale(rotate(CIBLE, angle, origin=c), reduction, reduction, origin=c)
    fig = plt.figure(figsize=(420 / 25.4, 297 / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 420)
    ax.set_ylim(0, 297)
    ax.axis("off")
    xy = [((x - c.x) * 1000 / ech + 210, (y - c.y) * 1000 / ech + 148) for x, y in g.exterior.coords]
    ax.plot(*zip(*xy), color="k", lw=2.0)
    for k in range(1, 5):
        a, b = xy[0], xy[1]
        p = (a[0] + (b[0] - a[0]) * k / 5, a[1] + (b[1] - a[1]) * k / 5)
        ax.plot([p[0], p[0] + 40], [p[1], p[1] + 40], color="k", lw=0.3)
    if texte:
        ax.text(15, 15, f"Plan RDC - Echelle 1/{ech}", fontsize=12)
        ax.text(200, 150, "12.45", fontsize=9)
    v = os.path.join(dossier, "v.pdf")
    fig.savefig(v)
    fig.savefig(os.path.join(dossier, "s.png"), dpi=150)
    plt.close(fig)
    Image.open(os.path.join(dossier, "s.png")).convert("L").save(os.path.join(dossier, "s.pdf"), resolution=150)
    return v, os.path.join(dossier, "s.pdf")


class Lecture(unittest.TestCase):
    def test_echelle_dans_les_textes(self):
        self.assertEqual(plans.echelle_dans_textes(["Plan", "RDC", "Echelle", "1/50"]), 50)
        self.assertEqual(plans.echelle_dans_textes(["1:100"]), 100)
        self.assertIsNone(plans.echelle_dans_textes(["12.45", "1/7"]))

    def test_vectoriel_et_scanne(self):
        with tempfile.TemporaryDirectory() as d:
            v, s = fabrique_pdf(d)
            pv = plans.lire_pdf(v)
            self.assertFalse(pv.scanne)
            self.assertEqual(pv.echelle, 200)
            cal = plans.classer(pv, 200)
            self.assertTrue(cal["MURS"])
            self.assertTrue(cal["TRAITS"])
            self.assertEqual([t[1] for t in cal["TEXTES_COTES"]], ["12.45"])      # la valeur de cote est reconnue
            ps = plans.lire_pdf(s)
            self.assertTrue(ps.scanne)
            self.assertIsNone(ps.echelle)                                          # pas de texte lisible dans un scan
            self.assertTrue(plans.classer(ps, 200)["MURS"])

    def test_pdf_illisible(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "x.pdf")
            open(f, "wb").write(b"pas un pdf")
            with self.assertRaises(plans.PlanError):
                plans.lire_pdf(f)


class Scan(unittest.TestCase):
    def test_murs_epais_et_traits_fins_du_scan(self):
        """Un trait épais devient un mur (avec sa largeur), un trait fin un trait ; le cadre de la feuille est ignoré."""
        import numpy as np
        with tempfile.TemporaryDirectory() as d:
            im = np.full((2480, 3508), 255, np.uint8)            # A4 paysage à 300 dpi
            im[1000:1010, 600:2600] = 0                          # trait épais ~0,85 mm, 169 mm de long
            im[1300:1302, 600:2600] = 0                          # trait fin ~0,17 mm
            im[20:22, 100:3400] = 0                              # cadre de la feuille, à ignorer
            f = os.path.join(d, "s.pdf")
            Image.fromarray(im).save(f, resolution=300)
            pl = plans.lire_pdf(f)
            roles = sorted(t.role for t in pl.traces)
            self.assertEqual(roles, ["mur", "trait"])
            mur = next(t for t in pl.traces if t.role == "mur")
            self.assertAlmostEqual(mur.largeur_mm, 0.85, delta=0.2)
            cal = plans.classer(pl, 50)
            self.assertEqual(len(cal["MURS"]), 1)
            self.assertAlmostEqual(cal["MURS"][0][2], 0.85 * 50 / 1000, delta=0.01)   # largeur en mètres réels

    def test_seuil_epaisseur(self):
        self.assertAlmostEqual(plans._seuil_epaisseur([0.15] * 30 + [0.6] * 30), 0.4, delta=0.25)
        self.assertEqual(plans._seuil_epaisseur([0.2] * 10), 0.33)


class Recalage(unittest.TestCase):
    def test_rotation_et_dimensions_retrouvees(self):
        with tempfile.TemporaryDirectory() as d:
            v, _ = fabrique_pdf(d, angle=37, reduction=0.96)
            cal = plans.classer(plans.lire_pdf(v), 200)
            rec = plans.recaler(cal, CIBLE)
            self.assertTrue(rec.fiable)
            self.assertGreater(rec.iou, 0.97)
            self.assertAlmostEqual(rec.echelle, 1 / 0.96, delta=0.02)               # dimensions corrigées
            self.assertAlmostEqual((rec.angle_deg + 37) % 360, 0, delta=1.5)        # rotation inverse
            placé = plans.appliquer(cal, rec)
            xs = [p[0] for ligne, _ in placé["MURS"] for p in ligne]
            self.assertAlmostEqual((min(xs) + max(xs)) / 2, (CIBLE.bounds[0] + CIBLE.bounds[2]) / 2, delta=1.0)    # centre de l'emprise

    def test_sans_cible_pas_de_recalage(self):
        with tempfile.TemporaryDirectory() as d:
            v, _ = fabrique_pdf(d)
            rec = plans.recaler(plans.classer(plans.lire_pdf(v), 200), None)
            self.assertFalse(rec.fiable)
            self.assertEqual(rec.echelle, 1.0)
            self.assertTrue(rec.avertissements)

    def test_scan_recale(self):
        with tempfile.TemporaryDirectory() as d:
            _, s = fabrique_pdf(d, angle=-20, reduction=1.0)
            rec = plans.recaler(plans.classer(plans.lire_pdf(s), 200), CIBLE)
            self.assertGreater(rec.iou, 0.8)
            self.assertAlmostEqual((rec.angle_deg - 20) % 360, 0, delta=3)


class Dessin(unittest.TestCase):
    def test_dxf_calques(self):
        import ezdxf
        from agoa_data.geo import Parcelle
        with tempfile.TemporaryDirectory() as d:
            v, _ = fabrique_pdf(d)
            cal = plans.classer(plans.lire_pdf(v), 200)
            rec = plans.recaler(cal, CIBLE)
            f = dwg.ecrire_dxf(os.path.join(d, "t.dxf"), [("RDC Enghien", plans.appliquer(cal, rec), rec.angle_deg)],
                               {"parcelle": Parcelle("i", "AH", "1", 1.0, CIBLE.buffer(2)), "voisines": [], "batiments": [CIBLE]})
            doc = ezdxf.readfile(f)
            noms = {la.dxf.name for la in doc.layers}
            for attendu in ("RDC_ENGHIEN_MURS", "RDC_ENGHIEN_TRAITS", "RDC_ENGHIEN_TEXTES_COTES", "CADASTRE_BATIMENTS",
                            "CADASTRE_PARCELLE_CIBLE"):
                self.assertIn(attendu, noms)
            self.assertEqual(doc.units, 6)

    def test_commande_oda_et_absence(self):
        c = dwg.commande_oda("ODAFileConverter.exe", "in", "out", "a.dxf")
        self.assertEqual(c, ["ODAFileConverter.exe", "in", "out", "ACAD2018", "DWG", "0", "1", "a.dxf"])
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.dxf")
            open(f, "w").write("0\nEOF\n")
            vrai, dwg.trouver_oda = dwg.trouver_oda, lambda: None
            try:
                self.assertIsNone(dwg.convertir_dwg(f))                              # pas de convertisseur : DXF seul
            finally:
                dwg.trouver_oda = vrai


if __name__ == "__main__":
    unittest.main()
