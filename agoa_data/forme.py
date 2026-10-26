"""Forme générale d'une façade sur rue : RDC à part, niveaux courants (planchers, linteaux), travées numérotées, terrasse et attiques en retrait.
Hauteurs en m au-dessus du sol de la façade ; abscisses en m depuis la limite gauche. Les tailles de baies viennent ensuite."""
import json
import os


def construire(f, diag):
    """`diag` : résultat de vue_unique.analyser_elevation (planchers_m, linteaux_m, travees_m, corniche…). Renvoie le dict de la forme générale."""
    pl = diag.get("planchers_m") or []
    li = diag.get("linteaux_m") or []
    ter = (getattr(f, "r", {}) or {}).get("terrasse") or {}
    if len(pl) < 2:
        return {}
    n = len(li)
    haut_rdc = pl[0]
    forme = {"voie": f.voie, "longueur": round(f.longueur, 2), "rdc": {"z0": 0.0, "z1": haut_rdc},
             "niveaux": [], "travees": [], "terrasse": None, "attiques": [], "recul": ter.get("recul")}
    for k in range(n):
        forme["niveaux"].append({"nom": f"R+{k + 1}", "plancher": pl[k], "linteau": li[k], "dessus_dalle": pl[k + 1]})
    T = pl[-1]
    forme["terrasse"] = {"dalle": T, "lidar": ter.get("terrasse_lidar"), "ecart_lidar": None if "terrasse_dalle" not in ter else round(T - ter["terrasse_dalle"], 2)}
    top = ter.get("attique_haut")
    if top and top > T + 2:
        na = max(1, int(round((top - T) / (pl[1] - pl[0])))) if len(pl) >= 2 else 2
        h = (top - T) / na
        for k in range(na):
            forme["attiques"].append({"nom": f"R+{n + 1 + k}", "plancher": round(T + k * h, 2), "haut": round(T + (k + 1) * h, 2)})
    for i, (a, b) in enumerate(diag.get("travees_m", []), 1):
        forme["travees"].append({"n": i, "x0": a, "x1": b, "largeur": round(b - a, 2)})
    return forme


