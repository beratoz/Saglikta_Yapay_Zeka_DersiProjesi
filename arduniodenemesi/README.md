# Arduino programları

- **`ppg_max30102/ppg_max30102.ino`**: proje sensör yazılımı. MAX30102'den RED/IR verilerini nominal 100 Hz okuyup Python arayüzüne gönderir.
- **`usb_test/usb_test.ino`**: sensör kullanmadan USB bağlantısını sınar; saniyede bir çalışma süresini gönderir.

Arduino IDE 2'de Espressif Systems'ın **esp32** kart paketini kurun. Test edilen sürüm **3.3.12**.

Kart: **ESP32C3 Dev Module**. USB CDC On Boot: **Enabled**. Flash: **DIO / 40 MHz / 4 MB**. Upload Speed ve Seri Monitör: **115200**. Kendi COM portunuzu seçin. `sketch.yaml` kart ayarlarını saklar; sabit COM portu içermez.

USB çıkarılmışken bağlantı: **3V3 → 3.3 V uyumlu VIN/VCC, GND → GND, GPIO4 → SDA, GPIO5 → SCL**. INT gerekmez. Ek sensör kütüphanesi gerekmez; program `Wire` kullanır.

Sensör bağlı değilse `ERR,SENSOR_NOT_READY,I2C_0x57` mesajı verilir ve yeniden denenir. Algılanınca `INFO,PPG_V1,100HZ,RED_IR` ardından `PPG,seq,device_ms,red,ir` satırları gönderilir. Kesintiler hata mesajı ve sıra boşluğu ile işaretlenir.

Arduino Seri Monitör ve Python arayüzü aynı COM portunu aynı anda kullanamaz. Yüklemeden önce her ikisinin de seri bağlantısını kapatın. Karta yükleme, mevcut programın yerini alır.
