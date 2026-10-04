# -*- coding: utf-8 -*-
"""Bedienanleitung Schlepp-Geschwindigkeitsanzeige fuer Vereinsmitglieder."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

# Farben aus dem Geraet selbst: dunkles Display, Tachozonen gelb/gruen/rot
C = dict(
    dunkel=RGBColor(0x14, 0x1A, 0x1D),
    hell=RGBColor(0xF4, 0xF6, 0xF6),
    tinte=RGBColor(0x14, 0x1A, 0x1D),
    grau=RGBColor(0x5A, 0x6A, 0x70),
    linie=RGBColor(0xD7, 0xDE, 0xDE),
    gruen=RGBColor(0x1F, 0x8A, 0x3B),
    display=RGBColor(0x3C, 0xDC, 0x3C),
    rot=RGBColor(0xB3, 0x28, 0x1B),
    amber=RGBColor(0xB0, 0x7A, 0x00),
    weiss=RGBColor(0xFF, 0xFF, 0xFF),
    grauhell=RGBColor(0x9F, 0xB0, 0xB5),
)
KOPF, TEXT, MONO = "Cambria", "Calibri", "Courier New"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)
LEER = prs.slide_layouts[6]

# Protokoll fuer die spaetere Pruefung: jede Textbox mit Geometrie und Schrift
kaesten = []


def folie(bg=None):
    s = prs.slides.add_slide(LEER)
    f = s.background.fill
    f.solid()
    f.fore_color.rgb = bg or C["hell"]
    return s


def text(s, x, y, w, h, inhalt, groesse=14, schrift=TEXT, farbe=None,
         fett=False, kursiv=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         zeile=None, nummer=None):
    """inhalt: str oder Liste von (str, dict)-Stuecken bzw. Liste von Absaetzen."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    absaetze = inhalt if isinstance(inhalt, list) else [inhalt]
    for i, a in enumerate(absaetze):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if zeile:
            p.line_spacing = Pt(zeile)
        if i < len(absaetze) - 1:
            p.space_after = Pt(5)
        stuecke = a if isinstance(a, list) else [(a, {})]
        for t, opt in stuecke:
            r = p.add_run()
            r.text = t
            r.font.name = opt.get("schrift", schrift)
            r.font.size = Pt(opt.get("groesse", groesse))
            r.font.bold = opt.get("fett", fett)
            r.font.italic = opt.get("kursiv", kursiv)
            r.font.color.rgb = opt.get("farbe", farbe or C["tinte"])
    kaesten.append(dict(folie=nummer or len(prs.slides.__iter__.__self__._sldIdLst),
                        x=x, y=y, w=w, h=h, groesse=groesse, schrift=schrift,
                        absaetze=[a if isinstance(a, str) else
                                  "".join(t for t, _ in (a if isinstance(a, list) else [(a, {})]))
                                  for a in absaetze],
                        zeile=zeile))
    return tb


def kasten(s, x, y, w, h, fuell="EAEEEE", rund=True, rand=True):
    form = MSO_SHAPE.ROUNDED_RECTANGLE if rund else MSO_SHAPE.RECTANGLE
    sh = s.shapes.add_shape(form, Inches(x), Inches(y), Inches(w), Inches(h))
    if rund:
        sh.adjustments[0] = 0.08
    sh.fill.solid()
    sh.fill.fore_color.rgb = RGBColor.from_string(fuell) if isinstance(fuell, str) else fuell
    if rand:
        sh.line.color.rgb = C["linie"]
        sh.line.width = Pt(0.75)
    else:
        sh.line.fill.background()
    sh.shadow.inherit = False
    sh.text_frame.word_wrap = True
    return sh


