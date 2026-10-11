@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_error

where java >nul 2>&1
if errorlevel 1 goto java_missing

set "JAVA_VERSION="
for /f "tokens=3" %%V in ('java -version 2^>^&1 ^| findstr /i /c:"version"') do if not defined JAVA_VERSION set "JAVA_VERSION=%%~V"
if not defined JAVA_VERSION goto java_version_error
set "JAVA_MAJOR="
for /f "tokens=1,2 delims=." %%A in ("%JAVA_VERSION%") do (
    set "JAVA_MAJOR=%%A"
    if "%%A"=="1" set "JAVA_MAJOR=%%B"
)
echo %JAVA_MAJOR%|findstr /r /x "[0-9][0-9]*" >nul
if errorlevel 1 goto java_version_error
if %JAVA_MAJOR% LSS 21 goto java_old

set "BOT_JAR="
if exist "ticket-bot.jar" set "BOT_JAR=ticket-bot.jar"
if not defined BOT_JAR if exist "target\ticket-bot.jar" set "BOT_JAR=target\ticket-bot.jar"
if not defined BOT_JAR goto jar_missing

echo Bot baslatiliyor. Durdurmak icin Ctrl+C kullan.
java -jar "%BOT_JAR%"
set "BOT_EXIT=%ERRORLEVEL%"
if "%BOT_EXIT%"=="0" goto finished
echo.
echo Bot hata kodu %BOT_EXIT% ile kapandi. Yukaridaki hata mesajini kontrol et.
pause
goto finished

:folder_error
echo Bot klasorune gecilemedi.
goto failure

:java_missing
echo Java bulunamadi. Java 21 veya daha yeni bir surum kur ve yeni CMD ac.
echo https://adoptium.net/temurin/releases/?version=21
echo Kurulumda PATH secenegini etkinlestir.
goto failure

:java_version_error
echo Java surumu okunamadi. CMD'de java -version komutunu kontrol et.
echo Java 21 veya daha yeni bir surum gereklidir.
goto failure

:java_old
echo Java %JAVA_VERSION% bulundu; Java 21 veya daha yeni bir surum gereklidir.
echo https://adoptium.net/temurin/releases/?version=21
echo Kurulumdan sonra yeni bir CMD penceresi ac.
goto failure

:jar_missing
echo ticket-bot.jar bulunamadi. Hazir Java paketini tamamen klasore cikar.
echo Kaynak kod kullaniyorsan once build.cmd calistir.
goto failure

:failure
set "BOT_EXIT=1"
pause

:finished
endlocal & exit /b %BOT_EXIT%
