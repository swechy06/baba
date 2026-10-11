# Baba Discord bilet botu

Python sürümünün tüm aktif işlevleri Java 21 ve JDA 6.7.0 ile taşındı. Python dosyası depoda korunuyor.

**[Hazır Java paketini indir](https://github.com/swechy06/baba/raw/refs/heads/main/downloads/baba-ticket-java-v1.zip)**

ZIP dosyasını çıkar, eski botu durdur ve kendi `.env` ile `ticket_data.json` dosyalarını paketin `java-bot` klasörüne kopyala. Java 21 veya üzeri kurulu olmalı. Bu klasörde CMD aç:

```cmd
run.cmd
```

Doğrudan çalıştırma:

```cmd
java -jar ticket-bot.jar
```

İlk geçişte Discord'da `/biletpaneliniyenile` kullan. Ayrıntılı geçiş ve derleme yönergeleri: [Java kullanım kılavuzu](java-bot/README.md).

Java kaynakları ve otomatik testler `java-bot` klasöründe. Gizli token ve sunucu kayıtları hazır pakete dahil edilmez.
