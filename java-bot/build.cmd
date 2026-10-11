@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_error

where java >nul 2>&1
if errorlevel 1 goto java_missing
where mvn >nul 2>&1
if errorlevel 1 goto maven_missing

echo Kaynak kod derleniyor ve testler calistiriliyor...
call mvn clean verify
set "BUILD_EXIT=%ERRORLEVEL%"
if not "%BUILD_EXIT%"=="0" goto build_failed
if not exist "target\ticket-bot.jar" goto jar_missing
copy /y "target\ticket-bot.jar" "ticket-bot.jar" >nul
set "BUILD_EXIT=%ERRORLEVEL%"
if not "%BUILD_EXIT%"=="0" goto copy_failed
echo Derleme tamamlandi. Botu run.cmd ile baslatabilirsin.
goto finished

:folder_error
echo Proje klasorune gecilemedi.
goto failure

:java_missing
echo Java bulunamadi. Kaynaktan derleme icin Java 21+ JDK kur.
echo https://adoptium.net/temurin/releases/?version=21
goto failure

:maven_missing
echo Maven bulunamadi. Apache Maven kur ve bin klasorunu PATH'e ekle.
echo https://maven.apache.org/download.cgi
echo Hazir ticket-bot.jar paketini calistirmak icin Maven gerekmez.
goto failure

:jar_missing
echo Derleme tamamlandi fakat target\ticket-bot.jar bulunamadi.
goto failure

:copy_failed
echo JAR dosyasi koke kopyalanamadi. Calisan botu durdurup tekrar dene.
goto failure

:build_failed
echo Derleme basarisiz oldu. Yukaridaki hata mesajini kontrol et.
echo Derleme icin Java 21+ JDK ve Maven gereklidir.
pause
goto finished

:failure
set "BUILD_EXIT=1"
pause

:finished
endlocal & exit /b %BUILD_EXIT%