def display(s, x, y, w, zeilen, h=1.75, beschriftung=None):
    """Nachbau des Empfaenger-Displays."""
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                            Inches(x), Inches(y), Inches(w), Inches(h))
    sh.adjustments[0] = 0.05
    sh.fill.solid()
    sh.fill.fore_color.rgb = C["dunkel"]
    sh.line.color.rgb = RGBColor(0x2A, 0x34, 0x38)
    sh.line.width = Pt(0.75)
    sh.shadow.inherit = False
    oben = y + 0.14
    for z in zeilen:
        gross = z.get("gross")
        text(s, x + 0.18, oben, w - 0.36, 0.5 if gross else 0.26, z["t"],
             groesse=z.get("groesse", 26 if gross else 12), schrift=MONO,
             farbe=z.get("farbe", C["display"]), fett=bool(gross),
             align=PP_ALIGN.RIGHT if z.get("rechts") else PP_ALIGN.LEFT,
             anchor=MSO_ANCHOR.MIDDLE)
        oben += 0.52 if gross else 0.26
    if beschriftung:
        text(s, x, y + h + 0.06, w, 0.25, beschriftung, groesse=10,
             kursiv=True, farbe=C["grau"])


def schritt(s, x, y, w, nr, kopf, inhalt, farbe=None):
    d = 0.42
    k = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    k.fill.solid()
    k.fill.fore_color.rgb = farbe or C["gruen"]
    k.line.fill.background()
    k.shadow.inherit = False
    text(s, x, y + 0.04, d, 0.34, str(nr), groesse=15, fett=True,
         farbe=C["weiss"], align=PP_ALIGN.CENTER)
    text(s, x + d + 0.18, y - 0.02, w - d - 0.18, 0.3, kopf, groesse=16, fett=True)
    text(s, x + d + 0.18, y + 0.3, w - d - 0.18, 0.8, inhalt, groesse=13,
         farbe=C["grau"], zeile=16)


def folientitel(s, nr, titel):
    text(s, 0.5, 0.35, 7.8, 0.72, titel, groesse=32, schrift=KOPF, fett=True)
    text(s, 8.8, 0.42, 0.7, 0.3, str(nr), groesse=12, farbe=C["grau"],
         align=PP_ALIGN.RIGHT)


def notiz(s, t):
    s.notes_slide.notes_text_frame.text = t


# ------------------------------------------------------------------ 1 Titel
s = folie(C["dunkel"])
text(s, 0.6, 1.25, 5.6, 0.95, "Schleppgeschwindigkeit", groesse=38, schrift=KOPF,
     fett=True, farbe=C["weiss"])
text(s, 0.6, 2.45, 5.6, 0.45, "Was die Anlage macht und wie sie bedient wird",
     groesse=17, farbe=C["grauhell"])
text(s, 0.6, 3.3, 5.9, 0.8,
     "Sender im Flugzeug · Anzeige am Startplatz · jeder Schlepp wird gespeichert",
     groesse=13, farbe=C["display"], zeile=18)
display(s, 6.6, 1.25, 2.9, [
    dict(t="BEREIT", gross=True, farbe=C["weiss"]),
    dict(t="zuletzt #12", farbe=C["grauhell"]),
    dict(t="108 km/h max, 50 s", farbe=C["grauhell"]),
], h=1.6)
text(s, 0.6, 4.8, 4.5, 0.3, "Stand Oktober 2026", groesse=11,
     farbe=RGBColor(0x6E, 0x80, 0x86))
notiz(s, "Ziel: jedes Mitglied kann die Anlage einschalten, die Anzeige lesen "
         "und die Daten holen. Keine Technik, nur Bedienung.")

# ------------------------------------------------------------------ 2 Wozu
s = folie()
folientitel(s, 2, "Wozu das Ganze")
text(s, 0.5, 1.25, 5.4, 1.3,
     "Im Flugzeugschlepp zählt die Geschwindigkeit. Zu langsam ist gefährlich, "
     "zu schnell belastet Flugzeug und Seil. Bisher wusste das nur der Pilot — "
     "am Boden konnte niemand mitlesen.", groesse=15, zeile=21)
