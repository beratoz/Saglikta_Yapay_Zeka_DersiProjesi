@echo off
setlocal
cd /d "%~dp0"
set "PPG_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%PPG_PYTHON%" goto run
set "PPG_PYTHON=%USERPROFILE%\miniconda3\envs\ppg_env\python.exe"
if exist "%PPG_PYTHON%" goto run
echo Python ortami hazirlaniyor...
python -m venv .venv
if errorlevel 1 goto error
set "PPG_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PPG_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto error
:run
"%PPG_PYTHON%" main.py
if errorlevel 1 goto error
exit /b 0
:error
echo Uygulama acilamadi. Yukaridaki hata mesajini kontrol edin.
pause
exit /b 1
