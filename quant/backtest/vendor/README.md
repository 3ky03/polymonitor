# Quant backtest framework vendors

This directory holds framework source that is safe to run directly from the
project tree.

## backtrader

`backtrader/` is vendored from:

```text
https://github.com/mementum/backtrader
commit: b853d7c
license: GPLv3+
```

It is pure Python, so `quant.backtest.frameworks` can import it directly from
this directory before falling back to site-packages or the sibling clone.

## nautilus_trader

NautilusTrader is not vendored here as raw source because it is a Rust/Cython
engine. Copying `nautilus_trader/` alone does not produce importable extension
modules such as `nautilus_trader.core.data`.

This project runs Nautilus through a dedicated Python 3.12 worker process. The
local development environment is:

```text
/home/user/.conda/envs/polymonitor-nautilus312/bin/python
```

It was created with:

```text
conda create -y -n polymonitor-nautilus312 python=3.12 pip
/home/user/.conda/envs/polymonitor-nautilus312/bin/python -m pip install -U nautilus_trader
```

Override the worker interpreter with:

```text
POLYDATA_NAUTILUS_PYTHON=/path/to/python3.12
```

or build/install the cloned repository with its supported Python/Rust toolchain
inside that environment.

```text
POLYDATA_NAUTILUS_TRADER_PATH=/path/to/built/nautilus_trader
```

The quant adapter supports `backtestEngine: "nautilus_trader"` from the main
Python 3.10 runtime by serializing price rows to JSON, launching
`quant/backtest/nautilus_worker.py` with Python 3.12, and reading the normalized
result JSON back.