text(s, 0.5, 2.7, 5.4, 1.3,
     "Die Anlage zeigt die Fahrt des geschleppten Flugzeugs am Startplatz und "
     "schreibt jeden Schlepp mit. Nach dem Flug lässt sich besprechen, was "
     "wirklich passiert ist.", groesse=15, farbe=C["grau"], zeile=21)
for i, (zahl, einheit, was) in enumerate([
        ("100", "km/h", "Soll für die ASK 21"),
        ("10", "/ Sekunde", "Messwerte im Schlepp"),
        ("50", "Sekunden", "Dauer je Aufzeichnung")]):
    y = 1.3 + i * 1.25
    kasten(s, 6.2, y, 3.3, 1.05)
    text(s, 6.45, y + 0.1, 2.9, 0.5,
         [[(zahl, dict(groesse=30, fett=True, farbe=C["gruen"])),
           (" " + einheit, dict(groesse=13, farbe=C["grau"]))]],
         anchor=MSO_ANCHOR.MIDDLE)
    text(s, 6.45, y + 0.64, 2.9, 0.3, was, groesse=12, farbe=C["grau"])
notiz(s, "Die 100 km/h sind im Gerät je Flugzeugtyp hinterlegt, weitere Typen "
         "lassen sich ergänzen.")

# ------------------------------------------------------------------ 3 Geraete
s = folie()
folientitel(s, 3, "Zwei Geräte")
for x, kopf, wo, punkte in [
    (0.5, "Sender", "im Flugzeug",
     ["misst die Fahrt am Staurohr",
      "funkt sie zehnmal je Sekunde zum Startplatz",
      "meldet, welches Flugzeug er ist",
      "schaltet sich nach dem Schlepp selbst ab"]),
    (5.15, "Empfänger", "am Startplatz",
     ["zeigt die Fahrt groß auf dem Display",
      "speichert jeden Schlepp auf der Karte",
      "spannt ein WLAN auf, fürs Handy",
      "braucht nur Strom, sonst nichts"]),
]:
    kasten(s, x, 1.25, 4.35, 3.3, "FFFFFF")
    text(s, x + 0.25, 1.42, 3.85, 0.4, kopf, groesse=21, schrift=KOPF, fett=True)
    text(s, x + 0.25, 1.85, 3.85, 0.3, wo, groesse=13, kursiv=True, farbe=C["gruen"])
    text(s, x + 0.25, 2.28, 3.85, 2.0,
         ["•  " + p for p in punkte], groesse=14, zeile=17)
text(s, 0.5, 4.75, 9, 0.35,
     "Beide Geräte arbeiten für sich. Es gibt nichts zu koppeln und nichts einzustellen.",
     groesse=13, kursiv=True, farbe=C["grau"])

# ------------------------------------------------------------------ 4 Reihenfolge
s = folie()
folientitel(s, 4, "Vor dem Start: die Reihenfolge")
schritt(s, 0.5, 1.3, 4.3, 1, "Empfänger einschalten",
        "Am Startplatz. Nach wenigen Sekunden steht die Startanzeige mit der "
        "Adresse fürs Handy da.")
schritt(s, 0.5, 2.65, 4.3, 2, "Sender einschalten",
        "Im Flugzeug. Er kalibriert kurz — dabei das Flugzeug nicht bewegen.")
schritt(s, 0.5, 4.0, 4.3, 3, "Fertig",
        "Nach etwa einer Sekunde kennt der Empfänger das Flugzeug.")
kasten(s, 5.2, 1.3, 4.3, 2.15, "FBE9E7")
text(s, 5.45, 1.45, 3.8, 0.3, "Wichtig", groesse=14, fett=True, farbe=C["rot"])
text(s, 5.45, 1.8, 3.8, 1.5,
     ["Der Sender schaltet sich nach 3 Minuten ohne Bewegung selbst ab. Er soll "
      "den Funk nicht belegen und die Batterie nicht leeren.",
      "Deshalb erst kurz vor dem Start einschalten. Ist er eingeschlafen: einmal "
      "aus und wieder ein."], groesse=12.5, zeile=17)
