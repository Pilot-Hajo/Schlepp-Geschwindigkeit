#!/usr/bin/env python3
"""
Schlepp-Geschwindigkeit Display
Banana Pro + LeMaker 7" LCD (1024x600)
Datenquelle: UDP (WLAN vom LilyGO-Empfaenger) oder USB-Serial als Fallback
Format: [HH:MM:SS] V:x.x T:x.x RSSI:x
"""

import os
import sys
import re
import glob
import socket
import serial
import serial.tools.list_ports
import pygame
import threading
import time
import math
import random
import datetime
import subprocess
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCREEN_W, SCREEN_H = 1024, 600
SERIAL_BAUD    = 115200
SERIAL_TIMEOUT = 5.0
UDP_PORT       = 5005
# Haengt der Empfaenger per USB am Pi, liefern Kabel und WLAN dieselben Daten.
# So lange nach dem letzten seriellen Messwert hat das Kabel Vorrang.
QUELLE_VORRANG_S = 2.0

BLACK      = (0,   0,   0)
WHITE      = (255, 255, 255)
YELLOW     = (255, 240,  20)
YELLOW_DIM = (80,  60,   0)
GREEN      = (60,  220,  60)
GREEN_DIM  = (0,   60,   0)
RED        = (255,  60,  60)
RED_DIM    = (80,   0,   0)
GRAY       = (180, 180, 180)

MAX_SPEED  = 180.0
ARC_CX     = 512
ARC_CY     = 580   # Mittelpunkt des Kreises, knapp unter dem Display
ARC_R      = 450   # Radius
ARC_W      = 50    # Breite des Bogens
ZONE_Y_END = 80.0
ZONE_G_END = 120.0

# Sollgeschwindigkeit im Schlepp je Flugzeugtyp [km/h]. Der weisse Zeiger am
# Aussenrand zeigt sie, solange ein Schlepp laeuft. Weitere Typen einfach hier
# ergaenzen - der Typ kommt vom Sender. Fehlt ein Typ, bleibt der Zeiger weg.
SOLL_KMH = {
    "ASK21": 100.0,
}

SLIDER_W    = 48
SLIDER_X    = SCREEN_W - SLIDER_W
SLIDER_Y1   = 30
SLIDER_Y2   = SCREEN_H - 30
TOUCH_X     = SCREEN_W - 90
BRIGHT_MIN  = 0.2
BRIGHT_MAX  = 1.0
BRIGHT_DEF  = 0.7   # Helligkeit beim Start

SIM_BTN_X = 10
SIM_BTN_Y = 8
SIM_BTN_W = 78
SIM_BTN_H = 40

# Ausschalter mittig am linken Rand. Bewusst mit Halten statt Tippen, damit ihn
# im Cockpit niemand versehentlich ausloest. Auf dieser Hoehe liegt der Bogen
# erst ab x=177, der Knopf steht also frei.
AUS_BTN_X    = 10
AUS_BTN_W    = 78
AUS_BTN_H    = 40
AUS_BTN_Y    = SCREEN_H // 2 - AUS_BTN_H // 2
AUS_HALTEN_S = 2.0

# --- Schlepp-Aufzeichnung auf USB-Stick -------------------------------------
USB_MOUNT     = "/media/schlepp"
CSV_SUMMARY   = os.path.join(USB_MOUNT, "schlepps.csv")
CSV_TRACE     = os.path.join(USB_MOUNT, "verlauf.csv")
LOG_START_KMH = 20.0   # ab hier gilt ein Schlepp als begonnen (wie im Sender)
LOG_STOP_KMH  = 15.0   # darunter gilt er als beendet, ...
LOG_STOP_S    = 3.0    # ... wenn es so lange so bleibt oder Daten ausbleiben

# --- Webserver fuer Handy/Tablet --------------------------------------------
WEB_PORT = 8080


def set_brightness(level):
    level = max(BRIGHT_MIN, min(BRIGHT_MAX, level))
    for path in glob.glob('/sys/class/backlight/*/brightness'):
        try:
            max_path = path.replace('brightness', 'max_brightness')
            with open(max_path) as f:
                max_val = int(f.read().strip())
            with open(path, 'w') as f:
                f.write(str(max_val))
        except Exception:
            pass
    return level


def apply_brightness_overlay(screen, brightness, boost_surf, dim_surf):
    NEUTRAL = 0.55
    if brightness > NEUTRAL:
        add = int((brightness - NEUTRAL) / (1.0 - NEUTRAL) * 120)
        if add > 0:
            boost_surf.fill((add, add, add))
            screen.blit(boost_surf, (0, 0), special_flags=pygame.BLEND_ADD)
    elif brightness < NEUTRAL:
        alpha = int((NEUTRAL - brightness) / (NEUTRAL - BRIGHT_MIN) * 220)
        dim_surf.fill((0, 0, 0, alpha))
        screen.blit(dim_surf, (0, 0))


def herunterfahren():
    """Sauber ausschalten. Das sync davor ist hier wichtig: die Wurzel ist mit
    commit=120 gemountet, ohne sync koennten bis zu zwei Minuten an Aenderungen
    im Cache liegen bleiben."""
    print("Herunterfahren angefordert", flush=True)
    try:
        subprocess.call(["sync"])
        subprocess.Popen(["systemctl", "poweroff"])
    except OSError as e:
        print("Herunterfahren fehlgeschlagen: %s" % e, flush=True)


