# PowerShell script to launch Chrome in developer mode for Python automation
# This enables remote debugging on port 9222

Write-Host "Starting Chrome in Developer Mode..." -ForegroundColor Green
Write-Host ""
Write-Host "This will:" -ForegroundColor Yellow
Write-Host "- Enable remote debugging on port 9222" -ForegroundColor Yellow
Write-Host "- Create a separate user profile in C:\temp\chrome-debug" -ForegroundColor Yellow
Write-Host "- Allow Python scripts to connect and control the browser" -ForegroundColor Yellow
Write-Host ""

# Create temp directory if it doesn't exist
$tempDir = "C:\temp"
if (!(Test-Path $tempDir)) {
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    Write-Host "Created directory: $tempDir" -ForegroundColor Green
}

# Find Chrome executable
$chromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
)

$chromePath = $null
foreach ($path in $chromePaths) {
    if (Test-Path $path) {
        $chromePath = $path
        break
    }
}

if (-not $chromePath) {
    Write-Host "ERROR: Chrome not found in standard locations!" -ForegroundColor Red
    Write-Host "Please install Google Chrome or modify this script with the correct path." -ForegroundColor Red
    Write-Host ""
    Write-Host "Checked locations:" -ForegroundColor Yellow
    foreach ($path in $chromePaths) {
        Write-Host "- $path" -ForegroundColor Yellow
    }
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Found Chrome at: $chromePath" -ForegroundColor Green
Write-Host ""

# Check if port 9222 is already in use
try {
    $connection = New-Object System.Net.Sockets.TcpClient
    $connection.Connect("localhost", 9222)
    $connection.Close()
    Write-Host "WARNING: Port 9222 is already in use!" -ForegroundColor Yellow
    Write-Host "This might mean Chrome is already running in debug mode." -ForegroundColor Yellow
    Write-Host "You can either:" -ForegroundColor Yellow
    Write-Host "1. Close the existing Chrome instance and restart" -ForegroundColor Yellow
    Write-Host "2. Connect to the existing instance directly" -ForegroundColor Yellow
    Write-Host ""
    $choice = Read-Host "Continue anyway? (y/n)"
    if ($choice.ToLower() -ne "y" -and $choice.ToLower() -ne "yes") {
        exit 0
    }
} catch {
    # Port is free, continue
}

# Launch Chrome with remote debugging enabled
Write-Host "Launching Chrome with remote debugging..." -ForegroundColor Green
Write-Host "CDP endpoint will be: http://localhost:9222" -ForegroundColor Cyan
Write-Host ""
Write-Host "After Chrome opens:" -ForegroundColor Yellow
Write-Host "1. Navigate to Microsoft Copilot (https://copilot.microsoft.com)" -ForegroundColor Yellow
Write-Host "2. Login if needed" -ForegroundColor Yellow
Write-Host "3. Run your Python script (getchat_cdp.py)" -ForegroundColor Yellow
Write-Host ""

$arguments = @(
    "--remote-debugging-port=9222",
    "--user-data-dir=C:\temp\chrome-debug",
    "--disable-features=VizDisplayCompositor",
    "--disable-extensions",
    "--disable-default-apps"
)

try {
    Start-Process -FilePath $chromePath -ArgumentList $arguments -Wait
    Write-Host ""
    Write-Host "Chrome has been closed. The debug session has ended." -ForegroundColor Green
} catch {
    Write-Host "Error launching Chrome: $_" -ForegroundColor Red
}

Read-Host "Press Enter to exit"