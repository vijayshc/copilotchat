@echo off
REM Convenient script to run the CDP chat capture tool

echo Microsoft Copilot Chat Capture - CDP Version
echo ============================================
echo.

REM Find Python (try Anaconda first, then system Python)
set PYTHON_PATH=""

if exist "C:\Users\%USERNAME%\anaconda3\python.exe" (
    set PYTHON_PATH="C:\Users\%USERNAME%\anaconda3\python.exe"
    echo Found Anaconda Python: %PYTHON_PATH%
) else if exist "C:\anaconda3\python.exe" (
    set PYTHON_PATH="C:\anaconda3\python.exe"
    echo Found Anaconda Python: %PYTHON_PATH%
) else (
    REM Try to use system Python
    python --version >nul 2>&1
    if %ERRORLEVEL% == 0 (
        set PYTHON_PATH="python"
        echo Using system Python
    ) else (
        echo ERROR: Python not found!
        echo Please ensure Python is installed and accessible.
        pause
        exit /b 1
    )
)

echo.
echo Starting chat capture tool...
echo Make sure Chrome is running in debug mode first!
echo (Use launch_chrome_debug.bat or launch_chrome_debug.ps1)
echo.

%PYTHON_PATH% getchat_cdp.py

echo.
echo Chat capture ended.
pause