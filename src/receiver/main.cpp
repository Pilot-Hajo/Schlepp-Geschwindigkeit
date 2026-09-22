#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <SD.h>
#include <Preferences.h>

// WLAN-Zugangspunkt "Schlepp" mit Webseite fuers Handy (Live-Tacho, Schlepp-
// Liste, CSV-Download) unter http://192.168.4.1. Eingeschaltet wird es im
// Betrieb per Doppel-Reset; MIT_WLAN 0 nimmt den ganzen WLAN-Teil heraus.
#define MIT_WLAN 1

// UDP-Rundsendung jeder Messzeile - nur fuer den stillgelegten Banana Pro
// (archiv/banana_pro). Aus, weil sie zehnmal pro Sekunde einen WLAN-Sendevorgang
// neben dem LoRa-Empfang ausloest, fuer einen Empfaenger, den es nicht mehr gibt.
#define MIT_UDP_PI 0

#if MIT_WLAN
#include <WiFi.h>
#include <WebServer.h>
#if MIT_UDP_PI
#include <WiFiUDP.h>
#endif
#endif

// Pinbelegung LilyGO T3 V1.6.x. Der Reset des Funkchips liegt auf GPIO 23 -
// frueher stand hier 14, das ist aber der Takt der Speicherkarte.
#define LORA_SCK    5
#define LORA_MISO   19
#define LORA_MOSI   27
#define LORA_SS     18
#define LORA_RST    23
#define LORA_DIO0   26

#define OLED_SDA    21
#define OLED_SCL    22

// microSD-Slot auf eigenem SPI-Bus (HSPI), getrennt vom Funk (VSPI)
#define SD_SCK      14
#define SD_MISO     2
#define SD_MOSI     15
#define SD_CS       13

#define LORA_FREQ   868E6
#define LORA_SYNC   0x3C      // muss zum Sender passen; 0x12 ist der Werkswert aller Boards

// Es kann viele Sender geben - einen je Flugzeug -, aber es startet immer nur
// eines. Der Empfaenger rastet deshalb auf den ersten Sender ein, der ueber
// LOCK_SPEED kommt, also auf den, der wirklich losrollt, und blendet ab da
// alle anderen Kennungen vollstaendig aus.
#define LOCK_SPEED     20.0f    // km/h - wie START_SPEED im Sender
#define LOCK_FREI_KMH  15.0f    // darunter gilt das Flugzeug als ausgerollt
#define LOCK_FREI_MS   3000UL   // so lange unter LOCK_FREI_KMH oder ohne Paket -> Bindung loesen
#define VORSCHAU_MS    2000UL   // so lange gilt der bisher schnellste Sender als Vorschau
#define STILLE_MS      3000UL   // so lange ohne Paket -> Anzeige auf 0 statt eingefroren
#define TYP_MAX        8        // so viele Sender merkt sich der Empfaenger

// Aufzeichnung: ein Schlepp ist genau die Zeit, in der der Empfaenger auf einen
// Sender eingerastet ist. Der Verlauf wird im RAM gesammelt und erst nach dem
// Schlepp auf die Karte geschrieben - eine Karte kann einzelne Schreibvorgaenge
// ueber 100 ms verzoegern, und in der Zeit wuerde der Funk Pakete verlieren.
#define PROBEN_MAX     1500     // 150 s bei 10 Hz; ein Schlepp dauert 50 s
#define SD_PRUEF_MS    10000UL  // so oft wird im Leerlauf nach der Karte gesehen
#define HINWEIS_MS     10000UL  // so lange steht "gespeichert" im Display

#define DATEI_SCHLEPPS "/schlepps.csv"
#define DATEI_VERLAUF  "/verlauf.csv"

// Die SD-Karte teilen sich zwei Kerne: die Hauptschleife schreibt Schlepps,
// der Webserver liest sie fuer Downloads. Ohne Sperre koennten beide
// gleichzeitig auf die Karte zugreifen.
static SemaphoreHandle_t sdSperre = nullptr;

#if MIT_WLAN
const char* AP_SSID = "Schlepp";
const char* AP_PASS = "schlepp123";
WebServer web(80);

// Der Webserver laeuft auf Kern 0, der Funkempfang in loop() auf Kern 1.
// Eine Anfrage wartet, bis das Handy seine Daten geschickt hat - im
// schlimmsten Fall Sekunden. Liefe das in loop(), stuende so lange der
// Empfang, und jedes zweite Paket in der Zeit waere verloren (gemessen:
// vier Stockungen von 100 ms in einem 50-s-Schlepp).
//
// Deshalb liest der Webserver nie die Variablen der Hauptschleife direkt,
// sondern nur diese Momentaufnahme - die Strings dort koennten sich sonst
// mitten im Lesen verschieben.
struct Momentaufnahme {
    float v;
    int rssi;
    bool sender, laeuft, sd;
    uint32_t nr;
    char typ[17];
    char id[17];
};
static Momentaufnahme moment = {};
static portMUX_TYPE momentSperre = portMUX_INITIALIZER_UNLOCKED;
// Doppel-Reset: so lange nach dem Setzen der Marke schaltet ein zweiter Reset
// das WLAN um. Gemessen ab der Marke, nicht ab dem Einschalten - bis setup()
// ueberhaupt laeuft, vergehen nach einem Reset schon rund 1,8 s. Ein Fenster ab
// Einschalten war deshalb fuer einen Fingerdruck praktisch nicht zu treffen.
#define DOPPEL_RESET_MS 4000UL
static bool wlanAn = true;            // Zustand steht im NVS, uebersteht das Ausschalten
static bool resetMarkeOffen = false;  // Marke fuer den Doppel-Reset noch nicht geloescht
static unsigned long markeGesetzt = 0;

#if MIT_UDP_PI
const uint16_t UDP_PORT = 5005;
WiFiUDP udp;
#endif
#endif

// Sollgeschwindigkeit im Schlepp je Flugzeugtyp [km/h] - wie am Pi. Die Handy-
// seite zeigt sie als weisses Dreieck, solange ein Schlepp laeuft.
struct Soll { const char* typ; float kmh; };
static const Soll SOLL_KMH[] = {
    { "ASK21", 100.0f },
};

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);
SPIClass sdSpi(HSPI);
Preferences prefs;

