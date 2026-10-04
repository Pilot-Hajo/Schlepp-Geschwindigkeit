#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <Preferences.h>
#include "esp_sleep.h"

#define LORA_SCK    5
#define LORA_MISO   19
#define LORA_MOSI   27
#define LORA_SS     18
#define LORA_RST    14
#define LORA_DIO0   26

#define OLED_SDA    21
#define OLED_SCL    22

#define MCPH21_ADDR  0x7F

#define LORA_FREQ    868E6
#define LORA_SYNC    0x3C      // eigenes Sync-Wort; 0x12 ist der Werkswert aller Boards
#define WAKE_PIN     GPIO_NUM_0
#define START_SPEED  20.0f     // km/h – ab hier läuft die Zeit
#define SCHLEPP_MS   50000UL   // 50 Sekunden

#define FLUG_SPEED   70.0f     // darueber sind wir in der Luft -> nicht senden
#define FLUGZEUGTYP  "ASK21"   // <<< Flugzeugtyp – hier aendern
#define TYP_SENDE_MS 1000UL    // Wiederholrate, solange unbestaetigt
#define TYP_ACK_MS   250UL     // Horchfenster direkt nach dem Typ-Paket

// Sendetakt. Gemessen wird immer mit 10 Hz - gefunkt wird im Wartezustand aber
// nur einmal pro Sekunde. Ein Paket belegt rund 31 ms Sendezeit; mit 10 Hz
// haelt ein einziger wartender Sender fast ein Drittel des Kanals besetzt und
// erschlaegt damit die Pakete des Flugzeugs, das gerade wirklich startet.
#define MESS_MS       100UL
#define SEND_AKTIV_MS 100UL
#define SEND_WARTE_MS 1000UL
// Zufall gegen dauerhaften Gleichtakt zweier wartender Sender - in ganzen
// Messtakten, sonst rutscht die Sendung ueber die naechste 100-ms-Grenze und
// der Takt halbiert sich still. Im Schlepp funkt ohnehin nur ein Geraet, dort
// bleibt es beim festen 10-Hz-Takt.
#define SEND_JITTER_N 4UL      // 0 bis 3 Messtakte, also 0 bis 300 ms
#define WARTE_MAX_MS  180000UL // 3 min ohne Bewegung -> in den Ruhetakt wechseln
#define BEWEGUNG_KMH  10.0f    // darueber gilt das Flugzeug als in Bewegung

// Testbetrieb seit 04.10.2026: Der Sender schlaeft NICHT mehr ein. Am Flugplatz
// kam keine Verbindung zustande, weil er nach drei Minuten abschaltete und der
// Reset-Taster im eingebauten Zustand nicht erreichbar ist. Statt zu schlafen
// funkt er weiter eine Geschwindigkeit je Minute und ist jederzeit bereit.
// Der hoehere Stromverbrauch ist dabei bewusst in Kauf genommen.
// Auf 1 setzen stellt das alte Verhalten wieder her (Stand: Tag
// "stand-mit-schlafmodus").
#define MIT_SCHLAFMODUS 0
#define SEND_RUHE_MS  60000UL  // Ruhetakt: ein Paket je Minute

enum State { WARTE, AKTIV, RUHE };

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);

static float mcph21_offset = 0;
static Preferences prefs;             // haelt den Nullpunkt ueber Reset hinweg
static State state = WARTE;
static unsigned long schlepp_start = 0;
static bool typ_bestaetigt = false;   // wird beim Neustart automatisch zurueckgesetzt
static char senderId[5] = "????";     // eindeutig je Board, 4 Hexziffern

// Kennung aus der Chip-Seriennummer. Es kann pro Flugzeug einen Sender geben,
// der Empfaenger muss sie auseinanderhalten koennen. Alle 48 MAC-Bits werden
// auf 16 gefaltet, damit sich nicht zufaellig zwei Boards nur in den oberen
// Bits unterscheiden.
static void kennungBilden() {
    uint64_t mac = ESP.getEfuseMac();
    uint16_t id = (uint16_t)mac ^ (uint16_t)(mac >> 16) ^ (uint16_t)(mac >> 32);
    snprintf(senderId, sizeof(senderId), "%04X", id);
}

static bool mcph21_read(uint32_t &raw_pres) {
    Wire.beginTransmission(MCPH21_ADDR);
    Wire.write(0x30);
    Wire.write(0x0A);
    if (Wire.endTransmission() != 0) return false;
    delay(5);

    for (int i = 0; i < 10; i++) {
        Wire.beginTransmission(MCPH21_ADDR);
        Wire.write(0x02);
        Wire.endTransmission(false);
        Wire.requestFrom((uint8_t)MCPH21_ADDR, (uint8_t)1);
        if (Wire.available() && (Wire.read() & 0x01)) break;
        delay(2);
    }

    Wire.beginTransmission(MCPH21_ADDR);
    Wire.write(0x06);
    Wire.endTransmission(false);
    Wire.requestFrom((uint8_t)MCPH21_ADDR, (uint8_t)3);
    if (Wire.available() < 3) return false;

    uint8_t p0 = Wire.read();
    uint8_t p1 = Wire.read();
    uint8_t p2 = Wire.read();
    raw_pres = ((uint32_t)p0 << 16) | ((uint32_t)p1 << 8) | p2;
    return true;
}

