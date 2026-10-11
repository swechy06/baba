# Baba Discord bilet botu — Python v41

Bilet sistemine yetkili istatistikleri ve bilet sahibinin DM üzerinden verebildiği puanlar eklendi. Mevcut kurulum, doğrulama, sıfırlama ve bilet yönetimi komutları korunur.

**[Hazır Python paketini indir](https://github.com/swechy06/baba/raw/refs/heads/main/downloads/baba-ticket-python-v41.zip)** · **[Yalnızca Python dosyasını indir](https://github.com/swechy06/baba/raw/refs/heads/main/ticket_system_main_v41.py)**

## Kurulum ve güncelleme

1. Çalışan botu CMD penceresinde **Ctrl+C** ile durdur. Aynı bot tokenıyla yalnızca bir sürüm çalıştır.
2. Mevcut bot klasörünü yedekle. **`.env`, `ticket_data.json` ve varsa `ticket_stats.json` dosyalarını koru.** Yeni paketi ayrı klasöre çıkarıyorsan bu dosyaları Python dosyasının yanına kopyala. Token ve sunucu kayıtlarını GitHub'a yükleme.
3. İlk kurulumda `.env.example` dosyasını `.env` adıyla kopyalayıp `DISCORD_TOKEN=` satırına bot tokenını ekle. Mevcut `.env` dosyan varsa onu kullan.
4. [Python](https://www.python.org/downloads/windows/) kurulu olmalı; Windows kurulumunda **Add Python to PATH** seçeneğini işaretle. Bot klasöründe CMD aç:

```cmd
python -m pip install -r requirements.txt
python ticket_system_main_v41.py
```

Sonraki açılışlarda şu komut yeterlidir:

```cmd
python ticket_system_main_v41.py
```

İstersen `run_python.cmd` dosyasına çift tıklayabilir veya CMD üzerinden `run_python.cmd` çalıştırabilirsin. Hata halinde pencere açık kalır. Discord Developer Portal'da **Server Members Intent** ve **Message Content Intent** açık olmalı; botun bilet ve log kanallarındaki mevcut izinlerini koru.

## İstatistikler ve puanlama

`/yetkiliistatistik` komutunu mevcut komut yetki rolüne (`1558781695950331904`) sahip üyeler kullanabilir. `yetkili` parametresi isteğe bağlıdır; boş bırakırsan kendi istatistiklerin, bir üye seçersen onun istatistikleri gösterilir.

- Kapanan bilet yalnızca **bir kez** sayılır. Bilet üstlenilmişse üstlenen yetkiliye, üstlenilmemişse kapatan yetkiliye yazılır.
- İlk yanıt süresi, açılıştan biletin yazıldığı yetkilinin ilk mesajına veya ilk bilet işlemine kadar ölçülür.
- Bilet kapanınca bileti açan kişiye DM üzerinden **1–5** puan seçebileceği bir mesaj gönderilir. Her bilet için yalnızca bir puan kabul edilir.
- Ortalama puan yalnızca puan verilmiş biletlerden hesaplanır; puansız biletler sıfır puan sayılmaz.
- İstatistikler ve puanlar `ticket_stats.json` içinde saklanır; güncellemelerde bu dosyayı silme.

Önceki Java kaynakları ve paketi `java-bot` klasöründe korunur. Bu güncellemenin çalıştırma dosyası `ticket_system_main_v41.py` dosyasıdır.

## Geliştirici doğrulaması

İstatistik testlerini kaynak kod bulunan proje klasöründe çalıştır:

```cmd
python -m unittest discover -s python-tests -p "test_stats*.py"
```
