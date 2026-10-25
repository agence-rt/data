"""Fabrique icon.ico : carré arrondi rouge agence, code de 2-3 lettres en gras (charte des icônes AGOA).

    python tools/make_icon.py [CODE]      # CODE = DA par défaut
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

RED = (179, 38, 30, 255)            # #B3261E, rouge agence
CODE = (sys.argv[1] if len(sys.argv) > 1 else "DA").upper()
POLICES = [r"C:\Windows\Fonts\arialbd.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
S = 1024                            # dessin en grand puis réduction (lissage)


def font(taille):
    for p in POLICES:
        if os.path.exists(p):
            return ImageFont.truetype(p, taille)
    return ImageFont.load_default()


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.215), fill=RED)
taille = int(S * (0.46 if len(CODE) <= 2 else 0.37))
f = font(taille)
box = d.textbbox((0, 0), CODE, font=f)
w, h = box[2] - box[0], box[3] - box[1]
d.text(((S - w) / 2 - box[0], (S - h) / 2 - box[1]), CODE, font=f, fill=(255, 255, 255, 255))
sortie = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "icon.ico")
img.resize((256, 256), Image.LANCZOS).save(sortie, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico écrit :", os.path.abspath(sortie))
