@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto folder_error

where python >nul 2>&1
if errorlevel 1 goto python_missing
if not exist "ticket_system_main_v41.py" goto script_missing

echo Bot baslatiliyor. Durdurmak icin Ctrl+C kullan.
python ticket_system_main_v41.py
set "BOT_EXIT=%ERRORLEVEL%"
if "%BOT_EXIT%"=="0" goto finished
echo.
echo Bot hata kodu %BOT_EXIT% ile kapandi. Yukaridaki hata mesajini kontrol et.
pause
goto finished

:folder_error
echo Bot klasorune gecilemedi.
goto failure

:python_missing
echo Python bulunamadi. Python kur ve Add Python to PATH secenegini etkinlestir.
echo https://www.python.org/downloads/windows/
echo Kurulumdan sonra yeni CMD ac ve python -m pip install -r requirements.txt calistir.
goto failure

:script_missing
echo ticket_system_main_v41.py bulunamadi. Python paketini tamamen klasore cikar.
goto failure

:failure
set "BOT_EXIT=1"
pause

:finished
endlocal & exit /b %BOT_EXIT%
