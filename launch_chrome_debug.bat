@echo off
REM Batch file to launch Chrome in developer mode for Python automation
REM This enables remote debugging on port 9222

echo Starting Chrome in Developer Mode...
echo.
echo This will:
echo - Enable remote debugging on port 9222
echo - Create a separate user profile in C:\temp\chrome-debug
echo - Allow Python scripts to connect and control the browser
echo.

REM Create temp directory if it doesn't exist
if not exist "C:\temp" mkdir "C:\temp"

REM Find Chrome executable (try common locations)
set CHROME_PATH=""

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set CHROME_PATH="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set CHROME_PATH="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set CHROME_PATH="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
) else (
    echo ERROR: Chrome not found in standard locations!
    echo Please install Google Chrome or modify this script with the correct path.
    echo.
    echo Common locations to check:
    echo - %ProgramFiles%\Google\Chrome\Application\chrome.exe
    echo - %ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
    echo - %LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
    pause
    exit /b 1
)

echo Found Chrome at: %CHROME_PATH%
echo.

REM Launch Chrome with remote debugging enabled
echo Launching Chrome with remote debugging...
echo CDP endpoint will be: http://localhost:9222
echo.

%CHROME_PATH% --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome-debug" --disable-features=VizDisplayCompositor --disable-extensions

echo.
echo Chrome has been closed. The debug session has ended.
pause