import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.bot.main import handle_message

async def test_bot():
    print("\n--- Starting Bot Tests ---")
    
    # Setup mock update
    update = MagicMock()
    update.message = MagicMock()
    update.effective_user.id = 99999  # fake user ID for testing
    
    # We will capture replies by redefining the side_effect
    async def mock_reply(text):
        print(f"BOT REPLY: {text}")
    
    update.message.reply_text = AsyncMock(side_effect=mock_reply)
    
    print("\n[Test 1] Unsupported Platform (TikTok)")
    update.message.text = "https://www.tiktok.com/@someuser/video/123456789"
    await handle_message(update, None)
    
    print("\n[Test 2] Private/Deleted Video (Fake YouTube ID)")
    update.message.text = "https://www.youtube.com/watch?v=this_is_fake_and_private"
    await handle_message(update, None)
    
    print("\n[Test 3] Duplicate Detection - First Save")
    update.message.text = "https://youtube.com/shorts/cQZ9q6ERKMA"
    await handle_message(update, None)
    
    print("\n[Test 4] Duplicate Detection - Second Save (Should trigger deduplication)")
    update.message.text = "https://youtube.com/shorts/cQZ9q6ERKMA"
    await handle_message(update, None)

if __name__ == "__main__":
    asyncio.run(test_bot())
