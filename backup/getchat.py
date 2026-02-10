#!/usr/bin/env python3
"""
Debug version to identify why capture stops after one message
"""

import asyncio
import time
import json
import datetime
import logging
import sys
import signal
from pathlib import Path
from typing import Set, Dict, Any, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext


class CopilotChatCapture:
    """Playwright-based capture for Copilot user and AI messages."""
    
    def __init__(self, 
                 output_file: str = "copilot_chat_capture.txt",
                 capture_interval: float = 0.5,
                 headless: bool = False):
        """
        Initialize the chat capture.
        
        Args:
            output_file: Path to save captured messages
            capture_interval: Seconds between capture checks
            headless: Whether to run browser in headless mode
        """
        self.output_file = Path(output_file)
        self.capture_interval = capture_interval
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.running = False
        self.captured_message_ids = set()  # Track captured messages to avoid duplicates
        self.message_count = 0
        self.loop_count = 0
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Create output directory
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    async def _setup_browser(self) -> tuple[Browser, Page]:
        """Setup and configure Playwright browser."""
        self.logger.info("Setting up Playwright browser...")
        
        playwright = await async_playwright().start()
        
        # Launch browser with options
        browser = await playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--window-size=1920,1080',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor'
            ]
        )
        
        # Create context and page
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        
        # Add page error handler
        page.on("error", lambda error: self.logger.error(f"Page error: {error}"))
        page.on("pageerror", lambda error: self.logger.error(f"Page error: {error}"))
        
        self.logger.info("✓ Playwright browser setup complete")
        return browser, page
    
    async def _wait_for_user_ready(self):
        """Wait for user to login and navigate to chat page."""
        print("\n" + "="*60)
        print("🌐 BROWSER OPENED - USER ACTION REQUIRED")
        print("="*60)
        print("1. Please login to Microsoft Copilot in the browser")
        print("2. Navigate to a chat conversation")
        print("3. Make sure you can see chat messages")
        print("4. Press ENTER here when ready to start capturing...")
        print("="*60)
        
        try:
            # Use asyncio to handle input without blocking
            await asyncio.get_event_loop().run_in_executor(None, input)
            print("✅ Starting message capture...")
        except KeyboardInterrupt:
            print("\n❌ Cancelled by user")
            raise
    
    def _generate_message_id(self, element_text: str, element_box: Dict, message_type: str) -> str:
        """Generate unique ID for a message element."""
        try:
            # Use text content hash + position for unique ID
            location_str = f"{element_box.get('x', 0)}_{element_box.get('y', 0)}"
            return f"{message_type}_{hash(element_text)}_{location_str}"
        except Exception:
            # Last resort: use timestamp
            return f"{message_type}_{int(time.time() * 1000)}"
    
    async def _capture_messages(self) -> int:
        """Capture new user and AI messages from the page using flexible selectors."""
        new_messages = 0
        
        try:
            print(f"🔍 Loop #{self.loop_count}: Checking for new messages...")
            
            # Check if page is still responsive
            try:
                page_title = await self.page.title()
                print(f"📄 Page title: {page_title}")
            except Exception as e:
                print(f"❌ Page not responsive: {e}")
                return 0
            
            # Strategy 1: Try original target selectors
            user_elements = await self.page.query_selector_all('[data-content="user-message"]')
            ai_elements = await self.page.query_selector_all('[data-content="ai-message"]')
            
            print(f"🔍 Original selectors found: {len(user_elements)} user, {len(ai_elements)} AI")
            
            if user_elements or ai_elements:
                self.logger.debug(f"Found {len(user_elements)} user and {len(ai_elements)} AI messages with original selectors")
                new_messages += await self._process_elements(user_elements, "user")
                new_messages += await self._process_elements(ai_elements, "ai")
                return new_messages
            
            # Strategy 2: Try alternative selectors
            alternative_selectors = [
                ('[data-testid="message"]', 'smart'),
                ('[data-testid="turn"]', 'smart'),
                ('article', 'smart'),
                ('[role="listitem"]', 'smart'),
                ('.message', 'smart'),
                ('.chat-message', 'smart'),
                ('div[data-message-id]', 'smart')
            ]
            
            for selector, method in alternative_selectors:
                elements = await self.page.query_selector_all(selector)
                print(f"🔍 Selector '{selector}': found {len(elements)} elements")
                if elements:
                    self.logger.debug(f"Found {len(elements)} elements with selector: {selector}")
                    if method == 'smart':
                        new_messages += await self._process_elements_smart(elements)
                    else:
                        new_messages += await self._process_elements(elements, method)
                    
                    if new_messages > 0:
                        break
            
            return new_messages
            
        except Exception as e:
            self.logger.error(f"Error capturing messages: {e}")
            print(f"❌ Error in capture: {e}")
            return 0
    
    async def _process_elements(self, elements, message_type):
        """Process elements with known message type."""
        new_messages = 0
        for element in elements:
            try:
                text = await element.inner_text()
                if text.strip():
                    box = await element.bounding_box()
                    message_id = self._generate_message_id(text, box or {}, message_type)
                    if message_id not in self.captured_message_ids:
                        await self._save_message(element, message_type, message_id, text)
                        new_messages += 1
            except Exception as e:
                self.logger.debug(f"Error processing {message_type} element: {e}")
        return new_messages
    
    async def _process_elements_smart(self, elements):
        """Process elements and determine message type automatically."""
        new_messages = 0
        for element in elements:
            try:
                text = await element.inner_text()
                if text.strip() and len(text.strip()) > 5:
                    # Determine message type based on content and attributes
                    message_type = await self._determine_message_type(element, text)
                    
                    box = await element.bounding_box()
                    message_id = self._generate_message_id(text, box or {}, message_type)
                    if message_id not in self.captured_message_ids:
                        await self._save_message(element, message_type, message_id, text)
                        new_messages += 1
            except Exception as e:
                self.logger.debug(f"Error processing smart element: {e}")
        return new_messages
    
    async def _determine_message_type(self, element, text):
        """Determine if message is from user or AI based on content and attributes."""
        try:
            # Check element attributes
            classes = await element.get_attribute('class') or ""
            
            # Check data attributes
            data_attrs = await element.evaluate('''el => {
                const attrs = {};
                for (let attr of el.attributes) {
                    if (attr.name.startsWith('data-')) {
                        attrs[attr.name] = attr.value;
                    }
                }
                return attrs;
            }''')
            
            # Check parent attributes
            parent_classes = ""
            try:
                parent_classes = await element.evaluate('el => el.parentElement?.className || ""')
            except:
                pass
            
            # Combine all attribute text for analysis
            all_attrs = f"{classes} {parent_classes} {str(data_attrs)}".lower()
            
            # Heuristics for message type detection
            if any(keyword in all_attrs for keyword in ['user', 'human', 'input']):
                return "user"
            elif any(keyword in all_attrs for keyword in ['assistant', 'ai', 'bot', 'copilot']):
                return "ai"
            elif any(keyword in text.lower() for keyword in ['you:', 'user:']):
                return "user"
            elif len(text) > 50:  # Longer messages are often AI responses
                return "ai"
            else:
                return "unknown"
                
        except Exception:
            return "unknown"
    
    async def _save_message(self, element, message_type: str, message_id: str, text: str):
        """Save a message to file."""
        try:
            if not text.strip():
                return
            
            timestamp = datetime.datetime.now().isoformat()
            
            # Get element info
            box = await element.bounding_box()
            html_snippet = await element.inner_html()
            
            # Create message data
            message_data = {
                'timestamp': timestamp,
                'message_id': message_id,
                'type': message_type,
                'content': text.strip(),
                'html_snippet': html_snippet[:500] if html_snippet else "",  # First 500 chars
                'element_location': box
            }
            
            # Save to file
            with open(self.output_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(message_data, ensure_ascii=False) + '\n')
            
            # Track as captured
            self.captured_message_ids.add(message_id)
            self.message_count += 1
            
            # Print to console for immediate feedback
            type_emoji = "👤" if message_type == "user" else "🤖"
            preview = text[:100] + "..." if len(text) > 100 else text
            print(f"{type_emoji} [{message_type.upper()}] {preview}")
            
            self.logger.debug(f"Captured {message_type} message (ID: {message_id})")
            
        except Exception as e:
            self.logger.error(f"Error saving message: {e}")
    
    async def start_capture(self, copilot_url: str = "https://copilot.microsoft.com"):
        """Start the capture process."""
        try:
            # Setup browser
            self.browser, self.page = await self._setup_browser()
            
            # Navigate to Copilot
            self.logger.info(f"Navigating to: {copilot_url}")
            await self.page.goto(copilot_url, wait_until="networkidle")
            
            # Wait for user to login and setup
            await self._wait_for_user_ready()
            
            # Write session header
            current_url = self.page.url
            with open(self.output_file, 'a', encoding='utf-8') as f:
                session_info = {
                    'session_start': datetime.datetime.now().isoformat(),
                    'url': current_url,
                    'capture_method': 'playwright_focused',
                    'target_selectors': {
                        'user_messages': '[data-content="user-message"]',
                        'ai_messages': '[data-content="ai-message"]'
                    }
                }
                f.write(json.dumps(session_info, ensure_ascii=False) + '\n')
            
            print(f"\n🔄 Starting continuous capture...")
            print(f"📁 Output file: {self.output_file}")
            print(f"⏱️  Checking every {self.capture_interval} seconds")
            print(f"⏹️  Press Ctrl+C to stop\n")
            
            self.running = True
            last_count = 0
            
            # Main capture loop
            while self.running:
                try:
                    self.loop_count += 1
                    print(f"\n--- Loop #{self.loop_count} ---")
                    print(f"Running: {self.running}")
                    print(f"Browser open: {self.browser is not None}")
                    print(f"Page responsive: {self.page is not None}")
                    
                    new_messages = await self._capture_messages()
                    
                    if new_messages > 0:
                        print(f"✅ Captured {new_messages} new messages (Total: {self.message_count})")
                    elif self.message_count != last_count:
                        print(f"📊 Total messages captured: {self.message_count}")
                        last_count = self.message_count
                    else:
                        print(f"💤 No new messages found (Total: {self.message_count})")
                    
                    print(f"⏱️  Sleeping for {self.capture_interval} seconds...")
                    await asyncio.sleep(self.capture_interval)
                    
                except KeyboardInterrupt:
                    self.logger.info("Capture interrupted by user")
                    break
                except Exception as e:
                    self.logger.error(f"Error during capture loop: {e}")
                    print(f"❌ Exception in loop: {e}")
                    print("⏱️  Waiting 2 seconds before retry...")
                    await asyncio.sleep(2)  # Wait before retrying
            
            print(f"\n🛑 Loop ended. Running: {self.running}")
                    
        except Exception as e:
            self.logger.error(f"Fatal error: {e}")
            print(f"💀 Fatal error: {e}")
        finally:
            await self._cleanup()
    
    async def _cleanup(self):
        """Clean up resources."""
        print("\n🧹 Starting cleanup...")
        try:
            if self.browser:
                await self.browser.close()
                self.logger.info("✓ Browser closed")
                print("✓ Browser closed")
        except Exception as e:
            self.logger.debug(f"Error during cleanup: {e}")
            print(f"Error during cleanup: {e}")
        
        # Write session footer
        try:
            with open(self.output_file, 'a', encoding='utf-8') as f:
                session_end = {
                    'session_end': datetime.datetime.now().isoformat(),
                    'total_messages_captured': self.message_count,
                    'unique_message_ids': len(self.captured_message_ids),
                    'total_loops': self.loop_count
                }
                f.write(json.dumps(session_end, ensure_ascii=False) + '\n')
        except Exception as e:
            self.logger.debug(f"Error writing session footer: {e}")
        
        print(f"\n✅ Capture complete!")
        print(f"📊 Total messages captured: {self.message_count}")
        print(f"🔄 Total loops executed: {self.loop_count}")
        print(f"📁 Output saved to: {self.output_file}")


async def main():
    """Main function."""
    print("🤖 Microsoft Copilot Chat Capture (DEBUG VERSION)")
    print("=" * 50)
    print("This debug version shows detailed loop information")
    print()
    
    try:
        capture = CopilotChatCapture(
            output_file="copilot_debug_capture.txt",
            capture_interval=2.0,  # Slower interval for debugging
            headless=False  # Keep browser visible for user interaction
        )
        
        await capture.start_capture()
        
    except KeyboardInterrupt:
        print("\n✅ Capture stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())