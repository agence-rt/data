import os
import tempfile
import unittest

import numpy as np

from agoa_data import facades, livraison, panoramax, process


def _facade(L=20.0):
    return facades.Facade("Rue Test", (0.0, 0.0), (1.0, 0.0), (0.0, 1.0), L)


class CompilerVues(unittest.TestCase):
    def test_les_vues_voisines_ne_comblent_que_les_colonnes_manquantes(self):
        ech, L, H = 10, 20.0, 100
        f = _facade(L)

        def rendre(pano, p, f_, t, d, ech=10, z_haut=9.5):
            img = np.full((H, int(L * ech), 3), 255, np.uint8)
            mk = np.zeros((H, int(L * ech)), bool)
            a, b = (0, 130) if p.id == "A" else (110, int(L * ech))
            mk[:, a:b] = True
            img[:, a:b] = (60, 60, 200) if p.id == "A" else (200, 60, 60)
            return img, mk, {"fov": 125.0}

        cands = [panoramax.Photo("A", 5.0, 8.0, 0.0, True, ""), panoramax.Photo("B", 15.0, 8.0, 0.0, True, "")]
        ch = (cands[0], 5.0, 8.0)
        img, mk, info = process.compiler_vues(f, cands, ch, lambda p: np.zeros((10, 20, 3), np.uint8), ech, 9.5, lambda s: None, rendre=rendre)
        self.assertGreaterEqual(info["couverture_largeur"], 0.97)
        self.assertEqual([v["id"] for v in info["vues"]], ["A", "B"])
        self.assertTrue((img[50, 50] == (60, 60, 200)).all())        # la base n'est pas écrasée
        self.assertTrue((img[50, 190] == (200, 60, 60)).all())       # le complément ne couvre que ce qui manquait


class Trame(unittest.TestCase):
    ELEV = {"planchers_m": [4.9, 7.6, 10.3, 13.0], "linteaux_m": [6.7, 9.4, 12.1], "corniche_rdc_m": 4.9, "niveaux_extrapoles": True}
    SEGS = [(0.8, 2.7), (3.8, 5.3), (6.5, 8.0)]

    def test_axes_niveaux_et_rapports(self):
        hyp = []
        t = livraison.construire_trame(20.0, 13.0, self.ELEV, self.SEGS, {}, _facade(), hyp, True)
        self.assertEqual(t["n_travees"], 3)
        self.assertEqual(t["n_niveaux"], 3)
        self.assertEqual(len(t["baies"]), 9)
        self.assertEqual(t["largeurs_m"][1], t["largeurs_m"][2])        # seule la travée 1 garde sa largeur
        r = t["rapports_h_l"][0]
        self.assertAlmostEqual(r["rapport_h_l"], round(1.8 / t["largeurs_m"][1], 2), places=2)
        self.assertTrue(any("extrapolés" in h for h in hyp))

    def test_dxf_lisible_avec_calques(self):
        import ezdxf
        t = livraison.construire_trame(20.0, 13.0, self.ELEV, self.SEGS, {}, _facade(), [], False)
        d = {"adresse": "x", "nom": "X1", "voie": "Rue Test", "version": "0", "date": "", "L": 20.0, "sol": 45.0, "trame": t, "photo": None,
             "coupe": None, "plan": None, "hypotheses": ["h"], "egout": None, "faitage": None}
        with tempfile.TemporaryDirectory() as tmp:
            ch = livraison.ecrire_dxf(d, os.path.join(tmp, "a.dxf"))
            doc = ezdxf.readfile(ch)
        noms = {e.dxf.layer for e in doc.modelspace()}
        self.assertTrue({"FAC_AXES", "FAC_BAIES", "FAC_NIVEAUX", "FAC_COTES"} <= noms)


if __name__ == "__main__":
    unittest.main()