kasten(s, 5.2, 3.65, 4.3, 0.9, "EAEEEE")
text(s, 5.45, 3.8, 3.8, 0.65,
     "Umgekehrte Reihenfolge ist kein Beinbruch: Kommt der Empfänger innerhalb "
     "der 3 Minuten dazu, findet er den Sender von selbst.", groesse=12,
     farbe=C["grau"], zeile=16)
notiz(s, "Schalterposition im Flugzeug hier ergänzen, sobald der Einbau "
         "endgültig ist.")

# ------------------------------------------------------------------ 5 BEREIT
s = folie()
folientitel(s, 5, "Die Anzeige im Ruhezustand")
display(s, 0.5, 1.35, 4.3, [
    dict(t="BEREIT", gross=True, farbe=C["weiss"]),
    dict(t="zuletzt #12", farbe=C["grauhell"]),
    dict(t="108 km/h max, 50 s", farbe=C["grauhell"]),
    dict(t="naechster #13    W1 SD", farbe=C["grauhell"]),
], h=2.2, beschriftung="So sieht das Display aus, wenn kein Sender funkt.")
for i, (mark, erkl) in enumerate([
    ("BEREIT", "Kein Sender zu hören, die Anlage wartet auf den nächsten "
               "Schlepp. Es ist nichts kaputt."),
    ("zuletzt #12", "Der letzte gespeicherte Schlepp mit Höchstwert und Dauer. "
                    "So sieht man, dass er angekommen ist."),
    ("naechster #13", "Die Nummer für den nächsten Schlepp. Sie zählt "
                      "immer weiter."),
    ("W1 SD", "Ein Handy verbunden, Karte steckt. Steht dort „--“, fehlt "
              "die Karte."),
]):
    y = 1.35 + i * 0.85
    text(s, 5.05, y, 2.0, 0.28, mark, groesse=12, schrift=MONO, fett=True,
         farbe=C["gruen"])
    text(s, 5.05, y + 0.26, 4.45, 0.58, erkl, groesse=12, zeile=16)

# ------------------------------------------------------------------ 6 Im Schlepp
s = folie()
folientitel(s, 6, "Während des Schlepps")
sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.5), Inches(1.35),
                        Inches(4.3), Inches(2.2))
sh.adjustments[0] = 0.05
sh.fill.solid(); sh.fill.fore_color.rgb = C["dunkel"]
sh.line.color.rgb = RGBColor(0x2A, 0x34, 0x38); sh.line.width = Pt(0.75)
sh.shadow.inherit = False
text(s, 0.75, 1.75, 2.3, 1.2, "108", groesse=54, schrift=MONO, fett=True,
     farbe=C["weiss"], anchor=MSO_ANCHOR.MIDDLE)
text(s, 3.0, 1.5, 1.6, 0.28, "ASK21", groesse=12, schrift=MONO,
     farbe=C["grauhell"], align=PP_ALIGN.RIGHT)
text(s, 2.8, 3.08, 1.8, 0.28, "-74 W1 SD", groesse=12, schrift=MONO,
     farbe=C["grauhell"], align=PP_ALIGN.RIGHT)
text(s, 0.5, 3.65, 4.3, 0.35,
     "Die große Zahl ist immer ein echter Messwert.", groesse=11,
     kursiv=True, farbe=C["grau"])
for i, (kopf, erkl) in enumerate([
    ("Aufgezeichnet wird ab 20 km/h",
     "Vorher passiert nichts — Rangieren erzeugt keine Schlepps."),
    ("Der Flugzeugtyp steht oben rechts",
     "Ab 100 km/h weicht er den großen Ziffern, die brauchen dann die ganze Breite."),
    ("Am Handy zeigt ein weißes Dreieck die Sollfahrt",
     "Bei der ASK 21 auf 100 km/h, nur solange der Schlepp läuft."),
    ("Zu Ende ist der Schlepp",
     "drei Sekunden nachdem das Flugzeug unter 15 km/h ist oder der Sender schweigt."),
]):
    y = 1.35 + i * 0.88
    text(s, 5.05, y, 4.45, 0.3, kopf, groesse=14, fett=True)
    text(s, 5.05, y + 0.28, 4.45, 0.55, erkl, groesse=12, farbe=C["grau"], zeile=16)

