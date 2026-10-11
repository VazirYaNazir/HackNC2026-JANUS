"""Deterministic backend-agent tests; live Gemini is separately opt-in."""
import os
import sys
from pathlib import Path

# app.config loads backend/.env, which may hold a real TIGER_DATABASE_URL. An explicit (empty) value wins over the file,
# so unit tests never talk to a real database. Export TIGER_DATABASE_URL yourself to run the opt-in integration test.
os.environ.setdefault("TIGER_DATABASE_URL", "")

sys.path.append(str(Path(__file__).resolve().parents[2] / "agent"))
