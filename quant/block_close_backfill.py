"""Compatibility wrapper for quant.prices.block_close_backfill."""

from .prices.block_close_backfill import *  # noqa: F401,F403

if __name__ == "__main__":
    from .prices.block_close_backfill import main

    main()