static String flugzeugTyp = "?";      // Typ des eingerasteten Senders, z.B. "ASK21"
static bool typAusgeblendet = false;  // einmal >=100 km/h -> Typ bleibt weg

static String lockId = "";            // Kennung des Senders, der gerade startet
static unsigned long lockPaket = 0;   // letztes Paket von ihm
static unsigned long unterSchwelle = 0;

static String vorschauId = "";        // vor dem Einrasten: der schnellste Sender
static float vorschauSpeed = -1.0f;
static unsigned long vorschauZeit = 0;

// Letztes verwertetes Paket - danach faellt die Anzeige auf 0 zurueck
static unsigned long letztesPaket = 0;
static String letzteId = "";
static int letzterRssi = 0;
static bool aufNull = true;

// Kleine Tabelle Kennung -> Typ. Der Typ wird gemeldet, bevor das Flugzeug
// rollt; beim Einrasten muss der passende Eintrag noch da sein.
static String typId[TYP_MAX];
static String typWert[TYP_MAX];
static int typNaechster = 0;

// --- Aufzeichnung -----------------------------------------------------------
struct Probe {
    uint16_t t_ds;     // Zehntelsekunden seit Schleppbeginn
    uint16_t v_dkmh;   // Zehntel km/h
    int16_t  rssi;
};
static Probe proben[PROBEN_MAX];
static uint16_t probenAnzahl = 0;
static uint32_t probenGesamt = 0;     // auch die, die nicht mehr in den Puffer passten
static float vMax = 0.0f;
static float vSumme = 0.0f;
static unsigned long schleppStart = 0;
static unsigned long schleppLetzte = 0;
static String schleppBeginn = "";     // Betriebszeit beim Start, "hh:mm:ss"

static bool sdBereit = false;
static unsigned long sdGeprueft = 0;
static uint32_t naechsteNr = 1;       // laufende Schleppnummer, uebersteht das Ausschalten

static char hinweis[24] = "";
static unsigned long hinweisSeit = 0;
static bool hinweisAktiv = false;

static bool startbildAktiv = false;   // Startbildschirm steht noch, kein Messwert darueber

// Kennzahlen des zuletzt gespeicherten Schlepps - sie stehen im Wartezustand
// auf dem Display, damit am Startplatz ohne Nachsehen klar ist, dass der
// Schlepp angekommen ist.
static uint32_t letzterNr = 0;
static float letzterVmax = 0.0f;
static float letzteDauer = 0.0f;

static void typMerken(const String& id, const String& typ) {
    for (int i = 0; i < TYP_MAX; i++) {
        if (typId[i] == id) { typWert[i] = typ; return; }
    }
    typId[typNaechster]   = id;
    typWert[typNaechster] = typ;
    typNaechster = (typNaechster + 1) % TYP_MAX;
}

static String typLesen(const String& id) {
    for (int i = 0; i < TYP_MAX; i++) {
        if (typId[i] == id) return typWert[i];
    }
    return "?";
}

static String laufzeit() {
    unsigned long s = millis() / 1000;
    char buf[9];
    snprintf(buf, sizeof(buf), "%02lu:%02lu:%02lu", s / 3600, (s % 3600) / 60, s % 60);
    return String(buf);
}

// Karte (neu) einhaengen. Das vorherige end() ist noetig, damit eine gezogene
// und wieder gesteckte Karte erkannt wird - sonst haelt die Bibliothek am
// alten Zustand fest.
static bool sdEinhaengen() {
    SD.end();
    sdBereit = SD.begin(SD_CS, sdSpi) && SD.cardType() != CARD_NONE;
    sdGeprueft = millis();
    return sdBereit;
}

// Unten rechts: sonst Feldstaerke und Kartenzustand, nach einem Schlepp fuer
// ein paar Sekunden die Rueckmeldung, ob er gespeichert wurde.
static void infoText(char* buf, size_t n, int rssi) {
    if (hinweisAktiv && millis() - hinweisSeit < HINWEIS_MS) {
        snprintf(buf, n, "%s", hinweis);
        return;
    }
    hinweisAktiv = false;
#if MIT_WLAN
    if (wlanAn) {
        // W und Anzahl der verbundenen Handys
        snprintf(buf, n, "%d W%d %s", rssi, WiFi.softAPgetStationNum(), sdBereit ? "SD" : "--");
        return;
    }
    snprintf(buf, n, "%d %s", rssi, sdBereit ? "SD" : "--");
#else
    snprintf(buf, n, "%d %s", rssi, sdBereit ? "SD" : "--");
#endif
}

static void showDisplay(float speed, int rssi) {
    startbildAktiv = false;
    char speedStr[8];
    snprintf(speedStr, sizeof(speedStr), "%.0f", speed);

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_logisoso62_tn);
    u8g2.drawStr(0, 64, speedStr);
    u8g2.setFont(u8g2_font_5x7_tr);
    char info[24];
    infoText(info, sizeof(info), rssi);
    u8g2.drawStr(128 - u8g2.getStrWidth(info), 64, info);
    // Flugzeugtyp klein oben rechts. Ab 100 km/h brauchen die Ziffern die volle
    // Displaybreite; danach bleibt der Typ ausgeblendet, bis der Sender neu
    // startet und ihn erneut meldet (dann ggf. mit einem anderen Typ).
    if (strlen(speedStr) > 2) typAusgeblendet = true;
    if (!typAusgeblendet) {
        u8g2.drawStr(128 - u8g2.getStrWidth(flugzeugTyp.c_str()), 7, flugzeugTyp.c_str());
    }
    u8g2.sendBuffer();
}

