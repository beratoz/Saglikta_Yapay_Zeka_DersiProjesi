# PPG sisteminin çalışma mantığı

Bu sistem, parmaktan optik nabız sinyali toplar; bilgisayarda görüntüler, bazı
özelliklerini hesaplar ve verdiğimiz yorgunluk etiketleriyle CSV'ye kaydeder.
Mevcut uygulama veri toplama aracıdır; henüz zihinsel yorgunluk tahmini yapan
eğitilmiş bir yapay zekâ modeli içermez.

## 1. Sensör ne ölçüyor?

MAX30102, parmağa **kırmızı (RED)** ve **kızılötesi (IR)** ışık gönderir.
Fotodedektör, dokudan geri dönen ışığı algılar. Nabızla birlikte dokudaki kan
hacmi değiştiğinde ışık sinyali de değişir; buna **PPG** denir.

Sensör, algıladığı analog ışık sinyalini kendi içindeki ADC ile sayısallaştırır.
ESP32'ye gelen RED/IR değerleri bu nedenle **voltaj veya doğrudan nabız değil,
sayısal optik ölçüm değerleridir**. Dalganın değişimi önemlidir; büyük bir ham
sayı tek başına daha yüksek nabız veya daha fazla yorgunluk demek değildir.
[Üreticinin sensör açıklaması](https://www.analog.com/en/products/MAX30102.html)

## 2. Sensör, ESP32 ve bilgisayar nasıl haberleşiyor?

```text
Parmak → MAX30102 → I²C → ESP32-C3 → USB seri bağlantı → Python → Ekran / CSV
```

- **Sensör–ESP32:** I²C kullanılır. SDA veriyi, SCL haberleşme saatini taşır.
  Sensörde ölçümleri geçici olarak tutan FIFO belleği bulunur; ESP32 sayısal
  ölçümleri sensörün kayıtlarından okur.
- **ESP32–bilgisayar:** Karttaki firmware verileri metin satırları halinde USB
  seri porta gönderir. Python bu satırları COM portundan okur.
- **ESP-IDF:** ESP32 firmware'ini geliştirme/yükleme ortamıdır. Bilgisayardaki
  Python uygulaması ise hazır firmware'in gönderdiği veriyi okur.

I²C, ADC ve FIFO yapısı [MAX30102 veri sayfasında](https://www.analog.com/media/en/technical-documentation/data-sheets/max30102.pdf)
açıklanır. Kullanılan GPIO pinleri ve sensörün ayrıntılı ayarları karttaki
firmware'e bağlıdır. Önceki sürümün ESP-IDF ve Arduino kaynakları depodaki
`firmware/` ve `arduniodenemesi/` klasörlerinde korunur.

## 3. ESP32'den hangi veriler geliyor?

Doğruladığımız satır biçimi:

```text
PPG,237,2370,1266,1437
```

| Alan | Örnekteki değer | Anlamı |
|---|---:|---|
| `PPG` | — | Satırın PPG verisi olduğunu belirtir. |
| `seq` | 237 | Örneğin sıra numarası; atlanan örnekleri izlemek için kullanılır. |
| `device_ms` | 2370 | ESP32'nin gönderdiği örnek zamanı, milisaniye. |
| `red` | 1266 | Kırmızı kanalın ham ADC sayımı. |
| `ir` | 1437 | Kızılötesi kanalın ham ADC sayımı. |

Mevcut akış yaklaşık **100 Hz**, yani saniyede 100 örnektir; örnekler yaklaşık
10 ms aralıklıdır. **115200 baud** ise seri iletişim ayarıdır; örnekleme hızıyla
aynı şey değildir. Ölçüm tarihi/saati bilgisayardan, örnek zamanları karttan gelir.
Ekrandaki Hz değeri kartın zaman damgalarından hesaplanır.

## 4. Python ham sinyali nasıl dönüştürüyor?

1. Satırları ayırır; sıra, zaman, RED ve IR değerlerini sayıya çevirir.
2. Sıra/zaman kesintilerini kontrol eder. Kesintileri grafikte boşluk bırakır;
   araya tahmini örnek eklemez.
3. Ham RED/IR'yi ayrı grafiklerde gösterebilir. **Nabız bileşeni** görünümünde
   0,5–4 Hz bant geçiren filtre uygulayarak yavaş zemin değişimini ve bant dışı
   gürültüyü azaltır. Filtreli değerlerin negatif olması bu nedenle normaldir.
4. Filtrelenmiş **IR kanalındaki** tepe adaylarını bulur. Ardışık tepelerin zaman
   farkından atımlar arası süreyi, yani **IBI** değerlerini çıkarır.
5. Uygulamanın aralık/düzenlilik kontrollerini geçen IBI değerlerinden nabız ve
   uygun koşullarda PRV özelliklerini hesaplar.

**Ham RED/IR sayımları değiştirilmeden CSV'ye yazılır.** Filtreli grafik, ham
verinin yerine geçen yeni bir sensör ölçümü değildir.

## 5. Hesaplanan değerler bize ne söylüyor?

| Değer | Anlamı |
|---|---|
| **BPM** | Dakikadaki tahmini nabız sayısı. `BPM = 60000 / ortalama IBI(ms)`. Örneğin 833 ms yaklaşık 72 BPM'dir. |
| **IBI** | Ardışık optik nabız tepeleri arasındaki süre, ms. |
| **PRV–RMSSD** | Ardışık IBI değerlerinin birbirinden ne kadar değiştiğini özetler. |
| **PRV–SDNN** | Kabul edilmiş atımlar arası sürelerin genel dağılımını, standart sapma olarak özetler. |
| **Sinyal kontrolü (%)** | Bulunan aralıkların uygulamanın kabul kurallarını geçme oranı. Kesin doğruluk yüzdesi değildir. |
| **Eksik örnek / kesinti** | Veri akışındaki kayıpları ve zaman/sıra sorunlarını gösterir. |

Bu uygulama nabız için en az **15 saniye** veri ister. RMSSD/SDNN için en az
**60 saniye kesintisiz ve tüm aralıkları kabul edilmiş** seri gerekir. Canlı nabız
son yaklaşık 20 saniyeden, canlı PRV uygun olduğunda en fazla son yaklaşık
65 saniyeden hesaplanır. Kaydedilen CSV'nin özeti ise kaydın tamamından hesaplanır.
Bu süreler uygulamanın hesaplama kurallarıdır; klinik doğruluk garantisi değildir.

PPG'den elde edilen değişkenlik **PRV**'dir. EKG'nin elektriksel atımlarından
hesaplanan HRV ile her koşulda eşdeğer değildir.
[PRV–HRV karşılaştırma araştırması](https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2020.00779/full)

## 6. Zihinsel yorgunlukla nasıl ilişkilendiriyoruz?

Sensör doğrudan “yorgunluk” ölçmez. Biz her kayda **Sabah/Öğle/Akşam**, görev
öncesi/sonrası, zihinsel yorgunluk puanı, uyku, uykululuk, stres, fiziksel
yorgunluk, görev zorluğu ve diğer bağlam bilgilerini ekleriz.

- **Model girdileri:** PPG dalgası, IBI ve hesaplanan özellikler.
- **Hedef etiket:** Seçilen görev aşamasına ait kendi zihinsel yorgunluk puanımız.
- **Bağlam:** Uyku, stres, kafein gibi yorumu etkileyebilecek bilgiler.

Her ölçüm tek CSV'de saklanır; önce/sonra kayıtları otomatik çalışma koduyla
ilişkilendirilir. Etiketler daha sonra düzenlenebilir; ham sinyal korunur.
Tek başına yüksek/düşük nabız veya RMSSD, zihinsel yorgunluğu kanıtlamaz.
Mevcut sürüm ayrıca SpO₂, tansiyon veya EKG ölçümü üretmez.
