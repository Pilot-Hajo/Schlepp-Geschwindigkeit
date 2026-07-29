#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <WiFi.h>
#include <WiFiUDP.h>

#define LORA_SCK    5
#define LORA_MISO   19
#define LORA_MOSI   27
#define LORA_SS     18
#define LORA_RST    14
#define LORA_DIO0   26

#define OLED_SDA    21
#define OLED_SCL    22

#define LORA_FREQ   868E6

const char* AP_SSID = "Schlepp";
const char* AP_PASS = "schlepp123";
const uint16_t UDP_PORT = 5005;

U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);
WiFiUDP udp;

static String laufzeit() {
    unsigned long s = millis() / 1000;
    char buf[9];
    snprintf(buf, sizeof(buf), "%02lu:%02lu:%02lu", s / 3600, (s % 3600) / 60, s % 60);
    return String(buf);
}

static void showDisplay(float speed, int rssi, int clients) {
    char speedStr[8];
    snprintf(speedStr, sizeof(speedStr), "%.0f", speed);

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_logisoso62_tn);
    u8g2.drawStr(0, 64, speedStr);
    u8g2.setFont(u8g2_font_5x7_tr);
    char info[16];
    snprintf(info, sizeof(info), "%d W%d", rssi, clients);
    u8g2.drawStr(128 - u8g2.getStrWidth(info), 64, info);
    u8g2.sendBuffer();
}

void setup() {
    Serial.begin(115200);

    Wire.begin(OLED_SDA, OLED_SCL);
    u8g2.begin();
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 32, "WiFi...");
    u8g2.sendBuffer();

    WiFi.softAP(AP_SSID, AP_PASS);
    udp.begin(UDP_PORT);

    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_helvB14_tr);
    u8g2.drawStr(0, 18, "Schlepp");
    String ip = WiFi.softAPIP().toString();
    u8g2.setFont(u8g2_font_5x7_tr);
    u8g2.drawStr(0, 34, ip.c_str());
    u8g2.drawStr(0, 44, "Warte LoRa...");
    u8g2.sendBuffer();

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
    Serial.printf("AP: %s  IP: %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
    Serial.println("Empfaenger bereit");
}

static float smoothedSpeed = 0.0f;

void loop() {
    int packetSize = LoRa.parsePacket();
    if (packetSize) {
        String empfangen = "";
        while (LoRa.available()) {
            empfangen += (char)LoRa.read();
        }
        int rssi = LoRa.packetRssi();
        float raw = empfangen.toFloat();
        smoothedSpeed = 0.08f * raw + 0.92f * smoothedSpeed;
        float speed = smoothedSpeed;
        int clients = WiFi.softAPgetStationNum();

        showDisplay(speed, rssi, clients);

        char msg[64];
        snprintf(msg, sizeof(msg), "[%s] V:%.1f T:0.0 RSSI:%d\n",
                 laufzeit().c_str(), speed, rssi);

        Serial.print(msg);

        // UDP Broadcast an alle verbundenen Clients
        IPAddress bcast = WiFi.softAPIP();
        bcast[3] = 255;
        udp.beginPacket(bcast, UDP_PORT);
        udp.write((uint8_t*)msg, strlen(msg));
        udp.endPacket();
    }
}