// Kein Sender zu hoeren. Bewusst ohne grosse Ziffer: eine 0 hiesse "Flugzeug
// steht", und genau das soll sich vom Wartezustand unterscheiden.
static void zeigeWarten() {
    startbildAktiv = false;
    char zeile[28];

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 16, "BEREIT");
    u8g2.setFont(u8g2_font_5x7_tr);
    if (letzterNr) {
        snprintf(zeile, sizeof(zeile), "zuletzt #%lu", (unsigned long)letzterNr);
        u8g2.drawStr(0, 30, zeile);
        snprintf(zeile, sizeof(zeile), "%.0f km/h max, %.0f s", letzterVmax, letzteDauer);
        u8g2.drawStr(0, 40, zeile);
    } else {
        u8g2.drawStr(0, 30, "warte auf Sender");
    }

    // Unten rechts: Rueckmeldung zum letzten Schlepp, sonst Karte und Handys
    char rechts[24];
    if (hinweisAktiv && millis() - hinweisSeit < HINWEIS_MS) {
        snprintf(rechts, sizeof(rechts), "%s", hinweis);
    } else {
        hinweisAktiv = false;
#if MIT_WLAN
        if (wlanAn)
            snprintf(rechts, sizeof(rechts), "W%d %s",
                     WiFi.softAPgetStationNum(), sdBereit ? "SD" : "--");
        else
#endif
            snprintf(rechts, sizeof(rechts), "%s", sdBereit ? "SD" : "--");
    }
    int breiteRechts = u8g2.getStrWidth(rechts);
    u8g2.drawStr(128 - breiteRechts, 62, rechts);

    snprintf(zeile, sizeof(zeile), "naechster #%lu", (unsigned long)naechsteNr);
    if (u8g2.getStrWidth(zeile) + breiteRechts + 6 <= 128) u8g2.drawStr(0, 62, zeile);
    u8g2.sendBuffer();
}

static void setzeHinweis(const char* text) {
    snprintf(hinweis, sizeof(hinweis), "%s", text);
    hinweisSeit  = millis();
    hinweisAktiv = true;
}

// --- Schlepp aufzeichnen ----------------------------------------------------

static void schleppBeginnen() {
    probenAnzahl  = 0;
    probenGesamt  = 0;
    vMax          = 0.0f;
    vSumme        = 0.0f;
    schleppStart  = millis();
    schleppLetzte = schleppStart;
    schleppBeginn = laufzeit();
    hinweisAktiv  = false;
}

static void schleppProbe(float v, int rssi) {
    unsigned long jetzt = millis();
    schleppLetzte = jetzt;
    probenGesamt++;
    vSumme += v;
    if (v > vMax) vMax = v;
    if (probenAnzahl < PROBEN_MAX) {
        Probe& p = proben[probenAnzahl++];
        p.t_ds   = (uint16_t)((jetzt - schleppStart) / 100);
        p.v_dkmh = (uint16_t)(v * 10.0f + 0.5f);
        p.rssi   = (int16_t)rssi;
    }
}

// Schreibt den fertigen Schlepp. Beide Dateien werden nach dem Schreiben
// geschlossen: zwischen zwei Schlepps ist nichts offen, die Karte darf dann
// jederzeit gezogen werden.
static bool schleppSchreibenGesperrt(uint32_t nr, const String& id, const String& typ) {
    if (!sdBereit && !sdEinhaengen()) return false;

    float dauer   = (schleppLetzte - schleppStart) / 1000.0f;
    float schnitt = probenGesamt ? vSumme / probenGesamt : 0.0f;

    bool neu = !SD.exists(DATEI_SCHLEPPS);
    File f = SD.open(DATEI_SCHLEPPS, FILE_APPEND);
    if (!f) { sdBereit = false; return false; }
    size_t geschrieben = 0;
    if (neu) geschrieben += f.print("nr;betriebszeit;kennung;typ;dauer_s;v_max;v_schnitt\n");
    geschrieben += f.printf("%lu;%s;%s;%s;%.0f;%.0f;%.0f\n",
                            (unsigned long)nr, schleppBeginn.c_str(), id.c_str(), typ.c_str(),
                            dauer, vMax, schnitt);
    f.close();
    if (geschrieben == 0) { sdBereit = false; return false; }

    neu = !SD.exists(DATEI_VERLAUF);
    f = SD.open(DATEI_VERLAUF, FILE_APPEND);
    if (!f) { sdBereit = false; return false; }
    if (neu) f.print("nr;t_s;kennung;typ;v_kmh;rssi\n");
    for (uint16_t i = 0; i < probenAnzahl; i++) {
        const Probe& p = proben[i];
        f.printf("%lu;%u.%u;%s;%s;%u.%u;%d\n",
                 (unsigned long)nr, p.t_ds / 10, p.t_ds % 10, id.c_str(), typ.c_str(),
                 p.v_dkmh / 10, p.v_dkmh % 10, p.rssi);
    }
    f.close();
    return true;
}

// Laeuft gerade ein Download, wartet das Schreiben, bis er fertig ist. Das ist
// unkritisch: der Schlepp ist in diesem Moment schon zu Ende.
static bool schleppSchreiben(uint32_t nr, const String& id, const String& typ) {
    xSemaphoreTake(sdSperre, portMAX_DELAY);
    bool ok = schleppSchreibenGesperrt(nr, id, typ);
    xSemaphoreGive(sdSperre);
    return ok;
}

static void schleppBeenden(const String& id) {
    // Der Typ kann waehrend des Schlepps noch nachgeliefert worden sein -
    // deshalb erst jetzt festlegen, nicht schon beim Einrasten.
    String typ = flugzeugTyp;
    uint32_t nr = naechsteNr;
    bool ok = schleppSchreiben(nr, id, typ);

    char text[24];
    if (ok) {
        naechsteNr  = nr + 1;
        letzterNr   = nr;
        letzterVmax = vMax;
        letzteDauer = (schleppLetzte - schleppStart) / 1000.0f;
        prefs.putUInt("nr", naechsteNr);
        snprintf(text, sizeof(text), "#%lu gespeichert", (unsigned long)nr);
        Serial.printf("Schlepp %lu gespeichert: %s %s, %.0f s, max %.0f km/h, %u Werte%s\n",
                      (unsigned long)nr, id.c_str(), typ.c_str(),
                      (schleppLetzte - schleppStart) / 1000.0f, vMax, probenAnzahl,
                      probenGesamt > probenAnzahl ? " (Verlauf gekuerzt)" : "");
    } else {
        snprintf(text, sizeof(text), "keine SD-Karte");
        Serial.println("Schlepp NICHT gespeichert - keine Karte");
    }
    setzeHinweis(text);
    zeigeWarten();
}

