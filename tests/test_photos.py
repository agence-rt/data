"""Niveau 2 : projection de photos à pose connue, recalage, détection des baies, chaîne complète avec un Panoramax simulé."""
import math
import os
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agoa_data import facade_photos, facades, ouvertures, panoramax, photos     # noqa: E402
from agoa_data.facades import Facade                                             # noqa: E402

L, HAUT, Z0 = 16.0, 20.0, 50.0                  # façade 16 m x 20 m, pied à 50 m NGF ; 50 px/m
O = np.array([1000.0, 2000.0, 0.0])


def texture_facade():
    """Façade synthétique : pierre beige, 3 colonnes x 6 niveaux de baies (cadre blanc, vitrage sombre), garde-corps."""
    img = np.full((int(HAUT * 50), int(L * 50), 3), (150, 190, 215), np.uint8)             # beige (BGR)
    rng = np.random.default_rng(3)
    img = np.clip(img + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)
    for _ in range(900):                                                                    # appareillage de pierre : taches discrètes
        x, y = int(rng.integers(0, img.shape[1] - 30)), int(rng.integers(0, img.shape[0] - 20))
        wv, hv = int(rng.integers(8, 30)), int(rng.integers(5, 20))
        img[y:y + hv, x:x + wv] = np.clip(img[y:y + hv, x:x + wv].astype(int) + int(rng.integers(-14, 14)), 0, 255)
    for n in range(6):
        ybas = int((HAUT - 1.0 - n * 3.0) * 50)
        for c in range(3):
            x0 = int((2.5 + c * 5.0) * 50)
            cv2.rectangle(img, (x0, ybas - 140), (x0 + 90, ybas), (245, 245, 245), -1)       # cadre
            cv2.rectangle(img, (x0 + 10, ybas - 130), (x0 + 80, ybas - 15), (60, 55, 50), -1)  # vitrage
            cv2.rectangle(img, (x0 - 5, ybas - 40), (x0 + 95, ybas - 35), (40, 40, 40), -1)  # garde-corps
    return img


def rendre_plat(tex, cam, yaw, pitch, fpx, W=1500, H=1100):
    th, ya = math.radians(pitch), math.radians(yaw)
    fwd = np.array([math.cos(th) * math.sin(ya), math.cos(th) * math.cos(ya), math.sin(th)])
    right = np.array([math.cos(ya), -math.sin(ya), 0.0])
    up = np.array([-math.sin(th) * math.sin(ya), -math.sin(th) * math.cos(ya), math.cos(th)])
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    d = right * ((u - W / 2) / fpx)[..., None] + up * ((H / 2 - v) / fpx)[..., None] + fwd
    n = np.array([0, -1.0, 0])
    s = ((O - np.array(cam)) @ n) / (d @ n)
    P = np.array(cam) + s[..., None] * d
    return _lire_tex(tex, P[..., 0] - O[0], P[..., 2], s > 0)


def rendre_equi(tex, cam, az, W=4000, H=2000):
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    br, la = np.radians(((u + 0.5) / W - 0.5) * 360 + az), np.radians((0.5 - (v + 0.5) / H) * 180)
    d = np.stack([np.cos(la) * np.sin(br), np.cos(la) * np.cos(br), np.sin(la)], -1)
    n = np.array([0, -1.0, 0])
    den = d @ n
    s = ((O - np.array(cam)) @ n) / np.where(np.abs(den) < 1e-9, 1e-9, den)
    P = np.array(cam) + s[..., None] * d
    return _lire_tex(tex, P[..., 0] - O[0], P[..., 2], s > 0)


def _lire_tex(tex, t, z, ok):
    u = np.clip(t * 50, 0, tex.shape[1] - 1).astype(np.float32)
    vv = np.clip((Z0 + HAUT - z) * 50, 0, tex.shape[0] - 1).astype(np.float32)
    img = cv2.remap(tex, u, vv, cv2.INTER_LINEAR)
    ok = ok & (t >= 0) & (t <= L) & (z >= Z0) & (z <= Z0 + HAUT)
    img[~ok] = (150, 210, 150)
    return img


def facade():
    f = Facade("Rue Test", (1000.0, 2000.0), (1.0, 0.0), (0.0, -1.0), L)
    T = np.arange(0, L + 1e-6, 0.5)
    f.r = {"abscisses": T, "sol": np.full(len(T), Z0), "egout": np.full(len(T), Z0 + HAUT), "sol_med": Z0, "egout_med": Z0 + HAUT}
    return f


def corr(a, b, ok):
    a = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (0, 0), 3).astype(float)
    b = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (0, 0), 3).astype(float)
    return np.corrcoef(a[ok], b[ok])[0, 1]


class Projection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tex = texture_facade()
        cls.f = facade()

    def test_image_360(self):
        cam = (1005.0, 1985.0, Z0 + 2.3)
        img = rendre_equi(self.tex, cam, 20.0)
        p = panoramax.Photo("a", cam[0], cam[1], 20.0, True, "")
        el, ok, _ = photos.projeter(img, p, self.f, Z0, Z0 + HAUT, 50.0, 2.3, Z0)
        self.assertGreater(ok.mean(), 0.9)
        self.assertGreater(corr(el, self.tex, ok), 0.9)

    def test_image_plate_tangage_retrouve(self):
        cam = (1006.0, 1985.0, Z0 + 2.3)
        yaw = math.degrees(math.atan2(1008 - cam[0], 2000 - cam[1]))
        img = rendre_plat(self.tex, cam, yaw, 30, 1400)
        p = panoramax.Photo("b", cam[0], cam[1], yaw, False, "", fov=2 * math.degrees(math.atan(750 / 1400)))
        el, ok, meta = photos.projeter(img, p, self.f, Z0, Z0 + HAUT, 50.0, 2.3, Z0)
        self.assertAlmostEqual(meta["tangage"], 30, delta=4)
        self.assertGreater(ok.mean(), 0.4)
        self.assertGreater(corr(el, self.tex, ok), 0.8)


