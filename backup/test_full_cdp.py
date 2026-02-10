#!/usr/bin/env python3
"""
Quick test to verify the CDP connection and basic page interaction
"""

import asyncio
from playwright.async_api import async_playwright

async def test_full_cdp():
    """Test full CDP connection with page interaction"""
    print("🧪 Testing Full CDP Connection")
    print("=" * 40)
    
    try:
        playwright = await async_playwright().start()
        print("✅ Playwright started")
        
        # Connect to CDP
        browser = await playwright.chromium.connect_over_cdp("http://localhost:9222")
        print("✅ Connected to browser via CDP")
        
        # Get the first page
        contexts = browser.contexts
        if contexts and contexts[0].pages:
            page = contexts[0].pages[0]
            print(f"✅ Using page: {page.url}")
            
            # Test basic page operations
            try:
                title = await page.title()
                print(f"✅ Page title: {title}")
                
                # Test element query (this is what the main script does)
                elements = await page.query_selector_all('body')
                print(f"✅ Found {len(elements)} body elements")
                
                # Test a simple selector that should exist
                all_divs = await page.query_selector_all('div')
                print(f"✅ Found {len(all_divs)} div elements")
                
                print("✅ All page operations successful!")
                
            except Exception as e:
                print(f"⚠️  Page operation failed: {e}")
        else:
            print("❌ No pages available")
        
        await playwright.stop()
        print("✅ Test completed successfully!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_full_cdp())