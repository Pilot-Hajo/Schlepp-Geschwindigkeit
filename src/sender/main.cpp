#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// TTGO LoRa32 – LoRa Pins
#define LORA_SCK   5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_SS    18
#define LORA_RST   14
#define LORA_DIO0  26

// OLED Pins (per I2C-Scan ermittelt, kein Hardware-RST)
#define OLED_SDA   21
#define OLED_SCL   22

#define LORA_FREQ  868E6

Adafruit_SSD1306 display(128, 64, &Wire, -1);

static String laufzeit() {
    unsigned long s = millis() / 1000;
    char buf[9];
    snprintf(buf, sizeof(buf), "%02lu:%02lu:%02lu", s / 3600, (s % 3600) / 60, s % 60);
    return String(buf);
}

static void showDisplay(const char* zeit, int wert) {
    display.clearDisplay();

    display.setTextSize(2);
    display.setCursor(16, 0);
    display.print("SENDER");

    display.setTextSize(2);
    display.setCursor(0, 18);
    display.print(zeit);

    display.setTextSize(3);
    display.setCursor(0, 38);
    display.print(wert);

    display.display();
}

void setup() {
    Serial.begin(115200);

    Wire.begin(OLED_SDA, OLED_SCL);
    if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
        Serial.println("OLED Fehler");
        while (true) { delay(1000); }
    }
    display.setTextColor(WHITE);
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(0, 20);
    display.print("Sender...");
    display.display();

    SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_SS);
    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(LORA_FREQ)) {
        display.clearDisplay();
        display.setTextSize(2);
        display.setCursor(0, 20);
        display.print("LoRa ERR");
        display.display();
        Serial.println("LoRa Fehler");
        while (true) { delay(1000); }
    }

    randomSeed(analogRead(0));
    Serial.println("Sender bereit");
}

void loop() {
    static unsigned long letzterSend = 0;

    if (millis() - letzterSend >= 1000) {
        letzterSend = millis();

        int wert = random(0, 1000);
        String zeit = laufzeit();

        LoRa.beginPacket();
        LoRa.print(wert);
        LoRa.endPacket();

        showDisplay(zeit.c_str(), wert);
        Serial.printf("[%s] Gesendet: %d\n", zeit.c_str(), wert);
    }
}
