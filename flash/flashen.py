# -*- coding: utf-8 -*-
"""Firmware auf ein Board schreiben - ohne Internet, ohne PlatformIO-Aufruf.

    flashen.py <datei.bin> [sender|empfaenger|COMx]

Sucht den Anschluss an der USB-Seriennummer des Boards, damit nicht das falsche
Geraet getroffen wird, und ruft dann esptool auf. Alles, was dazu gebraucht
wird, liegt lokal auf dem Rechner.
"""
import os
import subprocess
import sys

import serial.tools.list_ports

# Eindeutig sind nur die USB-Seriennummern, die COM-Nummern wechseln
BOARDS = {
    "sender":     "576A003770",
    "empfaenger": "576A003661",
}

HEIM = os.environ.get("USERPROFILE") or os.path.expanduser("~")
PYTHON = os.path.join(HEIM, ".platformio", "penv", "Scripts", "python.exe")
ESPTOOL = os.path.join(HEIM, ".platformio", "packages", "tool-esptoolpy", "esptool.py")
ADRESSE = "0x10000"          # nur die Anwendung; Bootloader und Partitionen bleiben


def liste():
    for p in serial.tools.list_ports.comports():
        rolle = next((n for n, s in BOARDS.items() if s == p.serial_number), "unbekannt")
        print("   %-6s %-12s %s" % (p.device, p.serial_number, rolle))


def anschluss(wunsch):
    if wunsch and wunsch.upper().startswith("COM"):
        return wunsch.upper()
    gesucht = BOARDS.get((wunsch or "sender").lower())
    if gesucht is None:
        print("Unbekanntes Board: %r. Erlaubt: sender, empfaenger oder COMx" % wunsch)
        return None
    for p in serial.tools.list_ports.comports():
        if p.serial_number == gesucht:
            return p.device
    print("Board %r (Seriennummer %s) ist nicht angeschlossen." % (wunsch or "sender", gesucht))
    print("Angeschlossen ist:")
    liste()
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("Angeschlossen ist:")
        liste()
        return 2

    datei = sys.argv[1]
    if not os.path.isabs(datei):
        datei = os.path.join(os.path.dirname(os.path.abspath(__file__)), datei)
    if not os.path.isfile(datei):
        print("Datei nicht gefunden: %s" % datei)
        return 2
    for pfad, name in ((PYTHON, "Python"), (ESPTOOL, "esptool")):
        if not os.path.isfile(pfad):
            print("%s fehlt: %s" % (name, pfad))
            return 2

    port = anschluss(sys.argv[2] if len(sys.argv) > 2 else None)
    if port is None:
        return 1

    print("Schreibe %s" % os.path.basename(datei))
    print("     auf %s  (%.0f kB)\n" % (port, os.path.getsize(datei) / 1024.0))
    befehl = [PYTHON, ESPTOOL, "--chip", "esp32", "--port", port, "--baud", "460800",
              "write_flash", "-z", ADRESSE, datei]
    ergebnis = subprocess.call(befehl)
    print()
    if ergebnis == 0:
        print("Fertig. Das Board startet mit der neuen Firmware.")
        print("Mitlesen: mitlesen.bat")
    else:
        print("FEHLGESCHLAGEN (Code %d)." % ergebnis)
        print("Haeufigste Ursachen:")
        print(" - beim Empfaenger: SD-Karte steckt. Karte ziehen, dann noch einmal.")
        print(" - Kabel ist ein Ladekabel ohne Datenleitungen.")
        print(" - ein anderes Programm haelt den Anschluss offen (Monitor schliessen).")
    return ergebnis


if __name__ == "__main__":
    sys.exit(main())
