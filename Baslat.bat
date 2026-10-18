@echo off
setlocal
cd /d "%~dp0"
set "PPG_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%PPG_PYTHON%" goto run

rem Mevcut kullanicinin calisan Conda ortami da desteklenir.
set "PPG_PYTHON=%USERPROFILE%\miniconda3\envs\ppg_env\python.exe"
if exist "%PPG_PYTHON%" goto run

call Kurulum.bat --no-pause
if errorlevel 1 (
  pause
  exit /b 1
)
set "PPG_PYTHON=%~dp0.venv\Scripts\python.exe"

:run
"%PPG_PYTHON%" main.py
if errorlevel 1 (
  echo Uygulama acilamadi. Hata mesajini kontrol edin.
  pause
  exit /b 1
)
