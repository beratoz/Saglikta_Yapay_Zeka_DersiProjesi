// Kartin USB seri baglantisini sinar; sensor veya LED pini kullanmaz.
void setup() {
  Serial.begin(115200);
  delay(1000);
}

void loop() {
  Serial.print("ESP32-C3 calisiyor | sure: ");
  Serial.print(millis() / 1000);
  Serial.println(" saniye");
  delay(1000);
}
