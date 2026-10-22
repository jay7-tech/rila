import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.bot.main import handle_message, handle_location, handle_search
from src.db.database import Place

async def test_auth():
    print("\n--- Starting Auth Tests ---")
    
    # 1. Setup mock update
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "https://www.instagram.com/reel/C1234567890/"
    
    # We will capture replies by redefining the side_effect
    async def mock_reply(text):
        print(f"BOT REPLY: {text}")
    
    update.message.reply_text = AsyncMock(side_effect=mock_reply)
    
    # 2. Test with mismatched ALLOWED_USER_ID
    os.environ["ALLOWED_USER_ID"] = "11111"
    update.effective_user.id = 99999
    
    print("\n[Test 1] Unauthorized user calling handle_message")
    await handle_message(update, None)
    
    print("\n[Test 2] Unauthorized user calling handle_location")
    update.message.location.latitude = 34.0
    update.message.location.longitude = -118.0
    await handle_location(update, None)
    
    print("\n[Test 3] Unauthorized user calling handle_search")
    context = MagicMock()
    context.args = ["tacos"]
    await handle_search(update, context)

    # 3. Test with matched ALLOWED_USER_ID
    print("\n[Test 4] Authorized user calling handle_location (should hit DB)")
    os.environ["ALLOWED_USER_ID"] = "99999"
    await handle_location(update, None)
    
    print("\n[Test 5] Authorized user calling handle_search (should hit DB)")
    await handle_search(update, context)

if __name__ == "__main__":
    asyncio.run(test_auth())
