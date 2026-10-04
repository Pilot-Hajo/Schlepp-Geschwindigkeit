# Flashen ohne Internet

Zum Aufspielen der Firmware braucht dieser Rechner **kein Internet**. Alles liegt
lokal: die fertigen Firmware-Dateien in diesem Ordner und das Schreibwerkzeug
`esptool` in `%USERPROFILE%\.platformio\packages\tool-esptoolpy`.

Nötig ist nur ein **Datenkabel** zum Board. Ein Ladekabel ohne Datenleitungen ist
die häufigste Ursache, wenn nichts gefunden wird.

## Der einfache Weg: Doppelklick

| Datei | Was sie tut |
|---|---|
| `sender flashen - Dauerbetrieb.bat` | Spielt `sender-dauerbetrieb.bin` auf: **kein Schlafmodus**, ein Lebenszeichen je Minute, Reset-Taster nicht nötig. |
| `sender flashen - alter Stand mit Schlafmodus.bat` | Spielt `sender-mit-schlafmodus.bin` auf: der Stand vor dem 04.10.2026. Schaltet nach 3 Minuten ohne Bewegung ab. Nur zum Zurückgehen. |
| `angeschlossene Boards zeigen.bat` | Listet, was gerade am USB hängt, mit Rolle und Anschluss. |
| `mitlesen.bat COM3` | Zeigt die Ausgaben des Boards. Beenden mit Strg + C. |

Das Skript sucht das Board an seiner **USB-Seriennummer**, nicht an der
COM-Nummer. Es kann also nicht versehentlich den Empfänger treffen:

```
Sender      576A003770
Empfänger   576A003661
```

Nach dem Aufspielen muss im Mitlesen-Fenster stehen:

```
MCPH21 Offset: 842483.4
Sender bereit, Kennung 671C, Typ ASK21
```

## Der andere Weg: PlatformIO

Auch `pio run -e sender -t upload` funktioniert offline, weil Werkzeugkette und
Bibliotheken bereits unter `%USERPROFILE%\.platformio` liegen. PlatformIO sucht
allerdings alle 7 Tage nach Aktualisierungen und schickt Telemetrie. Ohne Netz
läuft es nach einer Wartezeit trotzdem durch. Wer das abstellen will:

```
pio settings set check_platformio_interval 365
pio settings set enable_telemetry No
```

Der Weg über die `.bat`-Dateien ist am Flugplatz der verlässlichere: ein Aufruf,
keine Übersetzung, keine Netzversuche.

## Was geschrieben wird

Nur die Anwendung ab Adresse `0x10000`. Bootloader und Partitionstabelle bleiben
unberührt — sie haben sich seit dem ersten Aufspielen nicht geändert. Deshalb
dauert es nur wenige Sekunden.

Falls ein Board einmal gar nicht mehr startet, braucht es den vollständigen
Satz. Der steht in `.pio/build/sender/` und wird so geschrieben:

```
esptool.py --chip esp32 --port COM3 --baud 460800 write_flash -z ^
  0x1000 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin
```

## Fallstricke

- **Empfänger: SD-Karte ziehen.** Die Karte hält GPIO 2 hoch, esptool scheitert
  dann mit `Wrong boot mode detected (0xb)`. Beim Sender gibt es keine Karte.
- **Kein zweites Programm darf den Anschluss offen halten.** Ein noch laufendes
  `mitlesen.bat` blockiert das Flashen.
- **Die Dateien hier sind Abbilder vom 04.10.2026.** Wird am Code etwas
  geändert, müssen sie neu erzeugt werden:
  `copy .pio\build\sender\firmware.bin flash\sender-dauerbetrieb.bin`
