# Banana Pro Bodenanzeige (stillgelegt)

Im September 2026 außer Betrieb genommen, aber aufbewahrt. Zwei Gründe:

- Das 7"-Display ist bei Tageslicht nicht ablesbar.
- Der Pi stört den LoRa-Empfang.

Die Aufgaben hat der LilyGO-Empfänger allein übernommen. Er zeigt die Geschwindigkeit
auf seinem OLED und schreibt die Schlepps auf seine microSD-Karte.

## Was der Pi konnte

- Tachobogen 0–180 km/h mit gelber, grüner und roter Zone und großer Digitalanzeige
- Weißer Soll-Zeiger je Flugzeugtyp während des Schlepps (`SOLL_KMH`, ASK21 = 100 km/h)
- Typanzeige unter dem SIM-Knopf, SIM-Lauf zum Prüfen ohne echten Schlepp
- Helligkeitsschieber am rechten Rand, Start mit 70 %
- AUS-Knopf mittig links: 2 s halten, dann fährt der Pi sauber herunter
- Aufzeichnung auf USB-Stick in `schlepps.csv` und `verlauf.csv`
- Weboberfläche auf Port 8080 mit Live-Tacho fürs Handy, CSV-Download und AUS-Knopf
- USB-Kabel vor WLAN: kamen beide Datenwege gleichzeitig herein, galt das Kabel

## Dateien hier

| Datei | Ort auf dem Pi |
|---|---|
| `schlepp_display.py` | `/opt/schlepp/schlepp_display.py` |
| `schlepp.service` | `/etc/systemd/system/schlepp.service` |
| `ft5x06-touch.service` | `/etc/systemd/system/ft5x06-touch.service` |

## Was NICHT hier liegt

Diese Teile gibt es nur auf der SD-Karte des Pi. **Bevor die Karte anderweitig verwendet
wird, sichern:**

- `/opt/schlepp/ft5x06_touch.py` ist der Treiber für den Touchscreen. Ohne ihn
  reagieren Schieber, SIM und AUS nicht. Zum Sichern:
  ```
  scp -i ~/.ssh/banana_pro root@192.168.0.185:/opt/schlepp/ft5x06_touch.py archiv/banana_pro/
  ```
- Der Eintrag in `/etc/fstab` für den USB-Stick:
  ```
  LABEL=TRAVELSTICK /media/schlepp vfat nofail,flush,umask=0022,x-systemd.device-timeout=5 0 0
  ```
- Die WLAN-Anbindung an das Netz „Schlepp“ des Empfängers (`wlan0`, 192.168.4.10).

## Wieder in Betrieb nehmen

1. **Datenweg:** Die Messzeilen (`V:… RSSI:… TYP:… ID:…`) schreibt der Empfänger weiterhin
   auf die serielle Schnittstelle. Per USB-Kabel funktioniert der Pi deshalb ohne jede
   Änderung an der Firmware. Für den Weg über WLAN in `src/receiver/main.cpp`
   `#define MIT_WLAN 1` setzen und den Empfänger neu bespielen.
2. Dateien an die Orte aus der Tabelle kopieren, danach `systemctl daemon-reload` und
   `systemctl enable --now ft5x06-touch schlepp`.
3. **Nach jedem Kopieren `sync` ausführen.** Armbian hängt `/` mit `commit=120` ein. Bei
   hartem Ausschalten gehen bis zu zwei Minuten an Änderungen verloren, im August 2026
   blieb so ein halbes Skript zurück.

## Lehren aus dem Betrieb

- Den Pi immer über den AUS-Knopf herunterfahren, nie einfach den Strom ziehen.
- Den Touchscreen findet SDL nur beim Start. `schlepp.service` wartet deshalb bis zu 20 s auf
  `ft5x06` in `/proc/bus/input/devices`.
- `pkill -f '[s]chlepp_display.py'` steht mit Klammer, sonst trifft das Muster die eigene
  Shell-Zeile.
- `SDL_AUDIODRIVER=dummy` unterdrückt Hunderte ALSA-Meldungen im Journal.
