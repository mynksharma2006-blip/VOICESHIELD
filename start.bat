@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found. Install Python 3.12 and enable the py launcher.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python 3.12 virtual environment...
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Could not create the virtual environment. Confirm Python 3.12 is installed.
        pause
        exit /b 1
    )
)

echo Installing or checking dependencies...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

if not exist "backend\models\aasist.onnx" (
    echo Downloading the pretrained AASIST detector...
    ".venv\Scripts\python.exe" backend\scripts\download_aasist.py
    if errorlevel 1 (
        echo Could not download the AASIST model. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

if not exist ".env" copy ".env.example" ".env" >nul
echo Starting VoiceShield AI server...
start "VoiceShield AI server" /D "%~dp0" "%~dp0.venv\Scripts\python.exe" -m uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port 8000

set /a attempts=0
:wait_for_server
curl.exe --silent --fail --max-time 1 http://127.0.0.1:8000/api/health >nul 2>nul
if not errorlevel 1 goto server_ready
set /a attempts+=1
if %attempts% GEQ 30 (
    echo Server did not become ready. Check the VoiceShield AI server window for errors.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait_for_server

:server_ready
curl.exe --silent --show-error --fail --max-time 5 -X POST http://127.0.0.1:8000/api/call-stream/demo-test | findstr.exe /C:passed >nul
if errorlevel 1 (
    echo The server on port 8000 is not the current VoiceShield version.
    echo Close the old VoiceShield server window and run start.bat again.
    pause
    exit /b 1
)
echo VoiceShield AI is ready at http://127.0.0.1:8000
start "" "http://127.0.0.1:8000"
echo Keep the VoiceShield AI server window open while using the app.
