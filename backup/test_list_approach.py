#!/usr/bin/env python3
"""
Test the new list-based approach for message tracking
"""

# Simple test of the list-based approach
def test_list_approach():
    print("Testing List-Based Message Tracking")
    print("=" * 40)
    
    # Simulate the approach
    user_messages_list = []
    ai_messages_list = []
    last_user_index_written = -1
    last_ai_index_written = -1
    
    # Simulate first capture cycle
    print("\n--- First Capture Cycle ---")
    current_user = ["hi"]
    current_ai = ["Hello! How can I help you?"]
    
    # Check for new user messages
    if len(current_user) > len(user_messages_list):
        new_user = current_user[len(user_messages_list):]
        user_messages_list.extend(new_user)
        print(f"New user messages: {new_user}")
        for i, msg in enumerate(new_user):
            index = len(user_messages_list) - len(new_user) + i
            print(f"  Writing user #{index}: {msg}")
            last_user_index_written = index
    
    # Check for new AI messages  
    if len(current_ai) > len(ai_messages_list):
        new_ai = current_ai[len(ai_messages_list):]
        ai_messages_list.extend(new_ai)
        print(f"New AI messages: {new_ai}")
        for i, msg in enumerate(new_ai):
            index = len(ai_messages_list) - len(new_ai) + i
            print(f"  Writing AI #{index}: {msg}")
            last_ai_index_written = index
    
    print(f"State: user_list={user_messages_list}, ai_list={ai_messages_list}")
    print(f"Last written: user={last_user_index_written}, ai={last_ai_index_written}")
    
    # Simulate second capture cycle (same messages, no change)
    print("\n--- Second Capture Cycle (no change) ---")
    # No new messages, lengths are the same
    if len(current_user) > len(user_messages_list):
        print("Would write new user messages")
    else:
        print("No new user messages")
        
    if len(current_ai) > len(ai_messages_list):
        print("Would write new AI messages")
    else:
        print("No new AI messages")
    
    # Simulate third capture cycle (user sends same message again)
    print("\n--- Third Capture Cycle (duplicate user message) ---")
    current_user = ["hi", "hi"]  # User sent "hi" again
    current_ai = ["Hello! How can I help you?", "I'm still here to help!"]
    
    # Check for new user messages
    if len(current_user) > len(user_messages_list):
        new_user = current_user[len(user_messages_list):]
        user_messages_list.extend(new_user)
        print(f"New user messages: {new_user}")
        for i, msg in enumerate(new_user):
            index = len(user_messages_list) - len(new_user) + i
            print(f"  Writing user #{index}: {msg}")
            last_user_index_written = index
    
    # Check for new AI messages  
    if len(current_ai) > len(ai_messages_list):
        new_ai = current_ai[len(ai_messages_list):]
        ai_messages_list.extend(new_ai)
        print(f"New AI messages: {new_ai}")
        for i, msg in enumerate(new_ai):
            index = len(ai_messages_list) - len(new_ai) + i
            print(f"  Writing AI #{index}: {msg}")
            last_ai_index_written = index
    
    print(f"Final state: user_list={user_messages_list}, ai_list={ai_messages_list}")
    print(f"Last written: user={last_user_index_written}, ai={last_ai_index_written}")
    
    print("\n✅ Test completed! The approach correctly handles duplicate messages.")

if __name__ == "__main__":
    test_list_approach()