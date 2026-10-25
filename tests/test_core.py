"""Tests du cœur d'AGOA VOL : aucun accès réseau (nuage synthétique, serveur HTTP local)."""
import http.server
import math
import os
import sys
import threading
import unittest

import numpy as np
from shapely.geometry import LineString, box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agoa_data import coupes, pipeline, volumetrie          # noqa: E402
from agoa_data.geo import Voie                                # noqa: E402
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
