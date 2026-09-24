"""Compatibility wrapper for quant.workers.build_runner."""

from .workers.build_runner import *  # noqa: F401,F403

if __name__ == "__main__":
    from .workers.build_runner import main

    main()