// --- Simulation -------------------------------------------------------------
// Ersetzt den Sender, wenn keiner erreichbar ist. Die Pakete gehen durch
// verarbeitePaket() - denselben Weg wie echte Funkpakete -, geprueft wird also
// alles ausser dem Funk selbst. Kennung SIM1, damit simulierte Schlepps in den
// Dateien nie mit echten verwechselt werden.
#define SIM_ID "SIM1"

static void verarbeitePaket(const String& empfangen, int rssi);

static char simArt = 0;               // 0 = aus, 'x' = voller Schlepp, 'a' = Startabbruch
static unsigned long simStart = 0;
static unsigned long simLetzte = 0;

static void simStarten(char art) {
    typMerken(SIM_ID, "ASK21");       // wie eine quittierte Typmeldung, ohne Funk
    simArt    = art;
    simStart  = millis();
    simLetzte = 0;
    Serial.printf("Simulation %s gestartet\n", art == 'x' ? "Schlepp" : "Startabbruch");
}

// Ein Paket alle 100 ms, wie der Sender im Schlepp
static void simSchritt() {
    if (!simArt || millis() - simLetzte < 100) return;
    simLetzte = millis();
    float t = (millis() - simStart) / 1000.0f;
    float v;

    if (simArt == 'x') {
        // Anrollen, Beschleunigen auf gut 100 km/h, Schleppflug mit Boeen.
        // Der echte Sender schlaeft 50 s nach dem Ueberschreiten von 20 km/h
        // ein - das ist hier bei 2,2 s -, danach kommt nichts mehr.
        if (t >= 52.2f) {
            simArt = 0;
            Serial.println("Simulation: Sender schlaeft ein");
            return;
        }
        if (t < 1.0f)       v = 0.0f;
        else if (t < 7.0f)  v = 100.0f * (t - 1.0f) / 6.0f;
        else if (t < 10.0f) v = 100.0f + 10.0f * (t - 7.0f) / 3.0f;
        else                v = 110.0f + 6.0f * sinf(0.7f * t) + 3.0f * sinf(1.9f * t)
                                 + (random(-15, 16) / 10.0f);
    } else {
        // Startabbruch: auf 45 km/h, dann ausrollen, der Sender bleibt wach
        if (t >= 15.0f) {
            simArt = 0;
            Serial.println("Simulation beendet");
            return;
        }
        if (t < 1.0f)      v = 0.0f;
        else if (t < 5.0f) v = 45.0f * (t - 1.0f) / 4.0f;
        else if (t < 9.0f) v = 45.0f * (9.0f - t) / 4.0f;
        else               v = 0.0f;
    }
    char paket[24];
    snprintf(paket, sizeof(paket), "%s:%.1f", SIM_ID, v);
    verarbeitePaket(String(paket), -60);
}

// --- Auslesen ueber USB -----------------------------------------------------
// Ein Zeichen im seriellen Monitor genuegt: l = Dateien, s = schlepps.csv,
// v = verlauf.csv. So lassen sich die Schlepps holen, ohne die Karte zu ziehen.
// Dazu x / a = Schlepp / Startabbruch simulieren, n = Zaehler auf 1 setzen.

static void dateiZeigen(const char* name) {
    File f = SD.open(name, FILE_READ);
    if (!f) { Serial.printf("%s fehlt\n", name); return; }
    Serial.printf("--- %s (%u Byte) ---\n", name, (unsigned)f.size());
    uint8_t puffer[256];
    size_t n;
    while ((n = f.read(puffer, sizeof(puffer))) > 0) Serial.write(puffer, n);
    f.close();
    Serial.printf("--- Ende %s ---\n", name);
}

static void befehlGesperrt(char c);

static void befehl(char c) {
    // Die Simulation braucht keine Karte - ohne Karte prueft sie gerade den
    // Fall "keine SD-Karte".
    if (c == 'x' || c == 'a') {
        if (simArt) Serial.println("Simulation laeuft bereits");
        else simStarten(c);
        return;
    }
    if (!sdBereit) { Serial.println("keine SD-Karte"); return; }
    if (xSemaphoreTake(sdSperre, pdMS_TO_TICKS(3000)) != pdTRUE) {
        Serial.println("SD-Karte gerade belegt (Download?) - bitte gleich noch einmal");
        return;
    }
    befehlGesperrt(c);
    xSemaphoreGive(sdSperre);
}

static void befehlGesperrt(char c) {
    switch (c) {
        case 'n':
            // Nur auf einer Karte ohne Schlepps: sonst gaebe es doppelte Nummern
            if (SD.exists(DATEI_SCHLEPPS)) {
                Serial.println("Zaehler NICHT zurueckgesetzt - erst schlepps.csv und verlauf.csv entfernen");
            } else {
                naechsteNr = 1;
                prefs.putUInt("nr", naechsteNr);
                Serial.println("Zaehler zurueckgesetzt, naechster Schlepp #1");
            }
            break;
        case 'l': {
            File wurzel = SD.open("/");
            Serial.printf("--- Karte: %llu MB frei von %llu MB, naechster Schlepp #%lu ---\n",
                          (SD.totalBytes() - SD.usedBytes()) >> 20, SD.totalBytes() >> 20,
                          (unsigned long)naechsteNr);
            for (File e = wurzel.openNextFile(); e; e = wurzel.openNextFile()) {
                Serial.printf("%-24s %8u Byte\n", e.name(), (unsigned)e.size());
                e.close();
            }
            wurzel.close();
            Serial.println("--- Ende Liste ---");
            break;
        }
        case 's': dateiZeigen(DATEI_SCHLEPPS); break;
        case 'v': dateiZeigen(DATEI_VERLAUF);  break;
        default:  Serial.println("Befehle: l = Dateien, s = schlepps.csv, v = verlauf.csv, "
                                 "x = Schlepp simulieren, a = Startabbruch simulieren, "
                                 "n = Zaehler zuruecksetzen");
    }
}

// --- Ausgabe und Funk -------------------------------------------------------

#if MIT_WLAN
static void webStarten();

