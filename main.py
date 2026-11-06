"""Launcher: `python main.py` (same as `python -m vidcatch`)."""

from vidcatch.server.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
