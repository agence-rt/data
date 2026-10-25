"""Test de fumée de l'interface (ignoré si tkinter ou l'affichage est absent ; exécuté sur le poste Windows de la CI)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import tkinter
    import agoa_vol
    tkinter.Tk().destroy()
    DISPO = True
except Exception:                                   # noqa: BLE001 — pas de tkinter / pas d'écran
    DISPO = False


@unittest.skipUnless(DISPO, "tkinter ou affichage indisponible")
class Fenetre(unittest.TestCase):
    def test_ouverture_et_affichage_des_resultats(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            for n in ("plan_coupes.png", "coupe_1_perp_Rue_Test.png"):
                Image.new("RGB", (1500, 800), "white").save(os.path.join(d, n))
            res = {"adresse": {"label": "1 Rue Test 75001 Paris"}, "parcelle": {"section": "AA", "numero": "0001", "idu": "x", "contenance": 100},
                   "dalles": ["a.copc.laz"], "date": "01/01/2026 10:00", "dossier": d, "n_points": 10, "voies": [],
                   "batiments": [{"usage": "Résidentiel", "etages": 5, "annee": "1900", "volumetrie": {
                       "emprise_m2": 100, "altitude_sol": 50.0, "sol_min": 49.9, "sol_max": 50.1, "hauteur_moyenne": 15.0,
                       "hauteur_max": 18.0, "volume_m3": 1500}}],
                   "coupes": [{"voie": "Rue Test", "type": "perpendiculaire", "fichier": "coupe_1_perp_Rue_Test.png",
                               "resultats": {"batiment_traverse": True, "sol": 50.0, "egout": 65.0, "egout_h": 15.0,
                                             "faitage": 68.0, "faitage_h": 18.0}}]}
            app = agoa_vol.App()
            try:
                app.adr.set("198 rue Championnet")
                app.update()
                self.assertEqual(app.nom.get(), "CHAMPIONNET198")
                app._fin(res)
                app.update()
                self.assertEqual(len(app.choix["values"]), 2)
                self.assertIn("perpendiculaire", app.choix["values"][1])
                self.assertIn("1500 m³", app.txt.get("1.0", "end"))
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
