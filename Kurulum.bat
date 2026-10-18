@echo off
setlocal
cd /d "%~dp0"
set "PPG_PAUSE=1"
if "%~1"=="--no-pause" set "PPG_PAUSE=0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install

:use_python
python -m venv .venv
if errorlevel 1 goto failed

:install
".venv\Scripts\python.exe" -c "import sys, struct; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,15) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
  echo Python 3.11-3.14 64-bit gerekiyor. Python 3.11 veya 3.12 onerilir.
  goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Kurulum tamamlandi. Baslat.bat dosyasini acabilirsiniz.
if "%PPG_PAUSE%"=="1" pause
exit /b 0

:failed
echo Kurulum tamamlanamadi. Python kurulumunu, interneti ve yukaridaki hatayi kontrol edin.
if "%PPG_PAUSE%"=="1" pause
exit /b 1
