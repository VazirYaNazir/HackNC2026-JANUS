# app/config.py
import os
from dotenv import load_dotenv

# Load variables from .env file if present
load_dotenv()

class Settings:
    # Uses .env variable, or defaults to fallback string if .env is missing
    ADMIN_SECRET: str = os.getenv("ADMIN_SECRET", "hackathon-builder-super-secret-123")

settings = Settings()