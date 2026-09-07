import os

class Config:
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    MONGO_URL = os.environ.get("MONGO_URL", "")
    PORT = int(os.environ.get("PORT", 8080))