# ------------------------------------------------------------------ 7 Danach
s = folie()
folientitel(s, 7, "Nach dem Schlepp")
text(s, 0.5, 1.2, 9, 0.6,
     "Der Sender legt sich 50 Sekunden nach dem Losrollen selbst schlafen. Der "
     "Empfänger schreibt den Schlepp dann auf die Karte und sagt, ob es "
     "geklappt hat.", groesse=15, zeile=21)
display(s, 0.5, 2.0, 4.3, [
    dict(t="BEREIT", gross=True, farbe=C["weiss"]),
    dict(t="zuletzt #12", farbe=C["grauhell"]),
    dict(t="108 km/h max, 50 s", farbe=C["grauhell"]),
    dict(t="#12 gespeichert", farbe=C["display"], rechts=True),
], h=2.2, beschriftung="Zehn Sekunden lang, danach wieder der Ruhezustand.")
kasten(s, 5.1, 2.0, 4.4, 1.0, "E8F3EA")
text(s, 5.35, 2.13, 3.9, 0.3, "Gespeichert", groesse=14, fett=True, farbe=C["gruen"])
text(s, 5.35, 2.45, 3.9, 0.45,
     "Dann ist der Schlepp vollständig auf der Karte.", groesse=12, zeile=16)
kasten(s, 5.1, 3.15, 4.4, 1.05, "FBE9E7")
text(s, 5.35, 3.28, 3.9, 0.3, "Nicht gespeichert", groesse=14, fett=True,
     farbe=C["rot"])
text(s, 5.35, 3.6, 3.9, 0.5,
     "Steht dort „keine SD-Karte“, ist der Schlepp verloren. Nachtragen "
     "geht nicht.", groesse=12, zeile=16)
text(s, 0.5, 4.75, 9, 0.35,
     "Der nächste Start braucht einen neu eingeschalteten Sender.", groesse=13,
     kursiv=True, farbe=C["grau"])

# ------------------------------------------------------------------ 8 Handy
s = folie()
folientitel(s, 8, "Die Anzeige aufs Handy holen")
schritt(s, 0.5, 1.3, 4.4, 1, "WLAN verbinden",
        "Netz „Schlepp“, Passwort schlepp123. Die Meldung „Keine "
        "Internetverbindung“ ist normal.")
schritt(s, 0.5, 2.7, 4.4, 2, "Adresse aufrufen",
        "Im Browser 192.168.4.1 eingeben. Der Tacho erscheint sofort.")
schritt(s, 0.5, 3.9, 4.4, 3, "Zum Home-Bildschirm",
        "Einmal ablegen, dann startet die Anzeige künftig wie eine App.")
kasten(s, 5.3, 1.3, 4.2, 3.25, "FFFFFF")
text(s, 5.55, 1.45, 3.7, 0.35, "Was das Handy zeigt", groesse=18, schrift=KOPF,
     fett=True)
text(s, 5.55, 1.92, 3.7, 1.5,
     ["•  Tacho mit gelber, grüner und roter Zone",
      "•  weißes Dreieck für die Sollfahrt",
      "•  Liste der gespeicherten Schlepps",
      "•  beide Dateien zum Herunterladen"], groesse=13.5, zeile=17)
text(s, 5.55, 3.55, 3.7, 0.85,
     "Das Display am Empfänger bleibt die Hauptanzeige. Das Handy ist die "
     "bequeme Zweitanzeige — und in der Sonne die besser lesbare.",
     groesse=12, kursiv=True, farbe=C["grau"], zeile=16)
notiz(s, "Über dieses WLAN gibt es kein Internet. Das iPhone nutzt dafür "
         "weiter Mobilfunk.")