def dessiner(forme, chemin):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    L = forme["longueur"]
    fig, ax = plt.subplots(figsize=(14, 8.5))
    ax.add_patch(Rectangle((0, 0), L, forme["rdc"]["z1"], fc="#d9d3cc", ec="#444", lw=1.2))
    ax.text(L / 2, forme["rdc"]["z1"] / 2, "RDC (étudié à part)", ha="center", va="center", fontsize=11, color="#555")
    for nv in forme["niveaux"]:
        ax.add_patch(Rectangle((0, nv["plancher"]), L, nv["dessus_dalle"] - nv["plancher"], fc="#f4efe7", ec="#444", lw=1.0))
        ax.axhline(nv["linteau"], color="#e0a000", lw=1.0, ls="--")
        ax.text(-0.3, (nv["plancher"] + nv["dessus_dalle"]) / 2, f"{nv['nom']}\nplancher {nv['plancher']:.2f}\nlinteau {nv['linteau']:.2f}",
                ha="right", va="center", fontsize=8)
        for t in forme["travees"]:                                     # cases provisoires : du plancher au linteau
            ax.add_patch(Rectangle((t["x0"], nv["plancher"] + 0.15), t["largeur"], nv["linteau"] - nv["plancher"] - 0.15, fc="#cfe3f3", ec="#1a5f9e", lw=0.8))
    for t in forme["travees"]:
        ax.text((t["x0"] + t["x1"]) / 2, forme["rdc"]["z1"] - 0.5, str(t["n"]), ha="center", va="top", fontsize=13, color="#b00020", weight="bold")
    T = forme["terrasse"]["dalle"]
    ax.plot([0, L], [T, T], color="#1a7f37", lw=2)
    ax.text(L + 0.3, T, f"terrasse (dessus de dalle) {T:.2f}", va="center", fontsize=9, color="#1a7f37")
    if forme["attiques"]:
        rc = forme.get("recul") or 4.5
        for a in forme["attiques"]:
            ax.add_patch(Rectangle((0, a["plancher"]), L, a["haut"] - a["plancher"], fc="none", ec="#777", lw=1.0, ls="--"))
            ax.text(L + 0.3, (a["plancher"] + a["haut"]) / 2, f"{a['nom']} en retrait ({rc:.1f} m) {a['plancher']:.2f} → {a['haut']:.2f}", va="center", fontsize=9, color="#555")
    ax.axhline(0, color="k", lw=1)
    ax.set_xlim(-6, L + 11)
    ax.set_ylim(-1, (forme["attiques"][-1]["haut"] if forme["attiques"] else T) + 1.5)
    ax.set_aspect("equal")
    ax.set_xlabel("abscisse le long de la façade (m)")
    ax.set_ylabel("hauteur au-dessus du sol (m)")
    ax.set_title(f"{forme['voie']} — forme générale (provisoire : cases = plancher → linteau ; tailles de baies à venir)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(chemin, dpi=110)
    plt.close(fig)


def ecrire_dxf(forme, chemin):
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.units = 6
    msp = doc.modelspace()
    for nom, c in (("SOL", 3), ("RDC", 8), ("PLANCHERS", 1), ("LINTEAUX", 2), ("TRAVEES", 5), ("TERRASSE", 3), ("ATTIQUES", 8), ("TEXTES", 7)):
        doc.layers.add(f"F_{nom}", color=c)
    L = forme["longueur"]
    msp.add_line((-1, 0), (L + 1, 0), dxfattribs={"layer": "F_SOL"})
    msp.add_lwpolyline([(0, 0), (L, 0), (L, forme["rdc"]["z1"]), (0, forme["rdc"]["z1"])], close=True, dxfattribs={"layer": "F_RDC"})
    for nv in forme["niveaux"]:
        msp.add_line((0, nv["plancher"]), (L, nv["plancher"]), dxfattribs={"layer": "F_PLANCHERS"})
        msp.add_line((0, nv["linteau"]), (L, nv["linteau"]), dxfattribs={"layer": "F_LINTEAUX", "linetype": "DASHED"})
        msp.add_text(f"{nv['nom']}  {nv['plancher']:.2f}", height=0.25, dxfattribs={"layer": "F_TEXTES", "insert": (-3.5, nv["plancher"] + 0.1)})
        for t in forme["travees"]:
            msp.add_lwpolyline([(t["x0"], nv["plancher"] + 0.15), (t["x1"], nv["plancher"] + 0.15), (t["x1"], nv["linteau"]), (t["x0"], nv["linteau"])],
                               close=True, dxfattribs={"layer": "F_TRAVEES"})
    for t in forme["travees"]:
        msp.add_text(str(t["n"]), height=0.4, dxfattribs={"layer": "F_TEXTES", "insert": ((t["x0"] + t["x1"]) / 2 - 0.1, forme["rdc"]["z1"] + 0.25)})
    T = forme["terrasse"]["dalle"]
    msp.add_line((0, T), (L, T), dxfattribs={"layer": "F_TERRASSE"})
    for a in forme["attiques"]:
        msp.add_lwpolyline([(0, a["plancher"]), (L, a["plancher"]), (L, a["haut"]), (0, a["haut"])], close=True, dxfattribs={"layer": "F_ATTIQUES", "linetype": "DASHED"})
        msp.add_text(f"{a['nom']} en retrait", height=0.25, dxfattribs={"layer": "F_TEXTES", "insert": (L + 0.3, a["plancher"] + 0.2)})
    doc.saveas(chemin)


def ecrire(f, diag, dossier):
    forme = construire(f, diag)
    if not forme:
        return None
    with open(os.path.join(dossier, "forme_generale.json"), "w", encoding="utf-8") as fh:
        json.dump(forme, fh, ensure_ascii=False, indent=1)
    dessiner(forme, os.path.join(dossier, "4_forme_generale.png"))
    ecrire_dxf(forme, os.path.join(dossier, "forme_generale.dxf"))
    return forme
