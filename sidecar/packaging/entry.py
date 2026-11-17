"""PyInstaller entry point. `python -m auto_presenter` in the frozen binary."""

import multiprocessing

from auto_presenter.main import main

if __name__ == "__main__":
    # Required in a frozen Windows binary if anything ever spawns a process
    # pool; a no-op everywhere else.
    multiprocessing.freeze_support()
    main()
