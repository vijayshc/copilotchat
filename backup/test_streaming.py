#!/usr/bin/env python3
"""
Test the streaming detection logic
"""

def test_streaming_detection():
    print("Testing AI Message Streaming Detection")
    print("=" * 45)
    
    # Simulate the streaming detection approach
    ai_streaming_tracker = {}
    streaming_wait_cycles = 3
    
    def message_looks_complete(content):
        """Heuristic to determine if a message looks complete."""
        if not content.strip():
            return False
        
        content = content.strip()
        
        # Check for typical AI response endings
        complete_endings = ['.', '!', '?', ':', ')', '"', '`']
        
        # Check for incomplete indicators
        incomplete_indicators = [
            content.endswith('E'),  # Often ends with 'E' when streaming "Edit in a page"
            content.endswith(' '),  # Ends with space (usually mid-word)
            len(content) < 10,      # Too short to be complete
        ]
        
        # If any incomplete indicator is true, it's not complete
        if any(incomplete_indicators):
            return False
        
        # If it ends with a complete ending, it's likely complete
        if any(content.endswith(end) for end in complete_endings):
            return True
        
        return False
    
    def check_streaming_complete(messages, cycle_num):
        """Check which messages are ready."""
        ready_messages = set()
        
        print(f"\n--- Cycle #{cycle_num} ---")
        for i, content in enumerate(messages):
            content_length = len(content)
            
            # Track this message's length history
            if i not in ai_streaming_tracker:
                ai_streaming_tracker[i] = {
                    'lengths': [],
                    'stable_cycles': 0
                }
            
            tracker = ai_streaming_tracker[i]
            tracker['lengths'].append(content_length)
            
            # Keep only recent lengths
            if len(tracker['lengths']) > 5:
                tracker['lengths'] = tracker['lengths'][-5:]
            
            # Check if length has been stable
            if len(tracker['lengths']) >= 2:
                if tracker['lengths'][-1] == tracker['lengths'][-2]:
                    tracker['stable_cycles'] += 1
                else:
                    tracker['stable_cycles'] = 0
            
            # Message is ready if stable or looks complete
            is_stable = tracker['stable_cycles'] >= streaming_wait_cycles
            looks_complete = message_looks_complete(content)
            
            if is_stable or looks_complete:
                ready_messages.add(i)
                print(f"✅ Message #{i} READY (len:{content_length}, stable:{tracker['stable_cycles']}, complete:{looks_complete})")
            else:
                print(f"⏳ Message #{i} streaming (len:{content_length}, stable:{tracker['stable_cycles']}, complete:{looks_complete})")
            
            # Show content preview
            preview = content[:50] + "..." if len(content) > 50 else content
            print(f"   Content: '{preview}'")
        
        return ready_messages
    
    # Simulate streaming AI message
    print("\n🧪 Simulating AI message streaming...")
    
    # Cycle 1: Message starts
    messages = ["Hello! Let me help you with"]
    ready = check_streaming_complete(messages, 1)
    
    # Cycle 2: Message continues
    messages = ["Hello! Let me help you with PySpark. Here's what you need to know"]
    ready = check_streaming_complete(messages, 2)
    
    # Cycle 3: Message continues more
    messages = ["Hello! Let me help you with PySpark. Here's what you need to know about reading from Teradata"]
    ready = check_streaming_complete(messages, 3)
    
    # Cycle 4: Message is same length (stable)
    messages = ["Hello! Let me help you with PySpark. Here's what you need to know about reading from Teradata"]
    ready = check_streaming_complete(messages, 4)
    
    # Cycle 5: Still same (more stable)
    messages = ["Hello! Let me help you with PySpark. Here's what you need to know about reading from Teradata"]
    ready = check_streaming_complete(messages, 5)
    
    # Cycle 6: Still same (should be ready now)
    messages = ["Hello! Let me help you with PySpark. Here's what you need to know about reading from Teradata"]
    ready = check_streaming_complete(messages, 6)
    
    # Cycle 7: Message completes with proper ending
    messages = ["Hello! Let me help you with PySpark. Here's what you need to know about reading from Teradata tables."]
    ready = check_streaming_complete(messages, 7)
    
    print(f"\n🎉 Final result: Messages ready to save: {ready}")

if __name__ == "__main__":
    test_streaming_detection()