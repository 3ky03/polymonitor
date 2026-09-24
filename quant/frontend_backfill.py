"""Compatibility wrapper for quant.prices.frontend_backfill."""

from .prices.frontend_backfill import *  # noqa: F401,F403

if __name__ == "__main__":
    from .prices.frontend_backfill import main

    main()