def find_serial_port():
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = p.description.lower()
        if any(x in desc for x in ["ch340", "ch9102", "cp210", "ftdi", "usb serial", "serial"]):
            candidates.append(p.device)
        elif p.device.startswith("/dev/ttyUSB") or p.device.startswith("/dev/ttyACM"):
            candidates.append(p.device)
    return candidates[0] if candidates else None


class SchleppLogger:
    """Zeichnet jeden Schlepp auf dem USB-Stick auf: eine Zusammenfassung je
    Schlepp in schlepps.csv, den vollen 10-Hz-Verlauf in verlauf.csv.

    Ohne eingesteckten Stick wird nicht aufgezeichnet. Die Dateien sind nur
    waehrend eines laufenden Schlepps geoeffnet und werden am Ende per fsync
    auf das Medium gezwungen - zwischen zwei Schlepps darf der Stick also
    jederzeit gezogen werden.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_mount_try = 0.0
        self._reset()

    def _reset(self):
        self.active       = False   # laeuft gerade ein Schlepp?
        self.aufzeichnung = False   # ... und wird er auch mitgeschrieben?
        self.sid         = ""
        self.t0          = 0.0
        self.n           = 0
        self.vmax        = 0.0
        self.vsum        = 0.0
        self.below_since = 0.0
        self.last_sample = 0.0
        self.actype      = "?"
        self._trace      = None

    def _stick_da(self):
        """Stick vorhanden? Falls er erst nachtraeglich eingesteckt wurde,
        hoechstens alle 5 s ein Mount-Versuch (fstab-Eintrag vorausgesetzt)."""
        if os.path.ismount(USB_MOUNT):
            return True
        now = time.time()
        if now - self._last_mount_try >= 5.0:
            self._last_mount_try = now
            try:
                subprocess.call(["mount", USB_MOUNT],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        return os.path.ismount(USB_MOUNT)

    def laeuft(self):
        """Laeuft gerade ein Schlepp? Bewusst ohne Lock - ein bool zu lesen
        ist atomar, und die Anzeige darf hier nie warten muessen."""
        return self.active

    def sample(self, speed, actype, rssi):
        """Ein empfangener Messwert. Laeuft im Reader-Thread."""
        now = time.time()
        # Der Mount-Versuch kann kurz blockieren, deshalb vor dem Lock. Er laeuft
        # auch im Ruhezustand mit: so wird ein erst nach dem Booten eingesteckter
        # Stick binnen 5 s eingehaengt, statt bis zum ersten Schlepp zu warten.
        stick = self._stick_da() if not self.active else False
        with self._lock:
            if not self.active:
                if speed < LOG_START_KMH:
                    return
                self._start(now, actype, stick)

            self.last_sample = now
            if actype:
                self.actype = actype
            self.n    += 1
            self.vsum += speed
            self.vmax  = max(self.vmax, speed)

            if self._trace is not None:
                stamp = datetime.datetime.now().isoformat(timespec="milliseconds")
                try:
                    self._trace.write("%s;%s;%s;%.1f;%d\n"
                                      % (self.sid, stamp, self.actype, speed, rssi))
                except OSError:      # Stick mitten im Schlepp gezogen
                    self._trace = None

            if speed < LOG_STOP_KMH:
                if not self.below_since:
                    self.below_since = now
            else:
                self.below_since = 0.0

    def tick(self):
        """Beendet den Schlepp, wenn er ausgerollt ist oder der Sender
        schlaeft (dann bleiben die UDP-Daten aus). Laeuft im Hauptthread."""
        now = time.time()
        with self._lock:
            if not self.active:
                return
            ausgerollt = self.below_since and now - self.below_since >= LOG_STOP_S
            keine_daten = now - self.last_sample >= LOG_STOP_S
            if ausgerollt or keine_daten:
                self._finish()

    def _start(self, now, actype, stick):
        self.sid         = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.t0          = now
        self.n           = 0
        self.vsum        = 0.0
        self.vmax        = 0.0
        self.below_since = 0.0
        self.actype      = actype or "?"
        # Ob mitgeschrieben wird, entscheidet sich einmal beim Start. Ein
        # mittendrin eingesteckter Stick faengt erst beim naechsten Schlepp an.
        self.aufzeichnung = stick
        self._trace = None
        if stick:
            try:
                neu = not os.path.exists(CSV_TRACE)
                self._trace = open(CSV_TRACE, "a", buffering=1)
                if neu:
                    self._trace.write("schlepp_id;zeitstempel;typ;v_kmh;rssi\n")
            except OSError:
                self._trace = None
        self.active = True

    def _finish(self):
        dauer   = self.last_sample - self.t0
        schnitt = self.vsum / self.n if self.n else 0.0

        if self._trace is not None:
            try:
                self._trace.flush()
                os.fsync(self._trace.fileno())
                self._trace.close()
            except OSError:
                pass

        if not self.aufzeichnung:
            self._reset()
            return

        try:
            neu = not os.path.exists(CSV_SUMMARY)
            with open(CSV_SUMMARY, "a") as f:
                if neu:
                    f.write("schlepp_id;datum;startzeit;typ;dauer_s;v_max;v_schnitt\n")
                t0 = datetime.datetime.fromtimestamp(self.t0)
                f.write("%s;%s;%s;%s;%.0f;%.0f;%.0f\n"
                        % (self.sid, t0.strftime("%Y-%m-%d"), t0.strftime("%H:%M:%S"),
                           self.actype, dauer, self.vmax, schnitt))
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

        self._reset()


class DataReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.speed    = 0.0
        self.temp     = 0.0
        self.last_rx  = 0.0
        self.port_name = ""
        self.actype   = ""      # Flugzeugtyp, z.B. "ASK21"
        self.senderid = ""      # Kennung des sendenden Geraets, z.B. "A3F1"
        self.last_seriell = 0.0 # letzter Messwert ueber USB-Kabel
        self.logger   = SchleppLogger()
        self._lock    = threading.Lock()

    def _parse(self, line, source, seriell=False):
        m = re.search(r'V:([\d.]+)(?:\s+T:([\d.]+))?\s+RSSI:(-?\d+)'
                      r'(?:\s+TYP:(\S+))?(?:\s+ID:(\S+))?', line)
        if not m:
            return
        try:
            raw  = float(m.group(1))
            rssi = int(m.group(3))
            temp = float(m.group(2)) if m.group(2) is not None else None
        except ValueError:
            return

        now = time.time()
        with self._lock:
            if seriell:
                self.last_seriell = now
            elif now - self.last_seriell < QUELLE_VORRANG_S:
                # Dieselben Daten kommen gerade ueber das USB-Kabel herein.
                # Ohne diesen Vorrang wuerde die Statuszeile zwischen beiden
                # Quellen springen und jeder Messwert doppelt gezaehlt.
                return

            if raw == 0.0:
                # Der Empfaenger meldet genau 0.0, wenn er nichts mehr hoert.
                # Durch den Filter geschickt wuerde daraus ein langsames
                # Absacken, das nie ankommt - also sofort uebernehmen.
                self.speed = 0.0
            else:
                self.speed = 0.24 * raw + 0.76 * self.speed
            if temp is not None:
                self.temp = temp
            if m.group(4):
                self.actype = m.group(4)
            if m.group(5):
                # Kennung des Senders, auf den der Empfaenger eingerastet ist.
                # Nur zur Diagnose - welches Geraet funkt gerade?
                self.senderid = m.group(5)
            typ = self.actype
            self.last_rx   = time.time()
            self.port_name = source

        # Datei-I/O bewusst ausserhalb des Locks, damit die Anzeige nie
        # auf einen langsamen USB-Stick warten muss.
        self.logger.sample(raw, typ, rssi)

    def _run_udp(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(('', UDP_PORT))
            sock.settimeout(1.0)
        except Exception:
            return
        while True:
            try:
                data, addr = sock.recvfrom(256)
                line = data.decode('utf-8', errors='ignore').strip()
                self._parse(line, f"WLAN {addr[0]}")
            except socket.timeout:
                pass
            except Exception:
                time.sleep(1)

    def _run_serial(self):
        while True:
            port = find_serial_port()
            if not port:
                time.sleep(2)
                continue
            try:
                with serial.Serial(port, SERIAL_BAUD, timeout=1) as ser:
                    while True:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            self._parse(line, port, seriell=True)
            except serial.SerialException:
                time.sleep(2)

    def run(self):
        threading.Thread(target=self._run_udp, daemon=True).start()
        self._run_serial()

    def get(self):
        with self._lock:
            # Datenstrom abgerissen - etwa weil der Empfaenger aus ist oder das
            # Kabel ab. Dann 0 zeigen statt den letzten Wert einzufrieren; ohne
            # das stuende die Endgeschwindigkeit des letzten Schlepps beliebig
            # lange auf dem Display.
            if self.last_rx and time.time() - self.last_rx >= SERIAL_TIMEOUT:
                self.speed = 0.0
            return self.speed, self.temp, self.last_rx, self.port_name

    def get_type(self):
        with self._lock:
            return self.actype


class Simulation:
    IDLE, PRE_LAUNCH, ACCELERATION, CRUISE, ENDING = range(5)

    def __init__(self):
        self.state       = self.IDLE
        self.phase_start = 0.0
        self.speed       = 0.0
        self.temp        = 20.0

    def start(self):
        if self.state == self.IDLE:
            self.state       = self.PRE_LAUNCH
            self.phase_start = time.time()
            self.speed       = 0.0

    @property
    def active(self):
        return self.state != self.IDLE

    def update(self):
        now     = time.time()
        elapsed = now - self.phase_start

        if self.state == self.PRE_LAUNCH:
            self.speed = 0.0
            if elapsed >= 3.0:
                self.state = self.ACCELERATION
                self.phase_start = now

        elif self.state == self.ACCELERATION:
            t = min(elapsed / 4.0, 1.0)
            self.speed = 80.0 * t * t
            if elapsed >= 4.0:
                self.state = self.CRUISE
                self.phase_start = now

        elif self.state == self.CRUISE:
            ramp   = min(elapsed / 5.0, 1.0)
            target = 80.0 + ramp * 40.0
            noise  = (8.0 * math.sin(elapsed * 0.7)
                    + 5.0 * math.sin(elapsed * 1.9)
                    + random.uniform(-2.0, 2.0))
            self.speed = max(0.0, target + noise)
            if elapsed >= 30.0:
                self.state = self.ENDING
                self.phase_start = now

        elif self.state == self.ENDING:
            t = min(elapsed / 3.0, 1.0)
            self.speed = max(0.0, 120.0 * (1.0 - t))
            if elapsed >= 3.0:
                self.state = self.IDLE
                self.speed = 0.0

        return self.speed, self.temp


def sim_btn_hit(mx, my):
    return (SIM_BTN_X <= mx <= SIM_BTN_X + SIM_BTN_W
            and SIM_BTN_Y <= my <= SIM_BTN_Y + SIM_BTN_H)


def draw_sim_button(surface, sim, font_btn):
    if sim.active:
        bg, border = (70, 10, 0), RED
    else:
        bg, border = (15, 15, 60), (70, 70, 200)
    rect = pygame.Rect(SIM_BTN_X, SIM_BTN_Y, SIM_BTN_W, SIM_BTN_H)
    pygame.draw.rect(surface, bg, rect, border_radius=8)
    pygame.draw.rect(surface, border, rect, 2, border_radius=8)
    txt = font_btn.render("SIM", False, WHITE)
    tr  = txt.get_rect(center=(SIM_BTN_X + SIM_BTN_W // 2,
                                SIM_BTN_Y + SIM_BTN_H // 2))
    surface.blit(txt, tr)


def draw_actype(surface, actype, font_btn):
    """Flugzeugtyp klein direkt unter dem SIM-Button."""
    if not actype:
        return
    txt = font_btn.render(actype, True, GRAY)
    tr  = txt.get_rect(center=(SIM_BTN_X + SIM_BTN_W // 2,
                               SIM_BTN_Y + SIM_BTN_H + 14))
    surface.blit(txt, tr)


def aus_btn_hit(mx, my):
    return (AUS_BTN_X <= mx <= AUS_BTN_X + AUS_BTN_W
            and AUS_BTN_Y <= my <= AUS_BTN_Y + AUS_BTN_H)


def draw_aus_button(surface, font_btn, anteil):
    """Ausschalter unten links. anteil 0..1 zeigt, wie weit das Halten ist."""
    rect = pygame.Rect(AUS_BTN_X, AUS_BTN_Y, AUS_BTN_W, AUS_BTN_H)
    pygame.draw.rect(surface, (30, 10, 10), rect, border_radius=8)
    if anteil > 0:
        fuell = pygame.Rect(AUS_BTN_X, AUS_BTN_Y,
                            int(AUS_BTN_W * min(anteil, 1.0)), AUS_BTN_H)
        pygame.draw.rect(surface, (140, 20, 20), fuell, border_radius=8)
    pygame.draw.rect(surface, (150, 60, 60), rect, 2, border_radius=8)
    txt = font_btn.render("AUS", False, WHITE)
    surface.blit(txt, txt.get_rect(center=(AUS_BTN_X + AUS_BTN_W // 2,
                                           AUS_BTN_Y + AUS_BTN_H // 2)))


def _arc_polygon(deg_start, deg_end, r_in, r_out, steps=80):
    """Liefert Polygon-Punkte für einen Kreisring-Sektor."""
    pts_out, pts_in = [], []
    for i in range(steps + 1):
        d   = deg_start + (deg_end - deg_start) * i / steps
        rad = math.radians(d)
        c, s = math.cos(rad), math.sin(rad)
        pts_out.append((ARC_CX + r_out * c, ARC_CY + r_out * s))
        pts_in.append( (ARC_CX + r_in  * c, ARC_CY + r_in  * s))
    return pts_out + list(reversed(pts_in))


def draw_arc(surface, speed):
    r_out = ARC_R
    r_in  = ARC_R - ARC_W

    def kmh_to_deg(kmh):
        return 180.0 + (kmh / MAX_SPEED) * 180.0

    speed_deg = 180.0 + min(speed / MAX_SPEED, 1.0) * 180.0

    zones = [
        (180.0,               kmh_to_deg(ZONE_Y_END), YELLOW_DIM, YELLOW),
        (kmh_to_deg(ZONE_Y_END), kmh_to_deg(ZONE_G_END), GREEN_DIM, GREEN),
        (kmh_to_deg(ZONE_G_END), 360.0,               RED_DIM,    RED),
    ]

    for z_start, z_end, dim_col, bright_col in zones:
        # Hintergrund (gedimmt)
        pygame.draw.polygon(surface, dim_col, _arc_polygon(z_start, z_end, r_in, r_out))
        # Ausgefüllter Anteil bis zur aktuellen Geschwindigkeit
        if speed_deg > z_start:
            fill_end = min(speed_deg, z_end)
            pygame.draw.polygon(surface, bright_col,
                                _arc_polygon(z_start, fill_end, r_in, r_out))

    # Skalenstriche
    for kmh in range(0, int(MAX_SPEED) + 1, 20):
        deg  = 180.0 + (kmh / MAX_SPEED) * 180.0
        rad  = math.radians(deg)
        c, s = math.cos(rad), math.sin(rad)
        major = (kmh % 60 == 0)
        tick  = 28 if major else 14
        r1 = r_in - 5
        r2 = r1 - tick
        pygame.draw.line(surface, WHITE,
                         (int(ARC_CX + r1*c), int(ARC_CY + r1*s)),
                         (int(ARC_CX + r2*c), int(ARC_CY + r2*s)),
                         3 if major else 1)


def draw_soll_zeiger(surface, soll):
    """Weisses Dreieck am Aussenrand des Bogens: Sollgeschwindigkeit."""
    if soll is None:
        return
    deg  = 180.0 + min(max(soll, 0.0), MAX_SPEED) / MAX_SPEED * 180.0
    rad  = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    px, py = -s, c                      # senkrecht zum Radius
    spitze = (ARC_CX + (ARC_R + 3) * c,  ARC_CY + (ARC_R + 3) * s)
    basis  = (ARC_CX + (ARC_R + 26) * c, ARC_CY + (ARC_R + 26) * s)
    pygame.draw.polygon(surface, WHITE, [
        spitze,
        (basis[0] + px * 12, basis[1] + py * 12),
        (basis[0] - px * 12, basis[1] - py * 12),
    ])


def draw_slider(surface, brightness, font_small):
    h  = SLIDER_Y2 - SLIDER_Y1
    cx = SLIDER_X + SLIDER_W // 2

    pygame.draw.rect(surface, (50, 50, 50),
                     (SLIDER_X, SLIDER_Y1, SLIDER_W, h), border_radius=12)

    rel    = (brightness - BRIGHT_MIN) / (BRIGHT_MAX - BRIGHT_MIN)
    fill_h = int(rel * h)
    if fill_h > 0:
        fill_y = SLIDER_Y2 - fill_h
        pygame.draw.rect(surface, (255, 200, 40),
                         (SLIDER_X, fill_y, SLIDER_W, fill_h), border_radius=12)

    pygame.draw.rect(surface, GRAY,
                     (SLIDER_X, SLIDER_Y1, SLIDER_W, h), 2, border_radius=12)

    knob_y = SLIDER_Y2 - fill_h
    knob_y = max(SLIDER_Y1 + 16, min(SLIDER_Y2 - 16, knob_y))
    pygame.draw.circle(surface, WHITE, (cx, knob_y), 16)
    pygame.draw.circle(surface, GRAY,  (cx, knob_y), 16, 2)

    pct_surf = font_small.render(f"{int(brightness * 100)}%", True, GRAY)
    surface.blit(pct_surf, pct_surf.get_rect(center=(cx, SLIDER_Y2 + 16)))

    sun_surf = font_small.render("*", True, YELLOW)
    surface.blit(sun_surf, sun_surf.get_rect(center=(cx, SLIDER_Y1 - 14)))


def slider_hit(mx, my):
    return mx >= TOUCH_X and 0 <= my <= SCREEN_H


def y_to_brightness(my):
    my  = max(0, min(SCREEN_H, my))
    rel = (SCREEN_H - my) / SCREEN_H
    return BRIGHT_MIN + rel * (BRIGHT_MAX - BRIGHT_MIN)


HTML_SEITE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Schlepp-Geschwindigkeit</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; -webkit-text-size-adjust: 100%; }
  body { margin: 0; padding: 12px env(safe-area-inset-right) 24px env(safe-area-inset-left);
         background: #000; color: #fff;
         font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; }
  .tacho { position: relative; max-width: 480px; margin: 0 auto; }
  svg { display: block; width: 100%; height: auto; }
  .werte { position: absolute; left: 0; right: 0; bottom: 4%; text-align: center; }
  .v { font-size: 22vw; font-weight: 700; line-height: .9; }
  @media (min-width: 480px) { .v { font-size: 106px; } }
  .einheit { font-size: 1.3rem; font-weight: 700; opacity: .85; }
  .kopf { max-width: 480px; margin: 0 auto 4px; display: flex;
          justify-content: space-between; align-items: baseline; font-size: .95rem; }
  .typ { color: #b4b4b4; font-weight: 700; letter-spacing: .05em; }
  .status { color: #b4b4b4; }
  .status.weg { color: #ff3c3c; }
  .dateien { max-width: 480px; margin: 24px auto 0;
             border-top: 1px solid #333; padding-top: 14px; }
  .dateien h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .1em;
                color: #888; font-weight: 600; margin: 0 0 10px; }
  a.datei { display: flex; justify-content: space-between; align-items: center;
            gap: 12px; padding: 14px 16px; margin-bottom: 8px; border-radius: 12px;
            background: #141414; border: 1px solid #2a2a2a;
            color: #fff; text-decoration: none; }
  a.datei:active { background: #222; }
  a.datei .name { font-weight: 600; }
  a.datei .meta { color: #888; font-size: .8rem; text-align: right; white-space: nowrap; }
  .hinweis { color: #888; font-size: .85rem; }
  .aus { max-width: 480px; margin: 22px auto 0; }
  .aus button { width: 100%; padding: 15px; border-radius: 12px; font: inherit;
                font-weight: 600; color: #ff8a8a; background: #1a0e0e;
                border: 1px solid #4a2020; }
  .aus button:active { background: #2c1414; }
</style></head><body>

<div class="kopf"><span class="typ" id="typ"></span><span class="status" id="status"></span></div>

<div class="tacho">
  <svg viewBox="0 0 400 212" aria-hidden="true">
    <g id="zonen" fill="none" stroke-width="34"></g>
    <g id="fuell" fill="none" stroke-width="34"></g>
    <g id="striche" stroke="#fff"></g>
    <g id="marken" fill="#888" font-size="15" text-anchor="middle"
       font-family="-apple-system, Arial, sans-serif"></g>
    <polygon id="soll" fill="#fff"></polygon>
  </svg>
  <div class="werte"><div class="v" id="v">--</div><div class="einheit">km/h</div></div>
</div>

<div class="dateien"><h2>Aufzeichnungen</h2><div id="liste"></div></div>

<div class="aus"><button id="ausknopf" type="button">Pi herunterfahren</button></div>

<script>
const CX = 200, CY = 200, R = 165, MAXV = 180, Z1 = 80, Z2 = 120;
const ZONEN = [[0, Z1, "#503c00", "#fff014"],
               [Z1, Z2, "#003c00", "#3cdc3c"],
               [Z2, MAXV, "#500000", "#ff3c3c"]];

const grad = v => 180 + Math.min(Math.max(v, 0), MAXV) / MAXV * 180;
const pkt  = (a, r) => [CX + r * Math.cos(a * Math.PI / 180),
                        CY + r * Math.sin(a * Math.PI / 180)];

function bogen(v0, v1) {
  const a0 = grad(v0), a1 = grad(v1);
  if (a1 <= a0 + 0.05) return "";
  const [x0, y0] = pkt(a0, R), [x1, y1] = pkt(a1, R);
  return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${R} ${R} 0 ${a1 - a0 > 180 ? 1 : 0} 1 `
       + `${x1.toFixed(1)} ${y1.toFixed(1)}`;
}

const NS = "http://www.w3.org/2000/svg";
function pfad(gruppe, d, farbe) {
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", d); p.setAttribute("stroke", farbe);
  document.getElementById(gruppe).appendChild(p);
  return p;
}

ZONEN.forEach(z => pfad("zonen", bogen(z[0], z[1]), z[2]));
const fuellungen = ZONEN.map(z => pfad("fuell", "", z[3]));

for (let v = 0; v <= MAXV; v += 20) {
  const a = grad(v), gross = v % 60 === 0;
  const [x1, y1] = pkt(a, R - 22), [x2, y2] = pkt(a, R - 22 - (gross ? 14 : 7));
  const l = document.createElementNS(NS, "line");
  l.setAttribute("x1", x1.toFixed(1)); l.setAttribute("y1", y1.toFixed(1));
  l.setAttribute("x2", x2.toFixed(1)); l.setAttribute("y2", y2.toFixed(1));
  l.setAttribute("stroke-width", gross ? 3 : 1);
  document.getElementById("striche").appendChild(l);
  if (gross) {
    const [tx, ty] = pkt(a, R - 52);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", tx.toFixed(1)); t.setAttribute("y", (ty + 5).toFixed(1));
    t.textContent = v;
    document.getElementById("marken").appendChild(t);
  }
}

// Weisses Dreieck am Aussenrand: Sollgeschwindigkeit, nur waehrend des Schlepps
function zeiger(soll) {
  const el = document.getElementById("soll");
  if (soll === null || soll === undefined) { el.setAttribute("points", ""); return; }
  const a = grad(soll) * Math.PI / 180;
  const c = Math.cos(a), s = Math.sin(a), px = -s, py = c;
  const RA = R + 17;                       // Aussenkante des Bogens
  const [sx, sy] = [CX + (RA + 2) * c,  CY + (RA + 2) * s];
  const [bx, by] = [CX + (RA + 15) * c, CY + (RA + 15) * s];
  el.setAttribute("points",
    `${sx.toFixed(1)},${sy.toFixed(1)} `
  + `${(bx + px * 7).toFixed(1)},${(by + py * 7).toFixed(1)} `
  + `${(bx - px * 7).toFixed(1)},${(by - py * 7).toFixed(1)}`);
}

function zeichne(v) {
  ZONEN.forEach((z, i) =>
    fuellungen[i].setAttribute("d", bogen(z[0], Math.min(v, z[1]))));
  document.getElementById("v").textContent = Math.round(v);
}

let liste_stand = "";
function aktualisiere(d) {
  zeichne(d.v);
  zeiger(d.soll);
  document.getElementById("typ").textContent = d.typ || "";
  const st = document.getElementById("status");
  st.textContent = d.verbunden ? d.quelle : "Keine Verbindung...";
  st.classList.toggle("weg", !d.verbunden);

  const key = JSON.stringify(d.dateien);
  if (key !== liste_stand) {
    liste_stand = key;
    const el = document.getElementById("liste");
    el.innerHTML = d.dateien.length ? "" :
      '<div class="hinweis">Kein USB-Stick eingesteckt.</div>';
    d.dateien.forEach(f => {
      const a = document.createElement("a");
      a.className = "datei"; a.href = "/" + f.name; a.setAttribute("download", f.name);
      a.innerHTML = '<span class="name"></span><span class="meta"></span>';
      a.querySelector(".name").textContent = f.name;
      a.querySelector(".meta").textContent = f.kb + " KB \\u00b7 " + f.stand;
      el.appendChild(a);
    });
  }
}

async function tick() {
  try {
    const r = await fetch("/api", { cache: "no-store" });
    aktualisiere(await r.json());
  } catch (e) {
    const st = document.getElementById("status");
    st.textContent = "Pi nicht erreichbar"; st.classList.add("weg");
  }
}
tick();
setInterval(tick, 250);

document.getElementById("ausknopf").addEventListener("click", async () => {
  if (!confirm("Pi wirklich herunterfahren?")) return;
  try { await fetch("/aus", { method: "POST" }); } catch (e) {}
  document.body.innerHTML =
    '<p style="padding:24px;line-height:1.6">Der Pi fährt herunter. Warte, bis '
  + 'das Display dunkel ist — danach kannst du die Stromversorgung trennen.</p>';
});
</script></body></html>
"""


