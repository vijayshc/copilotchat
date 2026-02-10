# Microsoft Copilot Chat Capture - CDP Version

This is a modified version of the Copilot chat capture tool that connects to an existing browser running in developer mode instead of launching a new browser instance. This approach works better on Windows systems where Python automation might have trouble launching browsers directly.

## How It Works

Instead of launching a new browser, this version:
1. Connects to Chrome running with remote debugging enabled (Chrome DevTools Protocol - CDP)
2. Uses the existing browser session to capture chat messages
3. Allows you to maintain your login session and browser state

## Setup Instructions

### Step 1: Launch Chrome in Developer Mode

You have several options:

#### Option A: Use the batch file (Easiest)
1. Double-click `launch_chrome_debug.bat`
2. Chrome will open with debugging enabled

#### Option B: Use PowerShell script (Recommended)
1. Right-click `launch_chrome_debug.ps1` and select "Run with PowerShell"
2. Or open PowerShell and run: `.\launch_chrome_debug.ps1`

#### Option C: Manual command line
Open Command Prompt or PowerShell and run:
```cmd
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome-debug"
```

Note: Replace `chrome.exe` with the full path to Chrome if it's not in your PATH.

### Step 2: Navigate to Copilot
1. In the Chrome browser that opened, go to https://copilot.microsoft.com
2. Login with your Microsoft account
3. Start or navigate to a chat conversation

### Step 3: Run the Python Script

#### Option A: Use the batch file (Easiest)
1. Double-click `run_capture.bat`
2. The script will automatically find your Python installation and run the capture tool

#### Option B: Manual command line
1. Open a terminal/command prompt in this directory
2. Run the CDP version of the script:
```cmd
C:\Users\vijay\anaconda3\python.exe getchat_cdp.py
```

Note: Replace the path with your actual Python installation path if different.

The script will:
- Connect to the running Chrome instance
- Ask if you want to automatically navigate to Copilot (optional)
- Wait for you to confirm when ready to start capturing
- Begin capturing chat messages and save them to `copilot_cdp_capture.txt`

## Files Included

- `getchat_cdp.py` - Main Python script (CDP version)
- `getchat.py` - Original Python script (launches new browser)
- `launch_chrome_debug.bat` - Windows batch file to launch Chrome in debug mode
- `launch_chrome_debug.ps1` - PowerShell script to launch Chrome in debug mode
- `run_capture.bat` - Convenient batch file to run the chat capture tool
- `README.md` - This file

## Troubleshooting

### "Failed to connect to browser" Error
- Make sure Chrome is running with the `--remote-debugging-port=9222` flag
- Check that port 9222 is not blocked by firewall
- Ensure Chrome has at least one tab open
- Try restarting Chrome with the debug command

### "No browser contexts found" Error
- Make sure you have at least one tab open in Chrome
- Try refreshing the page or opening a new tab

### Chrome not found
- Install Google Chrome from https://www.google.com/chrome/
- Or modify the launch scripts with the correct path to your Chrome installation

### Port 9222 already in use
- Close any existing Chrome instances running in debug mode
- Use Task Manager to end all chrome.exe processes if needed
- Or connect to the existing debug session if it's what you want

### Permission errors with batch/PowerShell files
- Run as Administrator if needed
- For PowerShell: You might need to change execution policy:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

## Benefits of CDP Version vs Original

1. **Better Windows compatibility** - No issues with browser launching
2. **Persistent sessions** - Keep your login and browser state
3. **Manual navigation** - You control where the browser goes
4. **Debugging friendly** - Browser stays open for inspection
5. **Multiple scripts** - Can run multiple Python scripts against same browser

## Output Format

Messages are saved to `copilot_cdp_capture.txt` in JSON format, one message per line:

```json
{"timestamp": "2025-01-13T10:30:45.123456", "message_id": "user_123456789", "type": "user", "content": "Hello, how are you?", "html_snippet": "<div>Hello, how are you?</div>", "element_location": {"x": 100, "y": 200, "width": 300, "height": 50}}
{"timestamp": "2025-01-13T10:30:47.654321", "message_id": "ai_987654321", "type": "ai", "content": "I'm doing well, thank you for asking!", "html_snippet": "<div>I'm doing well, thank you for asking!</div>", "element_location": {"x": 100, "y": 260, "width": 400, "height": 60}}
```

## Requirements

- Python 3.7+
- playwright library (`pip install playwright`)
- Google Chrome browser
- Windows operating system (batch files)

The original `conda` environment with `playwright` is already set up and ready to use.