// Doppel-Reset schaltet das WLAN um. Der Reset-Taster zieht EN und schaltet den
// Chip dabei komplett ab - der RTC-Speicher ueberlebt das nicht, deshalb liegt
// die Marke im NVS. Aus demselben Grund wirkt zweimal kurz Aus-und-Ein am
// ON/OFF-Schalter genauso.
static bool doppelResetPruefen() {
    wlanAn = prefs.getBool("wlan", true);
    if (prefs.getBool("marke", false)) {
        wlanAn = !wlanAn;
        prefs.putBool("wlan", wlanAn);
        prefs.putBool("marke", false);
        return true;
    }
    prefs.putBool("marke", true);
    markeGesetzt    = millis();
    resetMarkeOffen = true;           // wird in loop() nach DOPPEL_RESET_MS geloescht
    return false;
}
#endif

// Startbildschirm. Solange das Doppel-Reset-Fenster offen ist, steht unten die
// Aufforderung - so sieht man, wann ein zweiter Reset zaehlt. frueh: vor dem
// Einhaengen der Karte, deren Zustand ist dann noch unbekannt.
static void startbild(bool frueh) {
    char zeile[32];
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 16, "Schlepp");
    u8g2.setFont(u8g2_font_5x7_tr);
    if (!frueh) {
        snprintf(zeile, sizeof(zeile), "SD-Karte: %s", sdBereit ? "ok" : "fehlt");
        u8g2.drawStr(0, 30, zeile);
        snprintf(zeile, sizeof(zeile), "naechster Schlepp: #%lu", (unsigned long)naechsteNr);
        u8g2.drawStr(0, 40, zeile);
    }
#if MIT_WLAN
    if (wlanAn && !frueh) snprintf(zeile, sizeof(zeile), "WLAN: an %s", WiFi.softAPIP().toString().c_str());
    else                  snprintf(zeile, sizeof(zeile), "WLAN: %s", wlanAn ? "an" : "aus");
    u8g2.drawStr(0, 50, zeile);
    if (resetMarkeOffen) {
        u8g2.setFont(u8g2_font_6x10_tr);
        u8g2.drawStr(0, 63, wlanAn ? "Reset jetzt: WLAN aus" : "Reset jetzt: WLAN an");
    } else
#endif
    u8g2.drawStr(0, 60, "Warte LoRa...");
    u8g2.sendBuffer();
    startbildAktiv = true;
}

void setup() {
    Serial.begin(115200);

    Wire.begin(OLED_SDA, OLED_SCL);
    u8g2.begin();

    sdSperre = xSemaphoreCreateMutex();
    prefs.begin("empfaenger", false);
    naechsteNr = prefs.getUInt("nr", 1);

#if MIT_WLAN
    if (doppelResetPruefen()) {
        // Umschalten deutlich quittieren - sonst ist unklar, ob der zweite
        // Reset gezaehlt hat
        u8g2.clearBuffer();
        u8g2.setFont(u8g2_font_helvB14_tr);
        u8g2.drawStr(0, 30, wlanAn ? "WLAN AN" : "WLAN AUS");
        u8g2.setFont(u8g2_font_5x7_tr);
        u8g2.drawStr(0, 54, "2x Reset = umschalten");
        u8g2.sendBuffer();
        Serial.printf("Doppel-Reset: WLAN jetzt %s\n", wlanAn ? "an" : "aus");
        delay(2000);
    } else {
        // Sofort zeigen, dass das Fenster offen ist - Karte und WLAN brauchen
        // noch eine Weile, so lange soll niemand warten muessen
        startbild(true);
    }
#endif

    sdSpi.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
    sdEinhaengen();

#if MIT_WLAN
    if (wlanAn) {
        WiFi.softAP(AP_SSID, AP_PASS);
        webStarten();
#if MIT_UDP_PI
        udp.begin(UDP_PORT);
#endif
    } else {
        WiFi.mode(WIFI_OFF);
    }
#endif

    startbild(false);

    SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(LORA_FREQ)) {
        u8g2.clearBuffer();
        u8g2.setFont(u8g2_font_helvB14_tr);
        u8g2.drawStr(0, 32, "LoRa ERR");
        u8g2.sendBuffer();
        Serial.println("LoRa Fehler");
        while (true) { delay(1000); }
    }
    // Muss zum Sender passen: eigenes Sync-Wort haelt fremden Funk schon im
    // Chip fern, CRC verwirft verstuemmelte Pakete, statt Rauschen als
    // Geschwindigkeit durchzureichen.
    LoRa.setSyncWord(LORA_SYNC);
    LoRa.enableCrc();
#if MIT_WLAN
    if (wlanAn) Serial.printf("WLAN an: %s, http://%s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
    else        Serial.println("WLAN aus");
#endif
    Serial.printf("SD-Karte: %s, naechster Schlepp #%lu\n",
                  sdBereit ? "ok" : "fehlt", (unsigned long)naechsteNr);
    Serial.println("Empfaenger bereit");
}

static float smoothedSpeed = 0.0f;

// Eine Messzeile an das USB-Kabel, fuer den Pi auf Wunsch auch als Rundsendung.
static void meldung(float speed, int rssi, const String& id) {
    char msg[112];
    snprintf(msg, sizeof(msg), "[%s] V:%.1f T:0.0 RSSI:%d TYP:%s ID:%s\n",
             laufzeit().c_str(), speed, rssi, flugzeugTyp.c_str(), id.c_str());
    Serial.print(msg);

#if MIT_WLAN && MIT_UDP_PI
    if (wlanAn) {
        IPAddress bcast = WiFi.softAPIP();
        bcast[3] = 255;
        udp.beginPacket(bcast, UDP_PORT);
        udp.write((uint8_t*)msg, strlen(msg));
        udp.endPacket();
    }
#endif
}

// Typ-Paket "T:A3F1:ASK21" -> merken und quittieren. Quittiert wird jedes Mal,
// falls die Quittung verloren geht, und fuer jeden Sender - auch fuer wartende.
// Sonst wiederholen die ihren Typ endlos und belegen den Kanal.
static void behandleTyp(const String& empfangen) {
    int trenner = empfangen.indexOf(':', 2);
    if (trenner < 0) return;
    String id  = empfangen.substring(2, trenner);
    String typ = empfangen.substring(trenner + 1);
    if (id.length() == 0 || typ.length() == 0) return;

    typMerken(id, typ);
    LoRa.beginPacket();
    LoRa.print("A:");
    LoRa.print(id);
    LoRa.endPacket();

    // Anzeige nur aendern, wenn der Sender auch der angezeigte ist
    if (lockId.length() == 0 || lockId == id) {
        flugzeugTyp = typ;
        typAusgeblendet = false;   // Sender neu gestartet -> Typ wieder zeigen
    }
    Serial.printf("Flugzeugtyp %s von %s (quittiert)\n", typ.c_str(), id.c_str());
}