class Recalage(unittest.TestCase):
    def test_affine_retrouvee(self):
        tex = texture_facade()
        M0 = np.float32([[0.9, 0.05, 40], [-0.03, 0.92, 25]])
        deplace = cv2.warpAffine(tex, M0, (tex.shape[1], tex.shape[0]), borderValue=(255, 255, 255))
        M, n = photos.aligner(tex, deplace, min_inliers=15)
        self.assertIsNotNone(M)
        rec = cv2.warpAffine(deplace, M, (tex.shape[1], tex.shape[0]), borderValue=(255, 255, 255))
        ok = cv2.warpAffine(np.full(deplace.shape[:2], 255, np.uint8), M, (tex.shape[1], tex.shape[0])) > 0
        self.assertGreater(corr(rec, tex, ok), 0.9)

    def test_assembler_priorite_a_la_base(self):
        a = np.full((4, 4, 3), 10, np.uint8)
        b = np.full((4, 4, 3), 200, np.uint8)
        ma = np.zeros((4, 4), bool)
        ma[:, :2] = True
        out, m = photos.assembler(a, ma, [(b, np.ones((4, 4), bool))])
        self.assertEqual(out[0, 0, 0], 10)
        self.assertEqual(out[0, 3, 0], 200)
        self.assertTrue(m.all())


class Ouvertures(unittest.TestCase):
    def test_trame_de_baies(self):
        tex = texture_facade()
        h, w = tex.shape[:2]
        valide = np.ones((h, w), bool)
        det = ouvertures.aligner_sur_trames(ouvertures.detecter(tex, valide, 50.0), 50.0)
        baies = [o for o in det if o["type"] in ("baie", "fenetre")]
        self.assertGreaterEqual(len(baies), 14)                      # 18 baies réelles (3 x 6)
        self.assertLessEqual(len(baies), 22)
        for o in baies:
            self.assertTrue(0.6 <= o["w_m"] <= 2.2)


class ChaineComplete(unittest.TestCase):
    def test_panoramax_simule(self):
        tex = texture_facade()
        f = facade()
        cam1 = (1008.0, 1984.0, Z0 + 2.3)
        cam2 = (1003.0, 1982.0, Z0 + 2.3)
        yaw2 = math.degrees(math.atan2(1008 - cam2[0], 2000 - cam2[1]))
        imgs = {"A": rendre_equi(tex, cam1, 5.0), "B": rendre_plat(tex, cam2, yaw2, 25, 1400)}
        pho = [panoramax.Photo("A", cam1[0], cam1[1], 5.0, True, "A", "2024-05-01"),
               panoramax.Photo("B", cam2[0], cam2[1], yaw2, False, "B", "2023-05-01", fov=2 * math.degrees(math.atan(750 / 1400)))]
        d = tempfile.mkdtemp()
        res = facade_photos.traiter(f, 1, d, photos_dir=None, chercher=lambda x, y: pho, telecharger=lambda p: imgs[p.url])
        self.assertTrue(res["photos"], res["avertissements"])
        self.assertTrue(os.path.exists(os.path.join(d, res["elevation"])))
        self.assertGreaterEqual(sum(o["type"] in ("baie", "fenetre") for o in res["ouvertures"]), 12)
        facades.ecrire_facades_dxf([f], os.path.join(d, "f.dxf"))
        import ezdxf
        doc = ezdxf.readfile(os.path.join(d, "f.dxf"))
        noms = [l.dxf.name for l in doc.layers if l.dxf.name.endswith("_BAIES")]
        self.assertTrue(noms)
        self.assertGreater(len(doc.modelspace().query(f'LWPOLYLINE[layer=="{noms[0]}"]')), 10)

    def test_sans_photo_degrade_proprement(self):
        f = facade()
        res = facade_photos.traiter(f, 1, tempfile.mkdtemp(), chercher=lambda x, y: [], telecharger=None)
        self.assertEqual(res["ouvertures"], [])
        self.assertTrue(res["avertissements"])

    def test_panoramax_en_panne(self):
        def boom(x, y):
            raise RuntimeError("hors ligne")
        res = facade_photos.traiter(facade(), 1, tempfile.mkdtemp(), chercher=boom, telecharger=None)
        self.assertEqual(res["ouvertures"], [])
        self.assertTrue(any("indisponible" in a for a in res["avertissements"]))


class Selection(unittest.TestCase):
    def test_choix_cote_rue_et_orientation(self):
        f = facade()
        bonnes = panoramax.Photo("ok", 1008.0, 1986.0, 0.0, False, "u")           # au sud (côté rue), regarde vers le nord
        derriere = panoramax.Photo("derriere", 1008.0, 2010.0, 180.0, False, "u")   # dans l'immeuble
        tourne = panoramax.Photo("tourne", 1008.0, 1986.0, 180.0, False, "u")       # regarde ailleurs
        trop_loin = panoramax.Photo("loin", 1008.0, 1930.0, 0.0, True, "u")
        sel = panoramax.selectionner([derriere, tourne, trop_loin, bonnes], f)
        self.assertEqual([p.id for p in sel], ["ok"])


if __name__ == "__main__":
    unittest.main()