# ------------------------------------------------------------------ 9 Daten
s = folie()
folientitel(s, 9, "Wo die Schlepps landen")
text(s, 0.5, 1.2, 9, 0.4,
     "Auf der Karte im Empfänger entstehen zwei Dateien. Beide lassen sich in "
     "Excel öffnen.", groesse=15)
for i, (name, was, bsp) in enumerate([
    ("schlepps.csv",
     "Eine Zeile je Schlepp: Nummer, Flugzeug, Dauer, Höchst- und Mittelwert. "
     "Die Datei für die Nachbesprechung.",
     "12;00:41:07;671C;ASK21;50;108;97"),
    ("verlauf.csv",
     "Der ganze Verlauf, zehn Werte je Sekunde. Wenn man genau hinsehen will.",
     "12;21.4;671C;ASK21;103.8;-74"),
]):
    y = 1.75 + i * 1.5
    kasten(s, 0.5, y, 9, 1.3, "FFFFFF")
    text(s, 0.75, y + 0.15, 2.7, 0.3, name, groesse=15, schrift=MONO, fett=True,
         farbe=C["gruen"])
    text(s, 0.75, y + 0.5, 5.0, 0.7, was, groesse=12, zeile=16)
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.0), Inches(y + 0.3),
                            Inches(3.25), Inches(0.6))
    sh.adjustments[0] = 0.08
    sh.fill.solid(); sh.fill.fore_color.rgb = C["dunkel"]
    sh.line.color.rgb = RGBColor(0x2A, 0x34, 0x38); sh.line.width = Pt(0.75)
    sh.shadow.inherit = False
    text(s, 6.12, y + 0.3, 3.0, 0.6, bsp, groesse=9, schrift=MONO,
         farbe=C["display"], anchor=MSO_ANCHOR.MIDDLE)
text(s, 0.5, 4.8, 9, 0.5,
     "Am einfachsten über das Handy: auf der Seite unten auf die Datei tippen, "
     "sie landet in „Dateien“. Die Karte muss dafür nicht heraus.",
     groesse=13, farbe=C["grau"], zeile=17)

