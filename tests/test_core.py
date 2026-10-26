"""Tests du cœur d'AGOA DATA : aucun accès réseau (nuage synthétique, serveur HTTP local)."""
import http.server
import math
import os
import sys
import threading
import unittest

import numpy as np
from shapely.geometry import LineString, box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agoa_data import cadastre, coupes, geo, pipeline, volumetrie   # noqa: E402
from agoa_data.geo import Adresse, Parcelle, Voie             # noqa: E402
from agoa_data.httprange import HttpRangeFile                 # noqa: E402

SOL, EGOUT, FAITAGE = 50.0, 62.0, 65.0      # altitudes (m)
L, W = 20.0, 10.0                           # bâtiment 20 m (est-ouest) x 10 m (nord-sud)


def nuage_toit_deux_pans(rng=np.random.default_rng(1)):
    """Bâtiment 20 x 10 m, sol à 50, égout à 62, faîtage à 65 (axe est-ouest), rue au sud (y = -8)."""
    x0, y0 = 1000.0, 2000.0
    pts = []
    for _ in range(40000):                                  # toit
        x, y = rng.uniform(x0, x0 + L), rng.uniform(y0, y0 + W)
        z = EGOUT + (FAITAGE - EGOUT) * (1 - abs(y - (y0 + W / 2)) / (W / 2))
        pts.append((x, y, z, 6))
    for _ in range(40000):                                  # sol autour
        x, y = rng.uniform(x0 - 25, x0 + L + 25), rng.uniform(y0 - 25, y0 + W + 25)
        if not (x0 - 0.2 < x < x0 + L + 0.2 and y0 - 0.2 < y < y0 + W + 0.2):
            pts.append((x, y, SOL + rng.normal(0, 0.02), 2))
    return np.array(pts), box(x0, y0, x0 + L, y0 + W)


class Volume(unittest.TestCase):
    def test_volume_toit_deux_pans(self):
        pts, emprise = nuage_toit_deux_pans()
        v = volumetrie.calculer(pts, emprise)
        attendu = L * W * (EGOUT - SOL) + L * (W * (FAITAGE - EGOUT) / 2)    # 2400 + 300 m³
        self.assertLess(abs(v.volume_m3 - attendu) / attendu, 0.02)
        self.assertAlmostEqual(v.altitude_sol, SOL, delta=0.1)
        self.assertGreater(v.couverture, 0.98)

    def test_sans_points_batiment(self):
        pts, emprise = nuage_toit_deux_pans()
        pts[:, 3] = np.where(pts[:, 3] == 6, 1, pts[:, 3])        # toit « non classé »
        with self.assertRaises(ValueError):
            volumetrie.calculer(pts, emprise)


class Coupes(unittest.TestCase):
    def test_coupe_perpendiculaire_a_la_voie(self):
        pts, emprise = nuage_toit_deux_pans()
        rue = Voie("Rue Test", LineString([(960, 1992), (1060, 1992)]), 8.0)
        liste = coupes.definir_coupes(emprise, [rue])
        perp = [c for c in liste if c.type == "perpendiculaire"]
        self.assertEqual(len(perp), 1)
        # la coupe est bien perpendiculaire à la voie (vecteur de lecture ⟂ direction de la rue)
        self.assertAlmostEqual(abs(np.dot(perp[0].u, (1, 0))), 0.0, places=6)
        coupes.analyser_coupe(pts, emprise, perp[0], SOL)
        r = perp[0].r
        self.assertTrue(r["batiment_traverse"])
        self.assertAlmostEqual(r["sol"], SOL, delta=0.15)
        self.assertAlmostEqual(r["egout"], EGOUT, delta=0.3)
        self.assertAlmostEqual(r["faitage"], FAITAGE, delta=0.3)
        self.assertAlmostEqual(r["pente_deg"], math.degrees(math.atan(3 / 5)), delta=3)

    def test_une_coupe_par_voie(self):
        pts, emprise = nuage_toit_deux_pans()
        rue_sud = Voie("Rue Sud", LineString([(960, 1992), (1060, 1992)]), 8.0)
        rue_est = Voie("Rue Est", LineString([(1030, 1950), (1030, 2050)]), 10.0)
        liste = coupes.definir_coupes(emprise, [rue_sud, rue_est])
        self.assertEqual([c.nom for c in liste if c.type == "perpendiculaire"], ["Rue Sud", "Rue Est"])
        est = [c for c in liste if c.nom == "Rue Est" and c.type == "perpendiculaire"][0]
        self.assertAlmostEqual(abs(np.dot(est.u, (0, 1))), 0.0, places=6)    # perpendiculaire à une rue nord-sud


class Dossier(unittest.TestCase):
    def test_noms(self):
        n = pipeline.nom_dossier
        self.assertEqual(n("198 Rue Championnet 75018 Paris"), "CHAMPIONNET198")
        self.assertEqual(n("85 Rue Daguerre 75014 Paris"), "DAGUERRE85")
        self.assertEqual(n("7 Avenue de Saint-Ouen 75018 Paris"), "STOUEN7")
        self.assertEqual(n("12 bis Rue des Écoles 75005 Paris"), "ECOLES12BIS")


