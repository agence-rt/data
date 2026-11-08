"""Dessins d'architecte de la façade : relevé, géométrie des ombres, PNG 1080 × 1920, vidéo, planche A3."""
import os
import shutil
import tempfile
import unittest

from PIL import Image

from agoa_data import dessins, planche


def relevé_type():
    c = [1.8 + 2.95 * i for i in range(7)]
    reg = [[x - (1.9 if i == 0 else 1.55) / 2, x + (1.9 if i == 0 else 1.55) / 2] for i, x in enumerate(c)]
    pl, li = [4.88, 7.55, 10.23, 12.90, 15.58], [6.60, 9.27, 11.95, 14.62, 17.30]
    forme = {"voie": "Rue Gracieuse", "longueur": 21.3, "rdc": {"z0": 0, "z1": 4.88},
             "niveaux": [{"nom": f"R+{k + 1}", "plancher": pl[k], "linteau": li[k], "dessus_dalle": pl[k + 1] if k < 4 else 18.25} for k in range(5)],
             "travees": [], "terrasse": {"dalle": 18.25, "lidar": 18.55, "ecart_lidar": None}, "attiques": [], "recul": 4.5}
    etat = [{"niveau": 1, "persiennes": [True, False, True, True, False, True, False], "continu": [False] * 6},
            {"niveau": 2, "persiennes": [False] * 7, "continu": [True] * 6}, None, None, None]
    return dessins.releve(forme, {"travees_regularisees": reg, "etat": etat}, adresse="12 rue Gracieuse, 75005 Paris")


class TestDessins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fac = relevé_type()
        cls.dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_releve_aller_retour_et_mentions(self):
        ch = dessins.ecrire_releve(self.fac, os.path.join(self.dir, "facade.json"))
        back = dessins.charger(ch)
        self.assertEqual(back["voie"], "Rue Gracieuse")
        self.assertTrue(any("R+3" in s and "copiées" in s for s in back["suppose"]))     # niveaux hors photo déclarés
        self.assertTrue(back["non_lu"])

    def test_rapports_hauteur_largeur(self):
        mo = dessins.Modele(self.fac)
        r, w, h = mo.ratio(0)
        self.assertAlmostEqual(w, 1.55 - 0.30, places=2)
        self.assertAlmostEqual(h, 6.60 - 4.88 - 0.03, places=2)
        self.assertAlmostEqual(r, h / w, places=6)

    def test_ombre_longueur_egale_a_la_saillie(self):
        g = dessins.ombre_rect((0.0, 5.0, 3.0, 5.4), 0.8)
        x0, z0, x1, z1 = g.bounds
        self.assertAlmostEqual(x1, 3.0 + 0.8, places=6)           # l'ombre dépasse à droite de la saillie
        self.assertAlmostEqual(z0, 5.0 - 0.8, places=6)           # et descend de la saillie
        self.assertIsNone(dessins.ombre_rect((0, 0, 1, 1), 0.0))

    def test_png_planche_et_pdf_a3(self):
        res = dessins.generer(self.fac, self.dir, videos=False)
        for nom, _ in dessins.SCENES:
            self.assertEqual(Image.open(os.path.join(self.dir, nom + ".png")).size, (1080, 1920), nom)
        self.assertEqual(Image.open(os.path.join(self.dir, "planche.png")).size, (1080, 1920))
        import pdfplumber
        with pdfplumber.open(os.path.join(self.dir, "planche.pdf")) as pdf:
            w, h = pdf.pages[0].width, pdf.pages[0].height
        self.assertAlmostEqual(w / 72 * 25.4, 297, delta=1.0)
        self.assertAlmostEqual(h / 72 * 25.4, 420, delta=1.0)
        self.assertEqual(res["planche"]["fichiers"], [n + ".png" for n, _ in dessins.SCENES])

    def test_premiere_image_non_vide_et_derniere_egale_au_png(self):
        sc = dessins.Elevation(self.fac)
        premiere = sc.rgba(dessins.progression(0))
        self.assertGreater((premiere[..., :3] < 200).sum(), 2000)        # contour amorcé, sol, cartouche : jamais une image vide
        self.assertEqual(dessins.progression(dessins.N_IMG - dessins.N_FIXE), 1.0)
        self.assertEqual(dessins.progression(dessins.N_IMG - 1), 1.0)
        sc.fermer()

    @unittest.skipUnless(dessins.ffmpeg_exe(), "ffmpeg absent")
    def test_video_h264(self):
        import subprocess
        sc = dessins.Elevation(self.fac)
        ch = os.path.join(self.dir, "t.mp4")
        self.assertTrue(dessins.ecrire_mp4(lambda i: sc.rgba(dessins.progression(i)), ch))
        sc.fermer()
        info = subprocess.run([dessins.ffmpeg_exe(), "-i", ch], capture_output=True, text=True).stderr
        self.assertIn("h264", info)
        self.assertIn("yuv420p", info)
        self.assertIn("1080x1920", info)
        self.assertIn("30 fps", info)
        self.assertIn("00:00:03.00", info)


if __name__ == "__main__":
    unittest.main()