# ------------------------------------------------------------------ 10 Karte
s = folie()
folientitel(s, 10, "Die Speicherkarte")
for i, (kopf, was, fuell, farbe) in enumerate([
    ("Sie bleibt drin",
     "Zum Auslesen muss sie nicht heraus, das geht über das Handy.",
     "E8F3EA", C["gruen"]),
    ("Einstecken startet neu",
     "Der Empfänger startet dabei neu. Nie während eines Schlepps einstecken.",
     "FFF4E0", C["amber"]),
    ("Ziehen erst nach „gespeichert“",
     "Sonst kann die letzte Aufzeichnung unvollständig bleiben.",
     "FFF4E0", C["amber"]),
    ("FAT32, höchstens 32 GB",
     "Andere Formate liest der Empfänger nicht. 1 GB reicht für Jahre.",
     "EAEEEE", C["grau"]),
]):
    x = 0.5 + (i % 2) * 4.65
    y = 1.3 + (i // 2) * 1.7
    kasten(s, x, y, 4.35, 1.45, fuell)
    text(s, x + 0.25, y + 0.18, 3.85, 0.35, kopf, groesse=14.5, fett=True, farbe=farbe)
    text(s, x + 0.25, y + 0.58, 3.85, 0.75, was, groesse=12.5, zeile=17)
text(s, 0.5, 4.8, 9, 0.4,
     "Die Karte ist Verbrauchsmaterial. Geht eine kaputt, kostet die neue ein paar "
     "Euro — die Schlepps darauf sind dann allerdings weg.", groesse=12,
     kursiv=True, farbe=C["grau"])

# ------------------------------------------------------------------ 11 Stoerungen
s = folie()
folientitel(s, 11, "Wenn etwas nicht geht")
spalten = [2.6, 3.4, 3.0]
x = 0.5
for i, k in enumerate(["Das sehe ich", "Das heißt es", "Das tue ich"]):
    text(s, x, 1.18, spalten[i], 0.28, k, groesse=11, fett=True, farbe=C["grau"])
    x += spalten[i]
faelle = [
    ("„BEREIT“ bleibt stehen",
     "Der Sender schläft — nach 3 Minuten ohne Bewegung schaltet er ab.",
     "Sender aus und wieder ein."),
    ("Unten rechts „--“", "Keine Karte erkannt.",
     "Karte stecken, sonst FAT32 formatieren."),
    ("Typ zeigt „?“",
     "Der Empfänger wurde nach dem Sender gestartet.",
     "Sender aus und wieder ein."),
    ("Handy findet „Schlepp“ nicht", "Das WLAN ist abgeschaltet.",
     "Reset, warten bis die Aufforderung am Display steht, noch einmal Reset."),
    ("Anzeige bleibt dunkel", "Kein Strom am Empfänger.",
     "Stromversorgung prüfen."),
]
for i, zeilen in enumerate(faelle):
    y = 1.55 + i * 0.65
    if i % 2 == 0:
        kasten(s, 0.4, y - 0.07, 9.2, 0.62, "EEF2F2", rund=False, rand=False)
    cx = 0.5
    for j, zelle in enumerate(zeilen):
        text(s, cx, y, spalten[j] - 0.22, 0.5, zelle,
             groesse=11 if j == 0 else 11.5, schrift=MONO if j == 0 else TEXT,
             fett=(j == 0), farbe=C["gruen"] if j == 2 else C["tinte"],
             anchor=MSO_ANCHOR.MIDDLE, zeile=14)
        cx += spalten[j]
text(s, 0.5, 4.95, 9, 0.4,
     "Hilft das nicht: Gerät aus, zehn Sekunden warten, wieder ein. Gespeicherte "
     "Schlepps gehen dabei nicht verloren.", groesse=12, kursiv=True, farbe=C["grau"])

# ------------------------------------------------------------------ 12 Merkblatt
s = folie(C["dunkel"])
text(s, 0.6, 0.45, 8.8, 0.6, "Fünf Sätze zum Mitnehmen", groesse=30,
     schrift=KOPF, fett=True, farbe=C["weiss"])
merk = [
    "Erst den Empfänger einschalten, dann den Sender.",
    "Den Sender kurz vor dem Start einschalten — nach 3 Minuten ohne Bewegung schläft er ein.",
    "„#12 gespeichert“ heißt: der Schlepp ist auf der Karte.",
    "Die Karte nur stecken oder ziehen, wenn kein Schlepp läuft.",
    "Niemals in ein Staurohr blasen — das beschädigt den Fahrtmesser.",
]
for i, m in enumerate(merk):
    y = 1.3 + i * 0.68
    k = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.6), Inches(y), Inches(0.34),
                           Inches(0.34))
    k.fill.solid()
    k.fill.fore_color.rgb = C["rot"] if i == 4 else C["display"]
    k.line.fill.background()
    k.shadow.inherit = False
    text(s, 0.6, y + 0.03, 0.34, 0.28, str(i + 1), groesse=13, fett=True,
         farbe=C["weiss"] if i == 4 else C["dunkel"], align=PP_ALIGN.CENTER)
    text(s, 1.1, y - 0.02, 8.3, 0.42, m, groesse=15, farbe=C["weiss"],
         anchor=MSO_ANCHOR.MIDDLE, zeile=19)
text(s, 0.6, 4.85, 8.8, 0.35,
     "Fragen, Wartung und neue Flugzeugtypen: ⟨Name und Kontakt ergänzen⟩",
     groesse=12, farbe=C["grauhell"])
notiz(s, "Diese Folie ausdrucken und an den Startwagen hängen.")

prs.save("Schlepp-Bedienung.pptx")

import json
with open("kaesten.json", "w", encoding="utf-8") as f:
    json.dump(kaesten, f, ensure_ascii=False, indent=1)
print("Folien:", len(prs.slides.__iter__.__self__._sldIdLst), "Textkaesten:", len(kaesten))
