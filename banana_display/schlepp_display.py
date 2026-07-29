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

SCREEN_W, SCREEN_H = 1024, 600
SERIAL_BAUD    = 115200
SERIAL_TIMEOUT = 5.0
UDP_PORT       = 5005

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

SLIDER_W    = 48
SLIDER_X    = SCREEN_W - SLIDER_W
SLIDER_Y1   = 30
SLIDER_Y2   = SCREEN_H - 30
TOUCH_X     = SCREEN_W - 90
BRIGHT_MIN  = 0.2
BRIGHT_MAX  = 1.0

SIM_BTN_X = 10
SIM_BTN_Y = 8
SIM_BTN_W = 78
SIM_BTN_H = 40


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
        add = int((brightness - NEUTRAL) / (1.0 - NEUTRAL) * 80)
        if add > 0:
            boost_surf.fill((add, add, add))
            screen.blit(boost_surf, (0, 0), special_flags=pygame.BLEND_ADD)
    elif brightness < NEUTRAL:
        alpha = int((NEUTRAL - brightness) / (NEUTRAL - BRIGHT_MIN) * 220)
        dim_surf.fill((0, 0, 0, alpha))
        screen.blit(dim_surf, (0, 0))


def find_serial_port():
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = p.description.lower()
        if any(x in desc for x in ["ch340", "ch9102", "cp210", "ftdi", "usb serial", "serial"]):
            candidates.append(p.device)
        elif p.device.startswith("/dev/ttyUSB") or p.device.startswith("/dev/ttyACM"):
            candidates.append(p.device)
    return candidates[0] if candidates else None


class DataReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.speed    = 0.0
        self.temp     = 0.0
        self.last_rx  = 0.0
        self.port_name = ""
        self._lock    = threading.Lock()

    def _parse(self, line, source):
        m = re.search(r'V:([\d.]+)(?:\s+T:([\d.]+))?\s+RSSI:(-?\d+)', line)
        if m:
            try:
                with self._lock:
                    raw = float(m.group(1))
                    self.speed = 0.12 * raw + 0.88 * self.speed
                    if m.group(2) is not None:
                        self.temp = float(m.group(2))
                    self.last_rx  = time.time()
                    self.port_name = source
            except ValueError:
                pass

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
                            self._parse(line, port)
            except serial.SerialException:
                time.sleep(2)

    def run(self):
        threading.Thread(target=self._run_udp, daemon=True).start()
        self._run_serial()

    def get(self):
        with self._lock:
            return self.speed, self.temp, self.last_rx, self.port_name


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


def main():
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
    brightness = BRIGHT_MAX
    dragging   = False
    sim        = Simulation()
    set_brightness(brightness)

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
                elif slider_hit(mx, my):
                    dragging   = True
                    brightness = y_to_brightness(my)
                    set_brightness(brightness)
            if event.type == pygame.MOUSEBUTTONUP:
                dragging = False
            if event.type == pygame.MOUSEMOTION and dragging:
                _, my = event.pos
                brightness = y_to_brightness(my)
                set_brightness(brightness)

            if event.type == pygame.FINGERDOWN:
                mx = int(event.x * SCREEN_W)
                my = int(event.y * SCREEN_H)
                if sim_btn_hit(mx, my):
                    sim.start()
                elif slider_hit(mx, my):
                    dragging   = True
                    brightness = y_to_brightness(my)
                    set_brightness(brightness)
            if event.type == pygame.FINGERUP:
                dragging = False
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

        screen.fill(BLACK)
        draw_arc(screen, speed)

        # Geschwindigkeit
        spd_surf = font_speed.render(f"{speed:.0f}", False, WHITE)
        screen.blit(spd_surf, spd_surf.get_rect(center=(ARC_CX, ARC_CY - 240)))

        # Einheit
        unit_surf = font_unit.render("km/h", False, WHITE)
        screen.blit(unit_surf, unit_surf.get_rect(center=(ARC_CX, ARC_CY - 130)))

        # Temperatur
        temp_surf = font_info.render(f"{temp:.1f} °C", False, YELLOW)
        screen.blit(temp_surf, temp_surf.get_rect(center=(ARC_CX, SCREEN_H - 18)))

        # Verbindungsstatus oben
        if connected:
            st_surf = font_status.render(port, False, GRAY)
        else:
            st_surf = font_status.render("Keine Verbindung...", False, RED)
        screen.blit(st_surf, st_surf.get_rect(center=(ARC_CX, 22)))

        draw_sim_button(screen, sim, font_small)
        draw_slider(screen, brightness, font_small)
        apply_brightness_overlay(screen, brightness, boost_surf, dim_surf)

        pygame.display.flip()
        clock.tick(15)


if __name__ == "__main__":
    main()
