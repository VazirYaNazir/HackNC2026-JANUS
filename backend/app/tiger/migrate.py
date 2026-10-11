"""`python -m app.tiger.migrate`: create or update the Tiger Data schema. A no-op when TIGER_DATABASE_URL is unset."""
import sys

from .connection import connect, tiger_url
from .schema import migrate


def main() -> int:
    url = tiger_url()
    if not url:
        print("TIGER_DATABASE_URL is not set; skipping the Tiger Data schema.")
        return 0
    try:
        with connect(url, autocommit=True) as connection:
            skipped = migrate(connection, print)
    except Exception as error:  # keep the message free of connection details
        print(f"Tiger Data schema was not applied: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print("Tiger Data schema is up to date." + (f" ({len(skipped)} optional step(s) skipped)" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
