"""Deterministic backend-agent tests; live Gemini is separately opt-in."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2] / "agent"))