class SchleppWeb(BaseHTTPRequestHandler):
    """Liefert die Live-Anzeige fuers Handy und die CSV-Dateien vom Stick."""

    reader = None
    sim    = None
    protocol_version = "HTTP/1.1"
    # Safari haelt Keep-Alive-Verbindungen offen. Ohne Timeout bliebe je
    # Verbindung ein Thread ewig haengen und ueber Stunden sammeln sie sich an.
    timeout = 15

    def log_message(self, fmt, *args):
        pass                      # kein Zugriffslog ins journal

    def _antwort(self, code, ctype, body, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        pfad = self.path.split("?")[0]
        if pfad == "/":
            self._antwort(200, "text/html; charset=utf-8", HTML_SEITE)
        elif pfad == "/api":
            self._antwort(200, "application/json; charset=utf-8",
                          json.dumps(self._zustand()))
        elif pfad in ("/schlepps.csv", "/verlauf.csv"):
            self._csv(pfad.lstrip("/"))
        else:
            self._antwort(404, "text/plain; charset=utf-8", "nicht gefunden")

    def do_POST(self):
        # Ausschalten bewusst nur per POST: ein GET koennte Safari beim
        # Vorausladen von Links versehentlich ausloesen.
        if self.path.split("?")[0] == "/aus":
            self._antwort(200, "text/plain; charset=utf-8", "Pi faehrt herunter")
            # erst die Antwort ausliefern, dann ausschalten
            threading.Timer(1.0, herunterfahren).start()
        else:
            self._antwort(404, "text/plain; charset=utf-8", "nicht gefunden")

    def _csv(self, name):
        try:
            with open(os.path.join(USB_MOUNT, name), "rb") as f:
                daten = f.read()
        except OSError:
            self._antwort(404, "text/plain; charset=utf-8",
                          "Datei nicht vorhanden - steckt der USB-Stick?")
            return
        self._antwort(200, "text/csv; charset=utf-8", daten,
                      {"Content-Disposition": 'attachment; filename="%s"' % name})

    def _zustand(self):
        if self.sim is not None and self.sim.active:
            speed, temp = self.sim.speed, self.sim.temp
            verbunden, quelle = True, "SIMULATION"
        else:
            speed, temp, last_rx, quelle = self.reader.get()
            verbunden = (time.time() - last_rx) < SERIAL_TIMEOUT if last_rx > 0 else False

        dateien = []
        for name in ("schlepps.csv", "verlauf.csv"):
            try:
                st = os.stat(os.path.join(USB_MOUNT, name))
            except OSError:
                continue
            dateien.append({
                "name":  name,
                "kb":    round(st.st_size / 1024.0, 1),
                "stand": time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime)),
            })

        typ    = self.reader.get_type()
        laeuft = (self.sim is not None and self.sim.active) or self.reader.logger.laeuft()
        soll   = SOLL_KMH.get(typ) if laeuft else None

        return {"v": round(speed, 1), "temp": round(temp, 1), "typ": typ,
                "id": self.reader.senderid,
                "verbunden": verbunden, "quelle": quelle if verbunden else "",
                "soll": soll, "dateien": dateien}