// Darf dieses Paket die Anzeige speisen? Rastet nebenbei auf den Sender ein,
// der ueber LOCK_SPEED kommt - das ist der, der gerade wirklich losrollt.
static bool paketAnnehmen(const String& id, float raw) {
    if (lockId.length()) {
        if (id != lockId) return false;        // anderes Flugzeug
        lockPaket = millis();
        if (raw < LOCK_FREI_KMH) {
            if (unterSchwelle == 0) unterSchwelle = millis();
        } else {
            unterSchwelle = 0;
        }
        return true;
    }

    if (raw > LOCK_SPEED) {
        lockId    = id;
        lockPaket = millis();
        unterSchwelle = 0;
        flugzeugTyp = typLesen(id);
        typAusgeblendet = false;
        smoothedSpeed = raw;      // sauber bei der echten Geschwindigkeit anfangen
        schleppBeginnen();
        Serial.printf("eingerastet auf %s (%s)\n", id.c_str(), flugzeugTyp.c_str());
        return true;
    }

    // Noch rollt niemand ueber der Schwelle: dann speist der schnellste Sender
    // die Anzeige. Nicht der lauteste - beim Anrollen von 0 auf 20 km/h wuerde
    // sonst ein danebenstehendes, wartendes Flugzeug die Anzeige auf 0 halten,
    // und sie spraenge erst beim Einrasten hoch.
    if (id == vorschauId) {
        vorschauSpeed = raw;
        vorschauZeit  = millis();
        return true;
    }
    // Der Vorrang verfaellt, sonst kaeme ein abgeschalteter Sender nie wieder
    // aus der Anzeige heraus.
    if (raw > vorschauSpeed || millis() - vorschauZeit > VORSCHAU_MS) {
        vorschauId    = id;
        vorschauSpeed = raw;
        vorschauZeit  = millis();
        return true;
    }
    return false;
}

#if MIT_WLAN
// --- Webseite fuers Handy ---------------------------------------------------
// Uebernommen vom Banana Pro: gleicher Tachobogen, gleiche Zonen, weisser
// Soll-Zeiger. Statt der Dateiliste eine Schlepp-Liste aus schlepps.csv.