static void mcph21_calibrate() {
    double sum = 0;
    int count = 0;
    for (int i = 0; i < 50; i++) {
        uint32_t raw_p;
        if (mcph21_read(raw_p)) { sum += raw_p; count++; }
        delay(20);
    }
    if (count > 0) {
        mcph21_offset = (float)(sum / count);
        // Nullpunkt sichern: nach einem Reset in der Luft ist er die einzige
        // Moeglichkeit, ueberhaupt eine gueltige Geschwindigkeit zu berechnen.
        prefs.putFloat("offset", mcph21_offset);
    }
    Serial.printf("MCPH21 Offset: %.1f\n", mcph21_offset);
}

static float mcph21_speed_kmh() {
    uint32_t raw_p;
    if (!mcph21_read(raw_p)) return 0;
    float pa = (6.25f * ((float)raw_p - mcph21_offset) / 8388608.0f) * 1000.0f;
    if (pa < 0) pa = 0;
    return sqrtf(2.0f * pa / 1.225f) * 3.6f;
}

// Sendet den Flugzeugtyp als eigenes Paket: "T:A3F1:ASK21"
static void sendeTyp() {
    LoRa.beginPacket();
    LoRa.print("T:");
    LoRa.print(senderId);
    LoRa.print(":");
    LoRa.print(FLUGZEUGTYP);
    LoRa.endPacket();
}

// Horcht auf die Quittung des Empfaengers: "A:A3F1". Die Kennung muss darin
// stehen - sonst nimmt ein zweites Flugzeug die Bestaetigung fuer ein anderes
// als seine eigene und meldet seinen Typ nie wieder.
static void pruefeTypAck() {
    if (!LoRa.parsePacket()) return;

    String msg;
    while (LoRa.available()) msg += (char)LoRa.read();

    if (msg.startsWith("A:") && msg.substring(2) == senderId) {
        typ_bestaetigt = true;
        Serial.printf("Typ %s vom Empfaenger bestaetigt\n", FLUGZEUGTYP);
    }
}

// Nach dem Typ-Paket durchgehend horchen. Noetig, weil showDisplay() den
// Loop sonst zig Millisekunden blockiert und die Quittung verpasst wuerde.
static void warteAufAck(unsigned long dauer_ms) {
    unsigned long start = millis();
    while (!typ_bestaetigt && millis() - start < dauer_ms) {
        pruefeTypAck();
        yield();
    }
}

static void showDisplay(float speed, unsigned long restMs) {
    char speedStr[8];
    snprintf(speedStr, sizeof(speedStr), "%.0f", speed);

    char statusStr[10];
    if (state == RUHE) {
        // Ruhetakt: ein Paket je Minute, das Geraet bleibt wach und bereit
        snprintf(statusStr, sizeof(statusStr), "60s");
    } else if (state == WARTE) {
#if MIT_SCHLAFMODUS
        // Die letzte halbe Minute vor der Selbstabschaltung sichtbar machen -
        // sonst geht das Geraet dem Piloten wortlos aus.
        if (restMs <= 30000UL)
            snprintf(statusStr, sizeof(statusStr), "AUS %lus", restMs / 1000);
        else
#endif
            snprintf(statusStr, sizeof(statusStr), "<20");
    } else {
        int v = (int)((SCHLEPP_MS - (millis() - schlepp_start)) / 1000) + 1;
        snprintf(statusStr, sizeof(statusStr), "%ds", v);
    }

    u8g2.clearBuffer();
    // Geschwindigkeit maximal gross
    u8g2.setFont(u8g2_font_logisoso62_tn);
    u8g2.drawStr(0, 64, speedStr);
    // Status winzig unten rechts
    u8g2.setFont(u8g2_font_5x7_tr);
    u8g2.drawStr(128 - u8g2.getStrWidth(statusStr), 64, statusStr);
    // Flugzeugtyp klein oben rechts – wie beim Empfaenger: ab 100 km/h weg und
    // dann bis zum naechsten Neustart ausgeblendet.
    static bool typAusgeblendet = false;
    if (strlen(speedStr) > 2) typAusgeblendet = true;
    if (!typAusgeblendet) {
        u8g2.drawStr(128 - u8g2.getStrWidth(FLUGZEUGTYP), 7, FLUGZEUGTYP);
    }
    u8g2.sendBuffer();
}

