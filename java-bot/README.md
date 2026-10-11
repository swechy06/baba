# Discord bilet botu — Java sürümü

Hazır paketi çalıştırmak için **Java 21 veya üzeri** yeterlidir. Python ve Maven gerekmez. Botun ayarları yine `.env` ve `ticket_data.json` dosyalarından okunur.

## Python'dan geçiş

1. Eski bot klasörünün, özellikle `.env` ve `ticket_data.json` dosyalarının yedeğini al.
2. Python botunu çalıştığı CMD penceresinde **Ctrl+C** ile durdur. Aynı token ile iki botu birlikte çalıştırma.
3. Java paketini ayrı bir klasöre çıkar. Eski `.env` ve `ticket_data.json` dosyalarını **`ticket-bot.jar` ve `run.cmd` ile aynı klasöre** kopyala. Token veya ayar dosyalarını GitHub'a yükleme.
4. Java kurulu değilse [Adoptium Temurin](https://adoptium.net/temurin/releases/?version=21) üzerinden Windows için Java 21 kur; kurulumda PATH seçeneğini etkinleştir. Yeni CMD penceresinde `java -version` komutuyla kontrol et.
5. `run.cmd` dosyasına çift tıkla veya bot klasöründe CMD açıp aşağıdaki komutu çalıştır:

   ```cmd
   run.cmd
   ```

   JAR dosyasını doğrudan çalıştırmak için:

   ```cmd
   java -jar ticket-bot.jar
   ```

Token daha önce `DISCORD_TOKEN` ortam değişkeni olarak ayarlandıysa aynı değişken kullanılabilir; ortam değişkeni `.env` değerinden önce gelir. İlk kurulumda `.env.example` dosyasının bir kopyasını `.env` adıyla kaydedip `DISCORD_TOKEN=` satırına bot tokenını ekle.

## Discord ayarları ve ilk açılış

[Discord Developer Portal](https://discord.com/developers/applications) içinde botun **Server Members Intent** ve **Message Content Intent** seçeneklerini aç. Bot davetinde `bot` ve `applications.commands` kapsamlarını kullan. Botun ilgili kanallarda kanalı görüntüleme, kanalları ve izinleri yönetme, mesaj gönderme, bağlantı yerleştirme, dosya ekleme ve mesaj geçmişini okuma izinleri olmalı.

Mevcut ayarlar `ticket_data.json` üzerinden korunur. Python sürümünün eski bilet açma paneli düğmeleri Java'ya geçince çalışmaz; yönetici hesabıyla **`/biletpaneliniyenile`** komutunu bir kez kullan. Bu komut kayıtlı ayarlarla Java panelini gönderir. Java panelinin düğmeleri bot yeniden başlatıldığında da çalışır. Mevcut bilet kanalları ve eski biletlerin kontrol düğmeleri desteklenir.

Yönetici komutları:

| Komut | İşlev |
| --- | --- |
| `/biletsisteminikur` | Panel kanalı, bilet kategorileri ve yetkili rollerini ayarlar. |
| `/ticketsistemdogrulama` | Kurulumu sade bir özetle gösterir; eksik ayarları belirtir. |
| `/biletsistemisifirla` | Onay sonrası bilet panellerini siler ve sistem ayarlarını sıfırlar. Mevcut bilet kanallarını silmez. |
| `/biletpaneliniyenile` | Kayıtlı ayarlarla bilet açma panelini yeniler. |

Slash komutlarının Discord'da görünmesi zaman alabilir. Bot rolünün yetkili rollerinin üzerinde olması ve gerekli kategori/kanal izinlerinin bulunması gerekir. Başlatma hatalarında CMD çıktısını kontrol et; `run.cmd` hata halinde pencereyi açık tutar.

Biletin durumunu değiştirme, üstlenme, üye ekleme ve kapatma düğmelerini yalnızca o destek türünde seçilen yetkili roller kullanabilir. Yönetici izni tek başına bu düğmeler için yeterli değildir; yönetici de ilgili yetkili rolüne sahip olmalıdır. Bileti açan kişi bu işlemleri yapamaz, ayrıca yetkili rolü varsa yapabilir.

Log kanalı isteğe bağlıdır. Ayarlanmışsa konuşma kaydı başarıyla gönderilmeden bilet kapatılmaz; gönderme hatasında bilet açık kalır. Kapanan biletler silinmez: bileti açan ve sonradan eklenen üyelerin özel erişimi kaldırılır, yetkili rollerin okuma erişimi korunur. Kapalı kategori seçilmişse kanal oraya taşınır; seçilmemişse bulunduğu yerde kapatılır. Sistemi sıfırlamak kayıtlı yetkili ayarlarını da kaldırır; korunan biletleri yönetebilmek için aynı destek türlerini ve yetkili rolleri yeniden ayarlamak gerekir.

## Güncellemeler

Botu **Ctrl+C** ile durdur, yeni paketteki program dosyalarını yerleştir ve kendi `.env` ile `ticket_data.json` dosyalarını koru. Ardından bot klasöründe:

```cmd
run.cmd
```

veya:

```cmd
java -jar ticket-bot.jar
```

## Kaynak koddan derleme

Java 21+ **JDK** ve [Apache Maven](https://maven.apache.org/download.cgi) gerekir. Projede JDA 6.7.0 kullanılır. Derleme ve testler için proje klasöründe:

```cmd
mvn clean verify
```

Çıktı `target\ticket-bot.jar` dosyasıdır; `run.cmd` kökte JAR yoksa bu dosyayı da çalıştırır. `build.cmd` aynı derlemeyi yapar ve başarılı çıktıyı köke `ticket-bot.jar` olarak kopyalar. Kökteki JAR'ı güncel tutmak için CMD üzerinden:

```cmd
build.cmd
run.cmd
```

Bağımlılıklar ilk derlemede indirilir; hazır JAR ile çalıştırırken Maven gerekmez.