class SchleppHTTPServer(ThreadingHTTPServer):
    daemon_threads     = True
    request_queue_size = 32          # mehrere Handys zugleich

    def handle_error(self, request, client_address):
        """Ein Handy, das die Seite schliesst oder in den Sperrbildschirm geht,
        reisst die Verbindung ab. Das ist normal und darf nicht jedes Mal einen
        Traceback ins Journal schreiben - /var/log liegt auf einer 50-MB-zram."""
        if sys.exc_info()[0] in (ConnectionResetError, BrokenPipeError,
                                 ConnectionAbortedError, TimeoutError, socket.timeout):
            return
        super().handle_error(request, client_address)


def starte_webserver(reader, sim):
    """Laeuft als Daemon-Thread neben der Anzeige. Faellt er aus, laeuft das
    Display unbeeindruckt weiter."""
    SchleppWeb.reader = reader
    SchleppWeb.sim    = sim
    try:
        srv = SchleppHTTPServer(("", WEB_PORT), SchleppWeb)
    except OSError as e:
        print("Webserver nicht gestartet: %s" % e, flush=True)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("Webserver auf Port %d" % WEB_PORT, flush=True)


def main():
    # Die Anwendung gibt keinen Ton aus. Ohne diese Zeile oeffnet SDL trotzdem
    # ALSA und schreibt im Sekundentakt "underrun occurred" ins Journal.
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    pygame.mouse.set_visible(False)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
    pygame.display.set_caption("Schlepp-Geschwindigkeit")

    font_speed  = pygame.font.SysFont("DejaVu Sans", 240, bold=True)
    font_unit   = pygame.font.SysFont("DejaVu Sans",  58, bold=True)
    font_info   = pygame.font.SysFont("DejaVu Sans",  32)
    font_status = pygame.font.SysFont("DejaVu Sans",  16)
    font_small  = pygame.font.SysFont("DejaVu Sans",  22)

    reader = DataReader()
    reader.start()

    boost_surf = pygame.Surface((SCREEN_W, SCREEN_H))
    dim_surf   = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)

    clock      = pygame.time.Clock()
    brightness = BRIGHT_DEF
    dragging   = False
    aus_seit   = 0.0            # seit wann der Ausschalter gehalten wird
    sim        = Simulation()
    set_brightness(brightness)
    starte_webserver(reader, sim)

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit(); sys.exit()

            if event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos
                if sim_btn_hit(mx, my):
                    sim.start()
                elif aus_btn_hit(mx, my):
                    aus_seit = time.time()
                elif slider_hit(mx, my):
                    dragging   = True
                    brightness = y_to_brightness(my)
                    set_brightness(brightness)
            if event.type == pygame.MOUSEBUTTONUP:
                dragging = False
                aus_seit = 0.0
            if event.type == pygame.MOUSEMOTION and dragging:
                _, my = event.pos
                brightness = y_to_brightness(my)
                set_brightness(brightness)

            if event.type == pygame.FINGERDOWN:
                mx = int(event.x * SCREEN_W)
                my = int(event.y * SCREEN_H)
                if sim_btn_hit(mx, my):
                    sim.start()
                elif aus_btn_hit(mx, my):
                    aus_seit = time.time()
                elif slider_hit(mx, my):
                    dragging   = True
                    brightness = y_to_brightness(my)
                    set_brightness(brightness)
            if event.type == pygame.FINGERUP:
                dragging = False
                aus_seit = 0.0
            if event.type == pygame.FINGERMOTION and dragging:
                my = int(event.y * SCREEN_H)
                brightness = y_to_brightness(my)
                set_brightness(brightness)

        if sim.active:
            speed, temp = sim.update()
            connected, port = True, "SIMULATION"
        else:
            speed, temp, last_rx, port = reader.get()
            connected = (time.time() - last_rx) < SERIAL_TIMEOUT if last_rx > 0 else False

        # Schlepp abschliessen, wenn ausgerollt oder der Sender eingeschlafen ist
        reader.logger.tick()

        # Zeiger nur waehrend eines Schlepps - der SIM-Lauf zaehlt mit, damit
        # sich die Anzeige auch ohne echten Schlepp pruefen laesst.
        laeuft = sim.active or reader.logger.laeuft()
        soll   = SOLL_KMH.get(reader.get_type()) if laeuft else None

        screen.fill(BLACK)
        draw_arc(screen, speed)
        draw_soll_zeiger(screen, soll)

        # Geschwindigkeit
        spd_surf = font_speed.render(f"{speed:.0f}", False, WHITE)
        screen.blit(spd_surf, spd_surf.get_rect(center=(ARC_CX, ARC_CY - 240)))

        # Einheit
        unit_surf = font_unit.render("km/h", False, WHITE)
        screen.blit(unit_surf, unit_surf.get_rect(center=(ARC_CX, ARC_CY - 130)))

        # Verbindungsstatus oben
        if connected:
            st_surf = font_status.render(port, False, GRAY)
        else:
            st_surf = font_status.render("Keine Verbindung...", False, RED)
        screen.blit(st_surf, st_surf.get_rect(center=(ARC_CX, 22)))

        # Ausschalter: erst nach AUS_HALTEN_S Sekunden Halten loest er aus
        halten = (time.time() - aus_seit) if aus_seit else 0.0
        if halten >= AUS_HALTEN_S:
            aus_seit = 0.0
            herunterfahren()

        draw_sim_button(screen, sim, font_small)
        draw_actype(screen, reader.get_type(), font_small)
        draw_aus_button(screen, font_small, halten / AUS_HALTEN_S)
        draw_slider(screen, brightness, font_small)
        apply_brightness_overlay(screen, brightness, boost_surf, dim_surf)

        pygame.display.flip()
        clock.tick(15)


if __name__ == "__main__":
    main()
