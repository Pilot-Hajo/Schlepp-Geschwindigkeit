# -*- coding: utf-8 -*-
"""Prueft die Folien ohne Renderer: Textkaesten mit den echten Schriften
nachmessen, Umbruch simulieren, Hoehe vergleichen. Dazu Randabstand und
Ueberlappungen."""
import json, sys
from PIL import ImageFont

DPI = 96.0
FONTS = {
    ("Calibri", False): r"C:\Windows\Fonts\calibri.ttf",
    ("Calibri", True):  r"C:\Windows\Fonts\calibrib.ttf",
    ("Cambria", False): r"C:\Windows\Fonts\cambria.ttc",
    ("Cambria", True):  r"C:\Windows\Fonts\cambriab.ttf",
    ("Courier New", False): r"C:\Windows\Fonts\cour.ttf",
    ("Courier New", True):  r"C:\Windows\Fonts\courbd.ttf",
}
cache = {}
def font(name, pt, fett=False):
    k = (name, pt, fett)
    if k not in cache:
        pfad = FONTS.get((name, fett)) or FONTS[(name, False)]
        cache[k] = ImageFont.truetype(pfad, max(1, int(round(pt * DPI / 72.0))))
    return cache[k]

def breite(f, s):
    return f.getlength(s)

def zeilen(f, s, px):
    """Greedy-Umbruch wie im Textrahmen."""
    if not s.strip():
        return 1
    n, zeile = 0, ""
    for wort in s.split(" "):
        probe = (zeile + " " + wort).strip()
        if breite(f, probe) <= px or not zeile:
            zeile = probe
        else:
            n += 1
            zeile = wort
    return n + 1

kaesten = json.load(open("deck/kaesten.json", encoding="utf-8"))
BREIT, HOCH = 10.0, 5.625
probleme = []

for k in kaesten:
    f = font(k["schrift"], k["groesse"], True)        # fett = breiter: Worst Case
    px = k["w"] * DPI
    zeilenhoehe = (k["zeile"] or k["groesse"] * 1.22) * DPI / 72.0
    n = sum(zeilen(f, a, px) for a in k["absaetze"])
    abstand = (len(k["absaetze"]) - 1) * 5 * DPI / 72.0
    hoehe_px = n * zeilenhoehe + abstand
    platz_px = k["h"] * DPI
    if hoehe_px > platz_px * 1.04:
        probleme.append("Folie %2d  UEBERLAUF  %.2f\" gebraucht, %.2f\" da  (%d Zeilen)  %r"
                        % (k["folie"], hoehe_px / DPI, k["h"], n, k["absaetze"][0][:52]))
    if k["x"] + k["w"] > BREIT - 0.3 or k["y"] + k["h"] > HOCH - 0.2 or k["x"] < 0.35:
        probleme.append("Folie %2d  RAND       x=%.2f y=%.2f w=%.2f h=%.2f  %r"
                        % (k["folie"], k["x"], k["y"], k["w"], k["h"], k["absaetze"][0][:40]))

# Ueberlappung von Textkaesten derselben Folie
nach_folie = {}
for k in kaesten:
    nach_folie.setdefault(k["folie"], []).append(k)
for nr, liste in sorted(nach_folie.items()):
    for i in range(len(liste)):
        for j in range(i + 1, len(liste)):
            a, b = liste[i], liste[j]
            dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if dx > 0.05 and dy > 0.05:
                probleme.append("Folie %2d  UEBERDECKT %.2fx%.2f\"  %r  <>  %r"
                                % (nr, dx, dy, a["absaetze"][0][:28], b["absaetze"][0][:28]))

print("%d Textkaesten geprueft" % len(kaesten))
if probleme:
    print("\n".join(probleme))
else:
    print("keine Beanstandung")
sys.exit(1 if probleme else 0)
