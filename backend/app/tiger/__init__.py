"""Optional Tiger Data (TimescaleDB on Tiger Cloud) time-series store. Inert unless TIGER_DATABASE_URL is set."""
from .connection import tiger_url
from .sink import TigerSink
from .store import TigerStore, TigerUnavailable, parse_range
from .writer import NullWriter, TigerWriter, get_writer

__all__ = ["NullWriter", "TigerSink", "TigerStore", "TigerUnavailable", "TigerWriter", "get_writer", "parse_range", "tiger_url"]
