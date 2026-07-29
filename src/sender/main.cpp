#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <U8g2lib.h>
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
#define WAKE_PIN     GPIO_NUM_0
#define START_SPEED  20.0f     // km/h – ab hier läuft die Zeit
#define SCHLEPP_MS   50000UL   // 50 Sekunden

enum State { WARTE, AKTIV };

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);

static float mcph21_offset = 0;
static State state = WARTE;
static unsigned long schlepp_start = 0;

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
    if (count > 0) mcph21_offset = (float)(sum / count);
    Serial.printf("MCPH21 Offset: %.1f\n", mcph21_offset);
}

static float mcph21_speed_kmh() {
    uint32_t raw_p;
    if (!mcph21_read(raw_p)) return 0;
    float pa = (6.25f * ((float)raw_p - mcph21_offset) / 8388608.0f) * 1000.0f;
    if (pa < 0) pa = 0;
    return sqrtf(2.0f * pa / 1.225f) * 3.6f;
}

static void showDisplay(float speed) {
    char speedStr[8];
    snprintf(speedStr, sizeof(speedStr), "%.0f", speed);

    char statusStr[10];
    if (state == WARTE)
        snprintf(statusStr, sizeof(statusStr), "<20");
    else {
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
    u8g2.sendBuffer();
}

static void goToSleep() {
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 24, "Schlepp");
    u8g2.drawStr(0, 42, "beendet.");
    u8g2.sendBuffer();
    LoRa.end();
    delay(2000);
    u8g2.setPowerSave(1);
    esp_sleep_enable_ext0_wakeup(WAKE_PIN, 0);
    esp_deep_sleep_start();
}

void setup() {
    Serial.begin(115200);

    Wire.begin(OLED_SDA, OLED_SCL);
    u8g2.begin();
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 24, "Kalibriere");
    u8g2.drawStr(0, 42, "Pitot...");
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

    Serial.println("Sender bereit");
}

void loop() {
    static unsigned long letzterSend = 0;
    static float smoothedSpeed = 0.0f;

    if (millis() - letzterSend >= 100) {  // 10x pro Sekunde
        letzterSend = millis();

        float raw = mcph21_speed_kmh();
        smoothedSpeed = 0.04f * raw + 0.96f * smoothedSpeed;
        float speed = smoothedSpeed;
        Serial.printf("V:%.1f\n", speed);

        if (state == WARTE && speed > START_SPEED) {
            state = AKTIV;
            schlepp_start = millis();
        }

        if (state == AKTIV && millis() - schlepp_start >= SCHLEPP_MS) {
            goToSleep();
        }

        char paket[12];
        snprintf(paket, sizeof(paket), "%.1f", speed);
        LoRa.beginPacket();
        LoRa.print(paket);
        LoRa.endPacket();

        showDisplay(speed);
    }
}
