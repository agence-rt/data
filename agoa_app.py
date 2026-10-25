"""AGOA DATA — volumétrie d'un bâtiment à partir du nuage de points LiDAR HD de l'IGN.

Application Windows de la gamme AGOA (Python 3.10+, tkinter). Saisir une adresse : l'application
trouve la parcelle, les bâtiments et les voies, télécharge le morceau utile du nuage de points,
calcule la volumétrie et trace les coupes (une perpendiculaire à chaque voie), puis range les
livrables dans un dossier (nuage .laz, modèle de toiture .asc, coupes .png, plan cadastral .pdf, synthèse .txt).
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

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
        tk.Label(form, text="DOSSIER", font=(FONT, 8, "bold"), bg=BG, fg=MUTED).grid(row=0, column=1, sticky="w")
        self.nom = tk.StringVar()
        en = tk.Entry(form, textvariable=self.nom, font=(FONT, 11), width=18, relief="solid", bd=1, bg=FIELD)
        en.grid(row=1, column=1, ipady=6, padx=(0, 10))
        en.bind("<Key>", lambda ev: setattr(self, "_nom_modifie", True))     # nom saisi à la main : on n'y touche plus
        self.btn = Button(form, "ANALYSER", self.lancer, primary=True)
        self.btn.grid(row=1, column=2)
        form.columnconfigure(0, weight=1)
        self.adr.trace_add("write", lambda *a: self._suggere_nom())
        self.adr.trace_add("write", lambda *a: self._frappe())
        self.liste = tk.Listbox(self, font=(FONT, 11), bg="#FFFFFF", fg=INK, relief="solid", bd=1, activestyle="none",
                                selectbackground=RED, selectforeground="#FFFFFF", exportselection=False, height=1)
        self.liste.bind("<ButtonRelease-1>", lambda ev: self._choisit())

        sortie = tk.Frame(self, bg=BG)
        sortie.pack(fill="x", padx=18, pady=(2, 6))
        tk.Label(sortie, text="Enregistrer dans :", font=(FONT, 9), bg=BG, fg=MUTED).pack(side="left")
        self.out = tk.StringVar(value=self.settings.get("dossier_sortie") or os.path.join(os.path.expanduser("~"), "Documents", "AGOA DATA"))
        tk.Label(sortie, textvariable=self.out, font=(FONT, 9), bg=BG, fg=INK).pack(side="left", padx=6)
        Button(sortie, "Changer…", self.choisir_dossier).pack(side="left", padx=4)
        self.b_ouvrir = Button(sortie, "Ouvrir le dossier du résultat", self.ouvrir_dossier)
        self.b_ouvrir.pack(side="right")
        self.b_ouvrir.enable(False)
        self.b_pdf = Button(sortie, "Ouvrir le plan cadastral (PDF)", self.ouvrir_pdf)
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
                     "Choisissez-en une (↑ ↓ Entrée ou clic), puis ANALYSER.\n"
                     "Premier calcul : 30 s à 1 min (le nuage de points est téléchargé par morceaux).")

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
        """Entrée : valide la suggestion surlignée ; sinon lance l'analyse."""
        if self.suggestions and self.liste.curselection():
            self._choisit()
        else:
            self._cache()
            self.lancer()

    def _suggere_nom(self):
        if getattr(self, "_nom_modifie", False):
            return
        t = self.adr.get().strip()
        if t:
            try:
                self.nom.set(pipeline.nom_dossier(t if any(ch.isdigit() for ch in t[-6:]) else t + " 75000"))
            except Exception:
                pass

    def choisir_dossier(self):
        d = filedialog.askdirectory(initialdir=self.out.get() if os.path.isdir(self.out.get()) else os.path.expanduser("~"))
        if d:
            self.out.set(d)
            self.settings["dossier_sortie"] = d
            save_settings(self.settings)

    def ouvrir_dossier(self):
        if self.res:
            open_path(self.res["dossier"])

    def ouvrir_pdf(self):
        if self.res and self.res.get("cadastre_pdf"):
            open_path(os.path.join(self.res["dossier"], self.res["cadastre_pdf"]))

    # ------------------------------------------------------------------ calcul
    def _ecrire(self, s):
        self.log.config(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def lancer(self):
        texte = self.adr.get().strip()
        if not texte:
            messagebox.showinfo(APP_NAME, "Saisissez une adresse.")
            return
        if not self.btn._enabled:
            return
        self.btn.enable(False)
        self.b_ouvrir.enable(False)
        self.b_pdf.enable(False)
        self.settings.update(derniere_adresse=texte, dossier_sortie=self.out.get())
        save_settings(self.settings)
        self.nb.select(self.t_suivi)
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        nom = (self.nom.get().strip() or None)
        out = self.out.get()
        cible = self.choisie if (self.choisie and self.choisie.label == texte) else texte
        threading.Thread(target=self._travail, args=(cible, out, nom), daemon=True).start()

    def _travail(self, texte, out, nom):
        try:
            res = pipeline.analyser(texte, out, progress=lambda s: self.q.put(("log", s)),
                                    cache=os.path.join(out, "_cache"), nom=nom)
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
                    self.btn.enable(True)
                    messagebox.showerror(APP_NAME, val.split("\n\n")[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _fin(self, res):
        from agoa_data import export
        self.res = res
        self.btn.enable(True)
        self.b_ouvrir.enable(True)
        self.b_pdf.enable(bool(res.get("cadastre_pdf")))
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("end", export.synthese_texte(res))
        self.txt.config(state="disabled")
        d = res["dossier"]
        self.fichiers = {"Plan de situation des coupes": os.path.join(d, "plan_coupes.png")}
        for c in res["coupes"]:
            t = "perpendiculaire à la" if c["type"] == "perpendiculaire" else "parallèle à la"
            self.fichiers[f"Coupe {t} {c['voie']}"] = os.path.join(d, c["fichier"])
        self.choix["values"] = list(self.fichiers)
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
                    ("matplotlib", _t_mpl), ("tkinter", _t_tk), ("geo/pipeline", _t_modules)):
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


def _t_modules():
    from agoa_data import coupes, export, geo, httprange, lidar, pipeline, volumetrie  # noqa: F401


def main():
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    App().mainloop()


if __name__ == "__main__":
    main()
