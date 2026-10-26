"""AGOA DATA — volumétrie d'un bâtiment à partir du nuage de points LiDAR HD de l'IGN.

Application Windows de la gamme AGOA (Python 3.10+, tkinter). Saisir une adresse, puis choisir : 1 cadastres (PDF A3),
2 volumétrie (nuage de points), 3 plans DWG (fond de plan depuis des PDF). Volumétrie : l'application
trouve la parcelle, les bâtiments et les voies, télécharge le morceau utile du nuage de points,
calcule la volumétrie et trace les coupes (une perpendiculaire à chaque voie), puis range les
livrables dans un dossier (nuage .laz, modèle de toiture .asc, coupes .png, plans cadastraux A3 .pdf, synthèse .txt).
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk

from agoa_data import APP_NAME, APP_VERSION, geo, pipeline     # APP_VERSION : à augmenter à chaque publication (cf. README)
from agoa_data.geo import DataError

# Charte graphique commune à la gamme AGOA
RED = "#B3261E"
BG = "#F3F2EE"
INK = "#111111"
MUTED = "#7A7A75"
LINE = "#D6D4CC"
FIELD = "#F7F6F2"
FONT = "Segoe UI"

ROOT = os.environ.get("AGOA_HOME", r"C:\AGOA")
SETTINGS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AGOA", "data")
SETTINGS = os.path.join(SETTINGS_DIR, "settings.json")


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(d):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def open_path(p):
    if sys.platform.startswith("win"):
        os.startfile(p)                       # noqa: S606 (chemin issu de nos propres livrables)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


class Button(tk.Label):
    """Bouton plat aux couleurs AGOA (comme dans le launcher)."""

    def __init__(self, master, text, command, primary=False, **kw):
        fg, bg = ("#FFFFFF", INK) if primary else (INK, BG)
        super().__init__(master, text=text, fg=fg, bg=bg, font=(FONT, 9, "bold"), padx=14, pady=6, cursor="hand2",
                         highlightthickness=1, highlightbackground=INK, **kw)
        self._cmd, self._primary, self._enabled = command, primary, True
        self.bind("<Button-1>", lambda e: self._enabled and self._cmd())
        self.bind("<Enter>", lambda e: self._enabled and self.config(bg="#333333" if primary else "#E8E6DF"))
        self.bind("<Leave>", lambda e: self.config(bg=INK if primary else BG))

    def enable(self, on=True):
        self._enabled = on
        self.config(fg=("#FFFFFF" if self._primary else INK) if on else MUTED,
                    bg=(INK if self._primary else BG) if on else "#E8E6DF",
                    cursor="hand2" if on else "arrow")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        try:
            ctypes = __import__("ctypes")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.title(f"{APP_NAME} — volumétrie LiDAR HD")
        self.configure(bg=BG)
        self.geometry("1100x760")
        self.minsize(900, 620)
        try:
            self.iconbitmap(resource("icon.ico"))
        except tk.TclError:
            pass
        self.settings = load_settings()
        self.q = queue.Queue()
        self.res = None
        self.images = []
        self.suggestions, self.choisie = [], None
        self._timer, self._seq, self._silence = None, 0, False
        self._build()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ interface
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="AGOA ", font=(FONT, 18, "bold"), bg=BG, fg=INK).pack(side="left")
        tk.Label(head, text="DATA", font=(FONT, 18, "bold"), bg=BG, fg=RED).pack(side="left")
        tk.Label(head, text=f"v{APP_VERSION}", font=(FONT, 8), bg=BG, fg=MUTED).pack(side="left", padx=(8, 0), pady=(8, 0))
        tk.Label(head, text="Volumétrie d'un bâtiment par nuage de points LiDAR HD (IGN)", font=(FONT, 9), bg=BG,
                 fg=MUTED).pack(side="right")

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=18, pady=(6, 2))
        tk.Label(form, text="ADRESSE", font=(FONT, 8, "bold"), bg=BG, fg=MUTED).grid(row=0, column=0, sticky="w")
        self.adr = tk.StringVar(value=self.settings.get("derniere_adresse", ""))
        e = tk.Entry(form, textvariable=self.adr, font=(FONT, 12), relief="solid", bd=1, bg=FIELD)
        e.grid(row=1, column=0, sticky="ew", ipady=5, padx=(0, 10))
        self.entry_adr = e
        e.bind("<Return>", self._entree)
        e.bind("<Down>", lambda ev: self._deplace(1))
        e.bind("<Up>", lambda ev: self._deplace(-1))
        e.bind("<Escape>", lambda ev: self._cache())
        e.bind("<FocusOut>", lambda ev: self.after(200, self._cache))
        e.focus_set()
        form.columnconfigure(0, weight=1)
        self.adr.trace_add("write", lambda *a: self._frappe())
        self.liste = tk.Listbox(self, font=(FONT, 11), bg="#FFFFFF", fg=INK, relief="solid", bd=1, activestyle="none",
                                selectbackground=RED, selectforeground="#FFFFFF", exportselection=False, height=1)
        self.liste.bind("<ButtonRelease-1>", lambda ev: self._choisit())

        sortie = tk.Frame(self, bg=BG)
        sortie.pack(fill="x", padx=18, pady=(8, 2))
        tk.Label(sortie, text="ENREGISTRER SOUS", font=(FONT, 8, "bold"), bg=BG, fg=MUTED).grid(row=0, column=0, sticky="w")
        self.out = tk.StringVar(value=self.settings.get("dossier_sortie") or os.path.join(os.path.expanduser("~"), "Documents", "AGOA DATA"))
        tk.Entry(sortie, textvariable=self.out, font=(FONT, 12), relief="solid", bd=1, bg=FIELD, state="readonly",
                 readonlybackground=FIELD, fg=INK).grid(row=1, column=0, sticky="ew", ipady=5, padx=(0, 10))
        Button(sortie, "PARCOURIR…", self.choisir_dossier, primary=True).grid(row=1, column=1)
        sortie.columnconfigure(0, weight=1)

        act = tk.Frame(self, bg=BG)
        act.pack(fill="x", padx=18, pady=(10, 2))
        tk.Label(act, text="QUE SOUHAITEZ-VOUS OBTENIR ?", font=(FONT, 8, "bold"), bg=BG, fg=MUTED).pack(anchor="w")
        barre = tk.Frame(act, bg=BG)
        barre.pack(fill="x", pady=(2, 0))
        self.actions = [
            Button(barre, "1 · CADASTRES  (PDF A3)", lambda: self.lancer("cadastre"), primary=True),
            Button(barre, "2 · VOLUMÉTRIE  (nuage de points)", lambda: self.lancer("volumetrie"), primary=True),
            Button(barre, "3 · PLANS DWG  (depuis PDF)…", lambda: self.lancer("dwg"), primary=True),
        ]
        for b in self.actions:
            b.pack(side="left", padx=(0, 8))

        res = tk.Frame(self, bg=BG)
        res.pack(fill="x", padx=18, pady=(8, 2))
        self.b_ouvrir = Button(res, "Ouvrir le dossier du résultat", self.ouvrir_dossier)
        self.b_ouvrir.pack(side="right")
        self.b_ouvrir.enable(False)
        self.b_pdf = Button(res, "Plans cadastraux (PDF) ▾", self.menu_pdf)
        self.b_pdf.pack(side="right", padx=6)
        self.b_pdf.enable(False)

        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=18, pady=4)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(2, 14))
        self.t_suivi = tk.Frame(self.nb, bg=BG)
        self.t_res = tk.Frame(self.nb, bg=BG)
        self.t_img = tk.Frame(self.nb, bg=BG)
        self.nb.add(self.t_suivi, text="  Suivi  ")
        self.nb.add(self.t_res, text="  Résultats  ")
        self.nb.add(self.t_img, text="  Coupes et plan  ")

        self.log = tk.Text(self.t_suivi, font=("Consolas", 10), bg=FIELD, relief="flat", wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, padx=2, pady=2)
        self._ecrire("Saisissez une adresse (Paris par défaut) : des suggestions apparaissent pendant la frappe. "
                     "Choisissez-en une (↑ ↓ Entrée ou clic), puis cliquez sur ce que vous souhaitez obtenir.\n"
                     "Volumétrie : 30 s à 1 min au premier calcul (le nuage de points est téléchargé par morceaux).")

        self.txt = tk.Text(self.t_res, font=("Consolas", 10), bg=FIELD, relief="flat", wrap="none", state="disabled")
        self.txt.pack(fill="both", expand=True, padx=2, pady=2)

        top = tk.Frame(self.t_img, bg=BG)
        top.pack(fill="x", pady=4)
        tk.Label(top, text="Afficher :", font=(FONT, 9), bg=BG, fg=MUTED).pack(side="left", padx=4)
        self.choix = ttk.Combobox(top, state="readonly", width=60)
        self.choix.pack(side="left")
        self.choix.bind("<<ComboboxSelected>>", lambda e: self._montre())
        self.canvas = tk.Canvas(self.t_img, bg="#FFFFFF", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ recherche interactive
    def _frappe(self):
        """Chaque frappe relance (après 300 ms de pause) la recherche de suggestions."""
        if self._silence:
            return
        self.choisie = None
        if self._timer:
            self.after_cancel(self._timer)
        t = self.adr.get().strip()
        if len(t) < 3:
            self._cache()
            return
        self._timer = self.after(300, lambda: self._cherche(t))

    def _cherche(self, t):
        self._seq += 1
        n = self._seq

        def tache():
            try:
                self.q.put(("sugg", (n, geo.suggerer(t))))
            except Exception:                            # noqa: BLE001 — pas de suggestions : la saisie reste utilisable
                pass
        threading.Thread(target=tache, daemon=True).start()

    def _montre_suggestions(self, n, liste):
        if n != self._seq or not liste or self.focus_get() is not self.entry_adr:
            if n == self._seq and not liste:
                self._cache()
            return
        self.suggestions = liste
        self.liste.delete(0, "end")
        for a in liste:
            self.liste.insert("end", "  " + a.label)
        self.liste.config(height=min(len(liste), 8))
        e = self.entry_adr
        self.liste.place(x=e.winfo_rootx() - self.winfo_rootx(), y=e.winfo_rooty() - self.winfo_rooty() + e.winfo_height(),
                         width=e.winfo_width())
        self.liste.lift()

    def _cache(self):
        self.liste.place_forget()
        self.suggestions = []

    def _deplace(self, d):
        if not self.suggestions:
            return None
        cur = self.liste.curselection()
        i = (cur[0] + d) if cur else (0 if d > 0 else len(self.suggestions) - 1)
        i = max(0, min(len(self.suggestions) - 1, i))
        self.liste.selection_clear(0, "end")
        self.liste.selection_set(i)
        self.liste.see(i)
        return "break"

    def _choisit(self):
        cur = self.liste.curselection()
        if not cur or not self.suggestions:
            return False
        a = self.suggestions[cur[0]]
        self._cache()
        self._silence = True
        try:
            self.adr.set(a.label)
        finally:
            self._silence = False
        self.choisie = a
        self.entry_adr.icursor("end")
        self.entry_adr.focus_set()
        return True

    def _entree(self, ev=None):
        """Entrée : valide la suggestion surlignée."""
        if self.suggestions and self.liste.curselection():
            self._choisit()
        else:
            self._cache()

    def choisir_dossier(self):
        d = filedialog.askdirectory(initialdir=self.out.get() if os.path.isdir(self.out.get()) else os.path.expanduser("~"))
        if d:
            self.out.set(d)
            self.settings["dossier_sortie"] = d
            save_settings(self.settings)

    def ouvrir_dossier(self):
        if self.res:
            open_path(self.res["dossier"])

    def menu_pdf(self):
        """Petit menu : un plan A3 par échelle."""
        if not (self.res and self.res.get("cadastre_pdfs")) or not self.b_pdf._enabled:
            return
        m = tk.Menu(self, tearoff=0, font=(FONT, 10))
        for n in self.res["cadastre_pdfs"]:
            ech = n.rsplit("_1-", 1)[-1].removesuffix(".pdf")
            m.add_command(label=f"Plan A3 au 1/{ech}", command=lambda n=n: open_path(os.path.join(self.res["dossier"], n)))
        m.tk_popup(self.b_pdf.winfo_rootx(), self.b_pdf.winfo_rooty() + self.b_pdf.winfo_height())

    # ------------------------------------------------------------------ calcul
    def _ecrire(self, s):
        self.log.config(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _occupe(self, on):
        for b in self.actions:
            b.enable(not on)

    def lancer(self, mode="volumetrie"):
        texte = self.adr.get().strip()
        if mode == "dwg":
            if not self.actions[2]._enabled:
                return
            fichiers = filedialog.askopenfilenames(title="Plans PDF à transformer en fond de plan DWG",
                                                   filetypes=[("Plans PDF", "*.pdf"), ("Tous les fichiers", "*.*")])
            if not fichiers:
                return
            if not texte and not messagebox.askyesno(
                    APP_NAME, "Aucune adresse saisie : les plans ne seront pas recalés sur le cadastre.\n\nContinuer quand même ?"):
                return
            ech = simpledialog.askinteger(APP_NAME, "Échelle des plans (50 pour 1/50, 100 pour 1/100…).\n"
                                          "Laissez vide pour la lire dans le PDF (obligatoire pour un plan scanné).",
                                          parent=self, minvalue=10, maxvalue=1000)
            arguments = (list(fichiers), texte, ech)
        else:
            if not texte:
                messagebox.showinfo(APP_NAME, "Saisissez une adresse.")
                return
            if not self.actions[0]._enabled:
                return
            arguments = (texte,)
        self._occupe(True)
        self.b_ouvrir.enable(False)
        self.b_pdf.enable(False)
        self.settings.update(derniere_adresse=texte, dossier_sortie=self.out.get())
        save_settings(self.settings)
        self.nb.select(self.t_suivi)
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        out = self.out.get()
        if mode != "dwg":
            cible = self.choisie if (self.choisie and self.choisie.label == texte) else texte
            arguments = (cible,)
        threading.Thread(target=self._travail, args=(mode, arguments, out), daemon=True).start()

    def _travail(self, mode, args, out):
        say = lambda s: self.q.put(("log", s))
        try:
            if mode == "cadastre":
                res = pipeline.cadastres(args[0], out, progress=say)
            elif mode == "dwg":
                res = pipeline.plans_dwg(args[0], args[1], out, echelle=args[2], progress=say)
            else:
                res = pipeline.analyser(args[0], out, progress=say, cache=os.path.join(out, "_cache"))
            self.q.put(("ok", res))
        except DataError as e:
            self.q.put(("err", str(e)))
        except Exception as e:                           # noqa: BLE001 — on affiche tout à l'utilisateur
            self.q.put(("err", f"Erreur inattendue : {e}\n\n{traceback.format_exc()}"))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    # les messages de progression de téléchargement remplacent la ligne précédente
                    if val.startswith("Nuage de points") and getattr(self, "_dl", False):
                        self.log.config(state="normal")
                        self.log.delete("end-2l", "end-1l")
                        self.log.config(state="disabled")
                    self._dl = val.startswith("Nuage de points")
                    self._ecrire(val)
                elif kind == "sugg":
                    self._montre_suggestions(*val)
                elif kind == "ok":
                    self._dl = False
                    self._fin(val)
                else:
                    self._dl = False
                    self._ecrire("\nÉCHEC : " + val)
                    self._occupe(False)
                    messagebox.showerror(APP_NAME, val.split("\n\n")[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _fin(self, res):
        from agoa_data import export
        self.res = res
        mode = res.get("mode", "volumetrie")
        self._occupe(False)
        self.b_ouvrir.enable(True)
        self.b_pdf.enable(bool(res.get("cadastre_pdfs")))
        d = res["dossier"]
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.fichiers = {}
        if mode == "volumetrie":
            self.txt.insert("end", export.synthese_texte(res))
            self.fichiers["Plan de situation des coupes"] = os.path.join(d, "plan_coupes.png")
            for c in res["coupes"]:
                t = "perpendiculaire à la" if c["type"] == "perpendiculaire" else "parallèle à la"
                self.fichiers[f"Coupe {t} {c['voie']}"] = os.path.join(d, c["fichier"])
        elif mode == "cadastre":
            self.txt.insert("end", f"Plans cadastraux — {res['adresse']['label']}\nParcelle {res['parcelle']['section']} "
                                   f"{res['parcelle']['numero']} (IDU {res['parcelle']['idu']})\n\n" + "\n".join(res["cadastre_pdfs"]) +
                                   f"\n\nDossier : {d}\nPDF vectoriels A3, Lambert 93 (1 mm sur la feuille = échelle / 1000 m).")
        else:
            L = [f"Fond de plan — {res['adresse']['label']}", "",
                 f"DWG : {res['dwg']}" if res.get("dwg") else f"DXF : {res['dxf']}   (DWG non produit : ODA File Converter absent)", ""]
            for p in res["plans"]:
                L.append(f"{p['plan']} : échelle 1/{p['echelle']}, {'scanné' if p['scanne'] else 'vectoriel'}, rotation {p['rotation_deg']:.1f}°, "
                         f"dimensions ×{p['facteur_dimensions']:.3f}, recouvrement cadastre {100 * p['recouvrement']:.0f} %")
                L += ["   ⚠ " + w for w in p["avertissements"]]
            L += ["", f"Dossier : {d}", "Calques : CADASTRE_* et <PLAN>_MURS / _TRAITS / _TEXTES_COTES / _TEXTES. Coordonnées en Lambert 93 (mètres)."]
            self.txt.insert("end", "\n".join(L))
            self.fichiers["Aperçu du recalage sur le cadastre"] = os.path.join(d, res["apercu"])
        self.txt.config(state="disabled")
        self.choix["values"] = list(self.fichiers)
        if self.fichiers:
            self.choix.current(0)
        self.nb.select(self.t_res)
        self._montre()

    def _montre(self):
        p = self.fichiers.get(self.choix.get())
        if not p or not os.path.exists(p):
            return
        img = tk.PhotoImage(file=p)
        w = max(self.canvas.winfo_width(), 600)
        k = max(1, -(-img.width() // w))               # sous-échantillonnage entier pour tenir en largeur
        if k > 1:
            img = img.subsample(k, k)
        self.images = [img]
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=img, anchor="nw")


def selftest():
    """Vérifie que toutes les briques (y compris celles chargées dynamiquement) sont présentes dans le programme compilé.
    Appelé par la CI : `AGOA DATA.exe --selftest` doit se terminer avec le code 0."""
    import io
    import tempfile
    log = []
    ok = True
    for nom, fn in (("pyproj", _t_pyproj), ("shapely", _t_shapely), ("laspy+lazrs", _t_laspy),
                    ("matplotlib", _t_mpl), ("plans PDF (pdfplumber, opencv, ezdxf)", _t_plans), ("tkinter", _t_tk),
                    ("geo/pipeline", _t_modules)):
        try:
            fn()
            log.append(f"OK   {nom}")
        except Exception as e:                           # noqa: BLE001
            ok = False
            log.append(f"ÉCHEC {nom} : {e!r}")
    texte = "\n".join(log)
    try:
        with open(os.path.join(tempfile.gettempdir(), "agoa_data_selftest.txt"), "w", encoding="utf-8") as f:
            f.write(texte)
    except OSError:
        pass
    print(texte)
    return 0 if ok else 1


def _t_pyproj():
    from agoa_data import geo
    x, y = geo._TO_L93(2.331761, 48.894091)
    assert abs(x - 651004.7) < 1 and abs(y - 6866216.5) < 1, (x, y)


def _t_shapely():
    from shapely.geometry import box
    from shapely import contains_xy
    import numpy as np
    assert contains_xy(box(0, 0, 1, 1), np.array([0.5]), np.array([0.5]))[0]


def _t_laspy():
    import tempfile
    import laspy
    import numpy as np
    h = laspy.LasHeader(point_format=6, version="1.4")
    las = laspy.LasData(h)
    las.x, las.y, las.z = np.arange(5.0), np.arange(5.0), np.arange(5.0)
    p = os.path.join(tempfile.mkdtemp(), "t.laz")
    las.write(p)                                         # exige le moteur de compression LAZ (lazrs)
    assert len(laspy.read(p).points) == 5
    import laspy.copc  # noqa: F401


def _t_mpl():
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    b = io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    assert b.tell() > 500


def _t_tk():
    import tkinter  # noqa: F401


def _t_plans():
    """Chaîne PDF → DXF sur un petit plan fabriqué : lecture vectorielle, rendu scanné (pdfium + OpenCV), écriture DXF."""
    import tempfile
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    from agoa_data import dwg, plans
    d = tempfile.mkdtemp()
    fig = plt.figure(figsize=(8.27, 5.83))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 297)
    ax.set_ylim(0, 210)
    ax.plot([60, 220, 220, 60, 60], [50, 50, 150, 150, 50], color="k", lw=2.5)
    for k in range(1, 6):
        ax.plot([60 + 26 * k, 60 + 26 * k], [50, 150], color="k", lw=0.4)
    ax.text(20, 15, "Echelle 1/100")
    v = os.path.join(d, "v.pdf")
    fig.savefig(v)
    fig.savefig(os.path.join(d, "s.png"), dpi=150)
    plt.close(fig)
    Image.open(os.path.join(d, "s.png")).convert("L").save(os.path.join(d, "s.pdf"), resolution=150)
    pv = plans.lire_pdf(v)
    assert not pv.scanne and pv.echelle == 100, (pv.scanne, pv.echelle)
    ps = plans.lire_pdf(os.path.join(d, "s.pdf"))
    assert ps.scanne and len(ps.traces) > 3
    cal = plans.classer(pv, 100)
    assert cal["MURS"] and cal["TRAITS"]
    dwg.ecrire_dxf(os.path.join(d, "t.dxf"), [("TEST", cal, 0.0)])
    assert os.path.getsize(os.path.join(d, "t.dxf")) > 1000


def _t_modules():
    from agoa_data import cadastre, coupes, dwg, export, geo, httprange, lidar, pipeline, plans, volumetrie  # noqa: F401


def main():
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    App().mainloop()


if __name__ == "__main__":
    main()