// Pruefung direkt nach dem Start: sind wir schon in der Luft? Gerechnet wird
// mit dem gespeicherten Nullpunkt vom Boden - eine frische Kalibrierung waere
// hier wertlos, sie wuerde den Staudruck als Nullpunkt einlernen.
static bool istInFlug() {
    int treffer = 0;
    for (int i = 0; i < 10; i++) {
        if (mcph21_speed_kmh() > FLUG_SPEED) treffer++;
        delay(20);
    }
    return treffer >= 6;      // Mehrheit, damit ein einzelner Ausreisser nicht genuegt
}

// Zurueck in den Schlaf, ohne LoRa auch nur zu initialisieren - so ist sicher,
// dass dieses Geraet den Schlepp eines anderen Flugzeugs nicht stoert.
//
// Mit Ausweg: waere der gespeicherte Nullpunkt einmal falsch, haette sich das
// Geraet sonst bei jedem Start fuer fliegend gehalten und waere dauerhaft
// unbrauchbar. Ein Tastendruck im 3-Sekunden-Fenster erzwingt den Start. In
// der Luft drueckt niemand, dort schlaeft es also wie vorgesehen ein.
static bool startTrotzFlugerkennung() {
    Serial.println("Reset in der Luft erkannt - kein Senden");
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 20, "Im Flug:");
    u8g2.drawStr(0, 38, "kein Senden");
    u8g2.setFont(u8g2_font_5x7_tr);
    u8g2.drawStr(0, 60, "Taste = trotzdem starten");
    u8g2.sendBuffer();

    pinMode(WAKE_PIN, INPUT_PULLUP);
    unsigned long start = millis();
    while (millis() - start < 3000) {
        if (digitalRead(WAKE_PIN) == LOW) {
            Serial.println("Taste gedrueckt - Start wird erzwungen");
            return true;
        }
        delay(10);
    }

    Serial.println("zurueck in den Schlaf");
    u8g2.setPowerSave(1);
    esp_sleep_enable_ext0_wakeup(WAKE_PIN, 0);
    esp_deep_sleep_start();
    return false;                      // wird nie erreicht
}

#if MIT_SCHLAFMODUS
// Nur im alten Betrieb. Die Flugerkennung beim Start schlaeft weiterhin
// selbst, unabhaengig von diesem Schalter - sie hat ihren eigenen Weg.
static void goToSleep(const char* zeile1 = "Schlepp", const char* zeile2 = "beendet.") {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 24, zeile1);
    u8g2.drawStr(0, 42, zeile2);
    u8g2.sendBuffer();
    LoRa.end();
    delay(2000);
    u8g2.setPowerSave(1);
    esp_sleep_enable_ext0_wakeup(WAKE_PIN, 0);
    esp_deep_sleep_start();
}
#endif

void setup() {
    Serial.begin(115200);

    kennungBilden();
    randomSeed(esp_random());

    Wire.begin(OLED_SDA, OLED_SCL);
    u8g2.begin();

    // Zuerst pruefen, ob wir schon fliegen - noch bevor LoRa eingeschaltet wird.
    prefs.begin("schlepp", false);
    float alter_offset = prefs.getFloat("offset", 0.0f);
    if (alter_offset > 0.0f) {
        mcph21_offset = alter_offset;
        // kehrt nur zurueck, wenn der Nutzer den Start ausdruecklich erzwingt
        if (istInFlug()) startTrotzFlugerkennung();
    }

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 24, "Kalibriere");
    u8g2.drawStr(0, 42, "Pitot...");
    // Kennung und Typ dieses Geraets - der einzige Ort, an dem sie ablesbar
    // sind, ohne das Board an den Rechner zu haengen.
    u8g2.setFont(u8g2_font_5x7_tr);
    char kopf[24];
    snprintf(kopf, sizeof(kopf), "%s  %s", senderId, FLUGZEUGTYP);
    u8g2.drawStr(0, 60, kopf);
    u8g2.sendBuffer();

    mcph21_calibrate();

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
    // Eigenes Sync-Wort: der Funkchip meldet fremde Pakete dann gar nicht erst.
    // CRC: ohne sie reicht die Bibliothek verstuemmelte Pakete als Nutzdaten
    // durch - aus Rauschen wuerde eine Geschwindigkeit.
    LoRa.setSyncWord(LORA_SYNC);
    LoRa.enableCrc();

    Serial.printf("Sender bereit, Kennung %s, Typ %s\n", senderId, FLUGZEUGTYP);
}

