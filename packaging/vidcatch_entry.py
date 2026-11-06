"""Entry point for the packaged app (PyInstaller cannot start a module with relative imports)."""

import multiprocessing

from vidcatch.server.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
