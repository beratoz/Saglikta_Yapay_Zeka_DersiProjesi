#include <Wire.h>

// MAX30102: SDA -> GPIO4, SCL -> GPIO5, GND -> GND.
// Besleme girisini modulun 3.3 V uyumluluguna gore baglayin.
constexpr uint8_t ADDRESS = 0x57;
constexpr int SDA_PIN = 4;
constexpr int SCL_PIN = 5;
uint64_t sequence = 0;
bool ready = false;
unsigned long lastRetry = 0;

bool readRegister(uint8_t reg, uint8_t *data, uint8_t count) {
  Wire.beginTransmission(ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(ADDRESS, count) != count) return false;
  for (uint8_t i = 0; i < count; ++i) data[i] = Wire.read();
  return true;
}

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool startSensor() {
  uint8_t partId = 0;
  if (!readRegister(0xff, &partId, 1)) {
    Serial.println("ERR,SENSOR_NOT_READY,I2C_0x57");
    return false;
  }
  if (partId != 0x15) {
    Serial.printf("ERR,UNEXPECTED_PART_ID,0x%02X\n", partId);
    return false;
  }
  if (!writeRegister(0x09, 0x40)) return false;
  delay(100);
  // 100 Hz, averaging off, RED + IR, 18-bit ADC, approx. 7.2 mA/LED.
  const uint8_t config[][2] = {
    {0x02, 0}, {0x03, 0}, {0x08, 0x0f}, {0x0a, 0x27},
    {0x0c, 0x24}, {0x0d, 0x24},
    {0x04, 0}, {0x05, 0}, {0x06, 0}, {0x09, 0x03}
  };
  for (const auto &setting : config) {
    if (!writeRegister(setting[0], setting[1])) {
      Serial.println("ERR,SENSOR_CONFIG,I2C_WRITE");
      return false;
    }
  }
  Serial.println("INFO,PPG_V1,100HZ,RED_IR");
  return true;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN, 100000);
  Wire.setTimeOut(50);
  delay(1000);
  ready = startSensor();
  lastRetry = millis();
}

void loop() {
  if (!ready) {
    if (millis() - lastRetry >= 2000) {
      ready = startSensor();
      lastRetry = millis();
    }
    delay(5);
    return;
  }
  uint8_t pointers[3];
  if (!readRegister(0x04, pointers, 3) || pointers[1] != 0) {
    Serial.println("ERR,FIFO_OR_I2C,RESTART");
    sequence += 32;  // Mark a discontinuity; never fabricate missing samples.
    ready = false;
    return;
  }
  const uint8_t count = (pointers[0] - pointers[2]) & 0x1f;
  for (uint8_t i = 0; i < count; ++i) {
    uint8_t bytes[6];
    if (!readRegister(0x07, bytes, 6)) {
      Serial.println("ERR,FIFO_READ,I2C");
      sequence += 32;
      ready = false;
      return;
    }
    const uint32_t red = ((uint32_t(bytes[0]) << 16) | (uint32_t(bytes[1]) << 8) | bytes[2]) & 0x3ffff;
    const uint32_t ir = ((uint32_t(bytes[3]) << 16) | (uint32_t(bytes[4]) << 8) | bytes[5]) & 0x3ffff;
    // Time follows the nominal sensor clock, not USB arrival time.
    Serial.printf("PPG,%llu,%llu,%lu,%lu\n",
                  (unsigned long long)sequence, (unsigned long long)(sequence * 10),
                  (unsigned long)red, (unsigned long)ir);
    ++sequence;
  }
  delay(5);
}