void loop() {
    static unsigned long letzteMessung = 0;
    static unsigned long letzterFunk = 0;
    static unsigned long letzterTypSend = 0;
    static unsigned long jitter = 0;
    static unsigned long letzteBewegung = 0;
    static bool startfreigabe = true;
    static float smoothedSpeed = 0.0f;

    // Solange der Typ unbestaetigt ist: auf die Quittung horchen
    if (!typ_bestaetigt) pruefeTypAck();

    if (millis() - letzteMessung < MESS_MS) return;
    letzteMessung = millis();

    // Gemessen und geglaettet wird unabhaengig vom Sendetakt weiter mit 10 Hz,
    // sonst wuerde der Start um bis zu eine Sekunde zu spaet erkannt.
    float raw = mcph21_speed_kmh();
    smoothedSpeed = 0.08f * raw + 0.92f * smoothedSpeed;
    float speed = smoothedSpeed;
    Serial.printf("V:%.1f\n", speed);

    // Ein neuer Schlepp erst, wenn das Flugzeug zwischendurch gestanden hat.
    // Ohne diese Freigabe begaenne am Ende der 50 Sekunden sofort der naechste
    // Schlepp - das Flugzeug haengt zu diesem Zeitpunkt ja noch mit voller
    // Fahrt am Seil. Das Ergebnis waere Dauerfunk ueber den ganzen Flug und
    // eine Kette von Schein-Schlepps auf der Karte.
    if (speed < BEWEGUNG_KMH) startfreigabe = true;

    // Auch aus dem Ruhetakt heraus: der naechste Start wird ohne Neustart erkannt
    if (state != AKTIV && startfreigabe && speed > START_SPEED) {
        state = AKTIV;
        schlepp_start = millis();
        startfreigabe = false;
        Serial.println("Schlepp begonnen");
    }

    if (state == AKTIV && millis() - schlepp_start >= SCHLEPP_MS) {
#if MIT_SCHLAFMODUS
        goToSleep();
#else
        // Kein Schlaf: in den Ruhetakt. Die 60 s Pause bis zum naechsten Paket
        // sind laenger als die 3 s, nach denen der Empfaenger den Schlepp
        // abschliesst - er wird also sauber gespeichert.
        state = RUHE;
        letzteBewegung = millis();
        Serial.println("Schlepp beendet - weiter im Ruhetakt (60 s)");
#endif
    }

    // Vergessen eingeschaltet: nach WARTE_MAX_MS ohne Bewegung selbst schlafen
    // legen, sonst funkt das Geraet den ganzen Flugtag dazwischen und leert
    // nebenbei den Akku. Gezaehlt wird ab der letzten Bewegung, nicht ab dem
    // Einschalten - Rangieren und Anschleppen ans Seil halten es wach.
    if (speed > BEWEGUNG_KMH) letzteBewegung = millis();
    unsigned long ruht = millis() - letzteBewegung;
    if (state == WARTE && ruht >= WARTE_MAX_MS) {
#if MIT_SCHLAFMODUS
        goToSleep("Kein Start.", "Abschaltung.");
#else
        state = RUHE;
        Serial.println("kein Start - weiter im Ruhetakt (60 s)");
#endif
    }

    // Im Ruhetakt genuegt ein Paket je Minute. Solange der Typ aber noch nicht
    // quittiert ist, bleibt es beim Sekundentakt: ein spaeter eingeschalteter
    // Empfaenger soll die Verbindung in einer Sekunde haben und nicht in einer
    // Minute - genau daran ist es am Flugplatz gescheitert.
    unsigned long abstand;
    if (state == AKTIV)                       abstand = SEND_AKTIV_MS;
    else if (state == RUHE && typ_bestaetigt) abstand = SEND_RUHE_MS;
    else                                      abstand = SEND_WARTE_MS + jitter;
    if (millis() - letzterFunk >= abstand) {
        letzterFunk = millis();
        jitter = random(SEND_JITTER_N) * MESS_MS;

        // Lebenszeichen nur am Boden. Ueber START_SPEED wird entweder
        // geschleppt - dann ist der Zustand AKTIV - oder geflogen. Ein Paket
        // mit Flugfahrt wuerde den Empfaenger einrasten lassen und jede Minute
        // einen Schein-Schlepp aufzeichnen.
        if (state == RUHE && speed > START_SPEED) {
            // schweigen
        } else if (!typ_bestaetigt && millis() - letzterTypSend >= TYP_SENDE_MS) {
            // Typ-Paket ersetzt in diesem Takt das Speed-Paket. Direkt hinter
            // einem Speed-Paket wuerde es der Empfaenger verpassen, weil der
            // dann noch in Display-Update und UDP-Versand haengt.
            letzterTypSend = millis();
            sendeTyp();
            warteAufAck(TYP_ACK_MS);
            letzterFunk = millis();
        } else {
            char paket[20];
            snprintf(paket, sizeof(paket), "%s:%.1f", senderId, speed);
            LoRa.beginPacket();
            LoRa.print(paket);
            LoRa.endPacket();
        }
    }

    showDisplay(speed, WARTE_MAX_MS - ruht);
}