class DxfVolumetrie(unittest.TestCase):
    def test_nuage_et_coupes_en_dxf(self):
        import tempfile
        import ezdxf
        from agoa_data import dwg
        pts, emprise = nuage_toit_deux_pans()
        voie = Voie("Rue Test", LineString([(995, 1992), (1030, 1992)]), 8.0)
        liste = coupes.definir_coupes(emprise, [voie])
        donnees = []
        for c in liste:
            q, z, cl = coupes.analyser_coupe(pts, emprise, c, SOL)
            donnees.append((c, q, z, cl))
        with tempfile.TemporaryDirectory() as d:
            f, n = dwg.ecrire_nuage_dxf(pts, os.path.join(d, "n.dxf"), emprise=emprise, coupes=liste, maximum=30000)
            self.assertLessEqual(n, 30000)
            doc = ezdxf.readfile(f)
            noms = {la.dxf.name for la in doc.layers}
            self.assertTrue({"NUAGE_BATI", "NUAGE_SOL", "EMPRISE_BATIMENT", "COUPES_TRACES"} <= noms)
            g = dwg.ecrire_coupes_dxf(donnees, os.path.join(d, "c.dxf"))
            doc = ezdxf.readfile(g)
            textes = " ".join(e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT")
            self.assertIn("Faitage", textes)
            self.assertIn("Egout", textes)

    def test_eclaircissement_garde_le_bati(self):
        import numpy as np
        from agoa_data import dwg
        pts = np.array([(i, i, 50.0, 2) for i in range(1000)] + [(i, i, 60.0, 6) for i in range(200)], float)
        r = dwg._eclaircir(pts, 400)
        self.assertLessEqual(len(r), 400)
        self.assertEqual(int((r[:, 3] == 6).sum()), 200)


class Suggestions(unittest.TestCase):
    def test_suggerer_paris_puis_france(self):
        appels = []
        feat = {"geometry": {"coordinates": [2.3317, 48.8940]},
                "properties": {"label": "198 Rue Championnet 75018 Paris", "citycode": "75118", "score": 0.89}}

        def faux(url, params=None, **kw):
            appels.append(dict(params))
            return {"features": [feat] if "citycode" in params else []}
        vrai, geo.get_json = geo.get_json, faux
        try:
            r = geo.suggerer("198 rue champ")
            self.assertEqual(r[0].label, "198 Rue Championnet 75018 Paris")
            self.assertEqual(appels[0]["citycode"], "75056")
            self.assertEqual(appels[0]["autocomplete"], 1)
            self.assertAlmostEqual(r[0].x, 651004.7, delta=5)
            appels.clear()
            self.assertEqual(geo.suggerer("12 rue de la paix 69000"), [])   # autre ville : pas de restriction à Paris
            self.assertNotIn("citycode", appels[0])
            self.assertEqual(geo.suggerer("ab"), [])                         # trop court : aucun appel
        finally:
            geo.get_json = vrai

    def test_adresse_deja_choisie_non_regeocodee(self):
        a = Adresse("1 Rue X 75001 Paris", 2.3, 48.8, 650000.0, 6860000.0, "75101", 0.9)
        self.assertIs(geo.geocoder(a), a)


class PlanCadastral(unittest.TestCase):
    def test_trois_plans_a3_vectoriels(self):
        import re
        import tempfile
        parc = Parcelle("75118000AH0035", "AH", "35", 450.0, box(1000, 2000, 1030, 2015))
        voisines = [Parcelle("x", "AH", "36", 300.0, box(1030, 2000, 1050, 2015)), parc]
        adr = Adresse("1 Rue Test 75018 Paris", 2.3, 48.8, 1015.0, 2007.0, "75118", 1.0)
        with tempfile.TemporaryDirectory() as d:
            noms = cadastre.plans_cadastraux(adr, parc, d, voisines=voisines, batiments=[box(1005, 2003, 1025, 2012)],
                                             voies=[("Rue Test", LineString([(900, 1990), (1160, 1995)]))])
            self.assertEqual(noms, [f"plan_cadastral_75118000AH0035_1-{e}.pdf" for e in (500, 1000, 2000)])
            for n in noms:
                data = open(os.path.join(d, n), "rb").read()
                self.assertTrue(data.startswith(b"%PDF-"))
                m = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", data)
                self.assertAlmostEqual(float(m.group(1)), 420 / 25.4 * 72, delta=1)      # A3 paysage
                self.assertAlmostEqual(float(m.group(2)), 297 / 25.4 * 72, delta=1)

    def test_fenetre_a_l_echelle(self):
        parc = Parcelle("i", "AH", "1", 1.0, box(0, 0, 10, 10))
        cx, cy, dx, dy = cadastre.fenetre(parc, 500)
        self.assertEqual((cx, cy), (5, 5))
        self.assertAlmostEqual(2 * dx, 210.0)        # 420 mm × 500
        self.assertAlmostEqual(2 * dy, 148.5)        # 297 mm × 500


class Plage(unittest.TestCase):
    """HttpRangeFile contre un petit serveur local qui gère l'en-tête Range et refuse une fois (429)."""

    def test_lecture_partielle(self):
        data = bytes(range(256)) * 20000                       # ≈ 5 Mo
        etat = {"n": 0, "refus": 1}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if etat["refus"]:
                    etat["refus"] -= 1
                    self.send_response(429)
                    self.end_headers()
                    return
                a, b = self.headers["Range"].split("=")[1].split("-")
                a, b = int(a), min(int(b), len(data) - 1)
                etat["n"] += 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
                self.send_header("Content-Length", str(b - a + 1))
                self.end_headers()
                self.wfile.write(data[a:b + 1])

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        import agoa_data.httprange as hr
        hr.MIN_INTERVAL = 0.0
        try:
            f = HttpRangeFile(f"http://127.0.0.1:{srv.server_port}/x", block=1 << 16)
            self.assertEqual(f.size, len(data))
            f.seek(100000)
            self.assertEqual(f.read(70000), data[100000:170000])
            f.seek(100010)
            self.assertEqual(f.read(10), data[100010:100020])     # relu depuis le cache
            n = etat["n"]
            f.seek(100010)
            f.read(10)
            self.assertEqual(etat["n"], n)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