static const char SEITE[] PROGMEM = R"rawliteral(<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Schlepp">
<title>Schlepp</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; -webkit-text-size-adjust: 100%; }
  body { margin: 0; padding: 12px env(safe-area-inset-right) 24px env(safe-area-inset-left);
         background: #000; color: #fff;
         font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; }
  .tacho { position: relative; max-width: 480px; margin: 0 auto; }
  svg { display: block; width: 100%; height: auto; }
  .werte { position: absolute; left: 0; right: 0; bottom: 4%; text-align: center; }
  .v { font-size: 22vw; font-weight: 700; line-height: .9; font-variant-numeric: tabular-nums; }
  @media (min-width: 480px) { .v { font-size: 106px; } }
  .einheit { font-size: 1.3rem; font-weight: 700; opacity: .85; }
  .kopf { max-width: 480px; margin: 0 auto 4px; padding: 0 12px; display: flex;
          justify-content: space-between; align-items: baseline; font-size: .95rem; }
  .typ { color: #b4b4b4; font-weight: 700; letter-spacing: .05em; }
  .status { color: #b4b4b4; font-variant-numeric: tabular-nums; }
  .status.weg { color: #ff3c3c; }
  .block { max-width: 480px; margin: 24px auto 0; padding: 14px 12px 0;
           border-top: 1px solid #333; }
  .block h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .1em;
              color: #888; font-weight: 600; margin: 0 0 10px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th { text-align: right; color: #888; font-weight: 600; font-size: .75rem;
       padding: 0 6px 6px; }
  td { text-align: right; padding: 8px 6px; border-top: 1px solid #222; }
  th:nth-child(2), td:nth-child(2) { text-align: left; }
  .knoepfe { display: flex; gap: 10px; margin-top: 14px; }
  a.datei { flex: 1; text-align: center; padding: 14px 10px; border-radius: 12px;
            background: #141414; border: 1px solid #2a2a2a; color: #fff;
            text-decoration: none; font-weight: 600; }
  a.datei:active { background: #222; }
  .hinweis { color: #888; font-size: .85rem; }
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
  <div class="werte"><div class="v" id="v">&ndash;</div><div class="einheit" id="einheit">bereit</div></div>
</div>

<div class="block"><h2>Schlepps</h2><div id="liste"></div>
  <div class="knoepfe" id="knoepfe" hidden>
    <a class="datei" href="/schlepps.csv" download="schlepps.csv">schlepps.csv</a>
    <a class="datei" href="/verlauf.csv" download="verlauf.csv">verlauf.csv</a>
  </div>
</div>

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
  const RA = R + 17;
  const [sx, sy] = [CX + (RA + 2) * c,  CY + (RA + 2) * s];
  const [bx, by] = [CX + (RA + 15) * c, CY + (RA + 15) * s];
  el.setAttribute("points",
    `${sx.toFixed(1)},${sy.toFixed(1)} `
  + `${(bx + px * 7).toFixed(1)},${(by + py * 7).toFixed(1)} `
  + `${(bx - px * 7).toFixed(1)},${(by - py * 7).toFixed(1)}`);
}

// v === null: kein Sender zu hoeren. Dann bleibt der Bogen leer und es steht
// ein Strich da - eine 0 hiesse "Flugzeug steht".
function zeichne(v) {
  const leer = (v === null);
  ZONEN.forEach((z, i) =>
    fuellungen[i].setAttribute("d", leer ? "" : bogen(z[0], Math.min(v, z[1]))));
  document.getElementById("v").textContent = leer ? "–" : Math.round(v);
  document.getElementById("einheit").textContent = leer ? "bereit" : "km/h";
}

// Schlepp-Liste: nur neu laden, wenn sich Nummer oder Karte geaendert haben.
// Waehrend eines Schlepps verweigert der Empfaenger die Datei (503) - dann
// bleibt die alte Liste einfach stehen.
let listenStand = "";
async function liste(d) {
  const stand = d.sd + ":" + d.nr;
  if (stand === listenStand || d.laeuft) return;
  const el = document.getElementById("liste");
  const knoepfe = document.getElementById("knoepfe");
  if (!d.sd) {
    listenStand = stand;
    el.innerHTML = '<div class="hinweis">Keine SD-Karte im Empfänger.</div>';
    knoepfe.hidden = true;
    return;
  }
  try {
    const r = await fetch("/schlepps.csv", { cache: "no-store" });
    if (r.status === 503) return;
    listenStand = stand;
    if (!r.ok) {
      el.innerHTML = '<div class="hinweis">Noch keine Schlepps aufgezeichnet.</div>';
      knoepfe.hidden = true;
      return;
    }
    const zeilen = (await r.text()).trim().split("\n").slice(1)
                     .map(z => z.split(";")).filter(f => f.length >= 7);
    const tab = document.createElement("table");
    tab.innerHTML = "<tr><th>Nr</th><th>Typ</th><th>Dauer</th><th>max</th><th>Ø</th></tr>";
    zeilen.slice(-15).reverse().forEach(f => {
      const tr = document.createElement("tr");
      [f[0], f[3] + (f[2].startsWith("SIM") ? " (Sim)" : ""), f[4] + " s", f[5], f[6]]
        .forEach(w => { const td = document.createElement("td"); td.textContent = w; tr.appendChild(td); });
      tab.appendChild(tr);
    });
    el.innerHTML = "";
    el.appendChild(tab);
    knoepfe.hidden = false;
  } catch (e) {}
}

async function tick() {
  const st = document.getElementById("status");
  try {
    const r = await fetch("/api", { cache: "no-store" });
    const d = await r.json();
    zeichne(d.sender ? d.v : null);
    zeiger(d.soll);
    document.getElementById("typ").textContent = d.typ && d.typ !== "?" ? d.typ : "";
    st.textContent = d.sender ? d.id + " · " + d.rssi + " dBm" : "kein Sender";
    st.classList.toggle("weg", !d.sender);
    liste(d);
  } catch (e) {
    st.textContent = "Empfänger nicht erreichbar"; st.classList.add("weg");
  }
}
tick();
setInterval(tick, 250);
</script></body></html>)rawliteral";

// Nur Buchstaben, Ziffern, Minus und Unterstrich in die JSON-Antwort: Typ und
// Kennung kommen vom Funk, ein Anfuehrungszeichen darin wuerde sie zerbrechen.
static void kopiereSauber(char* ziel, size_t n, const String& s) {
    size_t i = 0;
    for (; i < s.length() && i + 1 < n; i++) {
        char c = s[i];
        ziel[i] = (isalnum((unsigned char)c) || c == '-' || c == '_') ? c : '?';
    }
    ziel[i] = '\0';
}

// Aus loop() heraus: die Werte fuer den Webserver festhalten. Alle 50 ms
// genuegt - das Handy fragt alle 250 ms.
static void momentAktualisieren() {
    static unsigned long zuletzt = 0;
    if (millis() - zuletzt < 50) return;
    zuletzt = millis();
    Momentaufnahme m;
    m.v      = smoothedSpeed;
    m.rssi   = letzterRssi;
    m.sender = !aufNull;
    m.laeuft = lockId.length() > 0;
    m.sd     = sdBereit;
    m.nr     = naechsteNr;
    kopiereSauber(m.typ, sizeof(m.typ), flugzeugTyp);
    kopiereSauber(m.id,  sizeof(m.id),  letzteId);
    portENTER_CRITICAL(&momentSperre);
    moment = m;
    portEXIT_CRITICAL(&momentSperre);
}

static Momentaufnahme momentLesen() {
    portENTER_CRITICAL(&momentSperre);
    Momentaufnahme m = moment;
    portEXIT_CRITICAL(&momentSperre);
    return m;
}

static void webSeite() {
    web.send_P(200, "text/html; charset=utf-8", SEITE);
}

static void webApi() {
    Momentaufnahme m = momentLesen();
    char soll[12] = "null";
    if (m.laeuft) {
        for (const Soll& s : SOLL_KMH) {
            if (strcmp(m.typ, s.typ) == 0) snprintf(soll, sizeof(soll), "%.0f", s.kmh);
        }
    }
    char json[200];
    snprintf(json, sizeof(json),
             "{\"v\":%.1f,\"typ\":\"%s\",\"id\":\"%s\",\"rssi\":%d,\"sender\":%s,"
             "\"soll\":%s,\"laeuft\":%s,\"sd\":%s,\"nr\":%lu}",
             m.v, m.typ, m.id, m.rssi, m.sender ? "true" : "false", soll,
             m.laeuft ? "true" : "false", m.sd ? "true" : "false", (unsigned long)m.nr);
    web.sendHeader("Cache-Control", "no-store");
    web.send(200, "application/json", json);
}

// Download - auch waehrend eines Schlepps moeglich, der Empfang laeuft auf dem
// anderen Kern weiter. Endet ein Schlepp mitten im Download, wartet dessen
// Schreiben an der Sperre, bis die Datei raus ist.
static void webDatei(const char* name) {
    if (!momentLesen().sd) { web.send(404, "text/plain; charset=utf-8", "keine SD-Karte"); return; }
    if (xSemaphoreTake(sdSperre, pdMS_TO_TICKS(5000)) != pdTRUE) {
        web.send(503, "text/plain; charset=utf-8", "Karte gerade belegt - bitte gleich noch einmal");
        return;
    }
    File f = SD.open(name, FILE_READ);
    if (!f) {
        xSemaphoreGive(sdSperre);
        web.send(404, "text/plain; charset=utf-8", "noch keine Schlepps");
        return;
    }
    web.sendHeader("Content-Disposition", String("attachment; filename=") + (name + 1));
    web.sendHeader("Cache-Control", "no-store");
    web.streamFile(f, "text/csv");
    f.close();
    xSemaphoreGive(sdSperre);
}

static void webAufgabe(void*) {
    for (;;) {
        web.handleClient();
        vTaskDelay(pdMS_TO_TICKS(2));   // Luft fuer WLAN-Stack und Leerlauf-Watchdog
    }
}

static void webStarten() {
    web.on("/", HTTP_GET, webSeite);
    web.on("/api", HTTP_GET, webApi);
    web.on(DATEI_SCHLEPPS, HTTP_GET, []() { webDatei(DATEI_SCHLEPPS); });
    web.on(DATEI_VERLAUF,  HTTP_GET, []() { webDatei(DATEI_VERLAUF); });
    web.onNotFound([]() { web.sendHeader("Location", "/"); web.send(302, "text/plain", ""); });
    web.begin();
    // Kern 0: dort laeuft auch der WLAN-Stack. loop() bleibt auf Kern 1 allein.
    xTaskCreatePinnedToCore(webAufgabe, "web", 8192, nullptr, 1, nullptr, 0);
}
#endif

// Ein empfangenes Paket - vom Funk oder aus der Simulation
static void verarbeitePaket(const String& empfangen, int rssi) {
    if (empfangen.startsWith("T:")) {
        behandleTyp(empfangen);
        return;
    }

    // Geschwindigkeitspaket "A3F1:112.4". Ohne Kennung nicht verwertbar.
    int trenner = empfangen.indexOf(':');
    if (trenner <= 0) return;
    String id = empfangen.substring(0, trenner);
    float raw = empfangen.substring(trenner + 1).toFloat();
    if (raw < 0.0f || raw > 300.0f) return;   // unplausibel

    if (!paketAnnehmen(id, raw)) return;

    letztesPaket = millis();
    letzteId     = id;
    letzterRssi  = rssi;
    aufNull      = false;

    // Aufgezeichnet wird der Wert des Senders, nicht die Displayglaettung
    if (lockId.length()) schleppProbe(raw, rssi);

    smoothedSpeed = 0.16f * raw + 0.84f * smoothedSpeed;
    float speed = smoothedSpeed;

    showDisplay(speed, rssi);
    meldung(speed, rssi, id);
}

void loop() {
    int packetSize = LoRa.parsePacket();
    if (packetSize) {
        String empfangen = "";
        while (LoRa.available()) {
            empfangen += (char)LoRa.read();
        }
        verarbeitePaket(empfangen, LoRa.packetRssi());
    }
    simSchritt();

#if MIT_WLAN
    // Werte fuer den Webserver auf Kern 0 bereitstellen. Die Anfragen selbst
    // bearbeitet er dort - hier wird nie auf das Handy gewartet.
    if (wlanAn) momentAktualisieren();

    // Nach DOPPEL_RESET_MS ist der naechste Reset wieder ein gewoehnlicher -
    // die Aufforderung verschwindet dann auch vom Startbildschirm
    if (resetMarkeOffen && millis() - markeGesetzt >= DOPPEL_RESET_MS) {
        prefs.putBool("marke", false);
        resetMarkeOffen = false;
        if (startbildAktiv) startbild(false);
    }
#endif

    // Nichts mehr zu hoeren: auf 0 gehen, statt den letzten Wert einzufrieren.
    // Der Sender legt sich nach dem Schlepp schlafen - ohne das bliebe auf dem
    // Display die Endgeschwindigkeit stehen, als flöge das Flugzeug noch.
    if (!aufNull && millis() - letztesPaket >= STILLE_MS) {
        aufNull       = true;
        smoothedSpeed = 0.0f;
        zeigeWarten();
        meldung(0.0f, letzterRssi, letzteId);
    }

    // Bindung loesen: ausgerollt oder der Sender schlaeft. Muss ausserhalb des
    // Paketzweigs stehen - wenn der Sender schweigt, kommt hier nichts mehr an.
    // Das Loesen ist zugleich das Ende des Schlepps.
    if (lockId.length()) {
        bool still      = millis() - lockPaket >= LOCK_FREI_MS;
        bool ausgerollt = unterSchwelle && millis() - unterSchwelle >= LOCK_FREI_MS;
        if (still || ausgerollt) {
            Serial.printf("Bindung an %s geloest (%s)\n", lockId.c_str(),
                          still ? "keine Pakete" : "ausgerollt");
            String fertig = lockId;
            lockId        = "";
            lockPaket     = 0;
            unterSchwelle = 0;
            vorschauId    = "";
            vorschauSpeed = -1.0f;
            schleppBeenden(fertig);
        }
    }

    // Im Leerlauf nach der Karte sehen: erkennt eine nachtraeglich gesteckte
    // und eine gezogene Karte. Nur wenn seit STILLE_MS kein Sender zu hoeren
    // war - ohne Karte blockiert ein Einhaengeversuch rund 500 ms, und ein
    // wartender oder anrollender Sender wuerde in der Zeit Pakete verlieren.
    // Laeuft gerade ein Download, faellt die Pruefung einfach aus - nie warten.
    if (lockId.length() == 0 && aufNull && millis() - sdGeprueft >= SD_PRUEF_MS &&
        xSemaphoreTake(sdSperre, 0) == pdTRUE) {
        bool vorher = sdBereit;
        sdEinhaengen();
        xSemaphoreGive(sdSperre);
        if (sdBereit != vorher) {
            Serial.printf("SD-Karte %s\n", sdBereit ? "erkannt" : "entfernt");
            if (!startbildAktiv) zeigeWarten();
        }
    }

    // Warteanzeige auffrischen: nach 10 s verschwindet der Hinweis, und die Zahl
    // der verbundenen Handys aendert sich. Nie ueber dem Startbildschirm - dort
    // steht die Adresse fuers Handy.
    static unsigned long wartenGezeigt = 0;
    if (aufNull && !startbildAktiv && lockId.length() == 0 &&
        millis() - wartenGezeigt >= 2000) {
        wartenGezeigt = millis();
        zeigeWarten();
    }

    // Befehle ueber USB nur im Leerlauf: eine lange Datei auszugeben blockiert,
    // waehrend eines Schlepps gingen dabei Pakete verloren.
    if (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r' || c == ' ') return;
        if (lockId.length()) Serial.println("waehrend eines Schlepps keine Befehle");
        else befehl(c);
    }
}
