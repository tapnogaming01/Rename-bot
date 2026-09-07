import os

class Config:
    # --- TELEGRAM BOT CREDENTIALS ---
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    
    # --- DATABASE CONFIGURATION ---
    MONGO_URL = os.environ.get("MONGO_URL", "")
    
    # --- RENDER PORT CONFIGURATION ---
    PORT = int(os.environ.get("PORT", 8080))
    
    # --- ADMIN & LIMIT SETTINGS ---
    # Apni Telegram Numeric User ID Render Environment Variables me 'ADMIN_ID' key me dalein
    ADMIN_ID = int(os.environ.get("ADMIN_ID", 5898522531))
    
    # Free users ke liye default daily limit (Agar DB me set na ho toh yeh use hogi)
    FREE_LIMIT = int(os.environ.get("FREE_LIMIT", 10))
    
