# Installation Guide — jellyfin-cleanup

This document covers every supported way to install **jellyfin-cleanup**, from the quickest one-liner to a fully isolated development environment.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Install with pip](#install-with-pip)
   - [System-wide](#system-wide-install)
   - [In a venv](#pip--venv)
3. [Install with uv](#install-with-uv)
   - [Quick one-liner](#uv-quick-one-liner)
   - [Manual venv workflow](#uv-manual-venv)
4. [Development Setup](#development-setup)
   - [With pip](#development-with-pip)
   - [With uv](#development-with-uv)
5. [Verifying the Installation](#verifying-the-installation)
6. [Uninstalling](#uninstalling)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement | Minimum version | How to check |
|---|---|---|
| **Python** | 3.11+ | `python3 --version` |
| **pip** | 22+ (bundled with Python) | `pip --version` |
| **uv** *(optional)* | 0.1+ | `uv --version` |
| **git** *(to clone)* | any | `git --version` |

> **Tip:** If you don't have `uv` yet, install it with:
> ```bash
> # Linux / macOS
> curl -LsSf https://astral.sh/uv/install.sh | sh
>
> # Windows (PowerShell)
> irm https://astral.sh/uv/install.ps1 | iex
>
> # Or via pip
> pip install uv
> ```

---

## Install with pip

### System-wide install

```bash
# 1. Clone the repository
git clone https://github.com/obnoxiousmods/jellyfinCleaner.git
cd jellyfinCleaner

# 2. Install
pip install .
```

This places the `jellyfin-cleanup` command on your `PATH`.

### pip + venv

Using a virtual environment keeps your system Python clean:

```bash
# 1. Clone
git clone https://github.com/obnoxiousmods/jellyfinCleaner.git
cd jellyfinCleaner

# 2. Create & activate a virtual environment
python3 -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (cmd)
.\.venv\Scripts\activate.bat

# 3. Install inside the venv
pip install .

# 4. Confirm
jellyfin-cleanup --help
```

When you're done, deactivate with:

```bash
deactivate
```

---

## Install with uv

[**uv**](https://github.com/astral-sh/uv) is a blazing-fast Python package manager written in Rust.

### uv quick one-liner

```bash
git clone https://github.com/obnoxiousmods/jellyfinCleaner.git
cd jellyfinCleaner

# Creates a venv, installs everything, and makes the command available
uv venv && source .venv/bin/activate && uv pip install .
```

### uv manual venv

Step-by-step for more control:

```bash
# 1. Clone
git clone https://github.com/obnoxiousmods/jellyfinCleaner.git
cd jellyfinCleaner

# 2. Create a virtual environment (picks the best available Python ≥ 3.11)
uv venv

# 3. Activate
# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# 4. Install the package
uv pip install .

# 5. Confirm it works
jellyfin-cleanup --help
```

---

## Development Setup

If you want to contribute or run the test suite, install with the `dev` extras so you get **pytest**, **ruff**, **respx**, and **pytest-asyncio**.

### Development with pip

```bash
git clone https://github.com/obnoxiousmods/jellyfinCleaner.git
cd jellyfinCleaner

python3 -m venv .venv
source .venv/bin/activate      # adjust for Windows as shown above

# Editable install with dev dependencies
pip install -e ".[dev]"

# Run the test suite
pytest -v

# Lint the code
ruff check .
```

### Development with uv

```bash
git clone https://github.com/obnoxiousmods/jellyfinCleaner.git
cd jellyfinCleaner

uv venv
source .venv/bin/activate

# Editable install with dev dependencies
uv pip install -e ".[dev]"

# Run the test suite
pytest -v

# Lint the code
ruff check .
```

---

## Verifying the Installation

After installing, confirm everything works:

```bash
# Check that the command is available
jellyfin-cleanup --help

# Or run the module directly
python -m jellyfin_cleanup --help
```

Both should print the help text with all available options.

---

## Uninstalling

```bash
pip uninstall jellyfin-cleanup
```

If you used a virtual environment, you can also simply delete the `.venv` directory:

```bash
deactivate           # if the venv is active
rm -rf .venv
```

---

## Troubleshooting

### `command not found: jellyfin-cleanup`

- Make sure the virtual environment is activated (`source .venv/bin/activate`).
- Or check that the pip `bin` directory is on your `PATH`:
  ```bash
  python3 -m site --user-base   # shows where pip installs scripts
  ```

### `ModuleNotFoundError: No module named 'jellyfin_cleanup'`

- You may have installed into a different Python than the one you're running. Verify with:
  ```bash
  which python3
  pip show jellyfin-cleanup
  ```

### `error: externally-managed-environment` (Debian/Ubuntu)

Newer distros prevent installing into the system Python. Use a virtual environment instead (see [pip + venv](#pip--venv) or [uv](#install-with-uv) sections above).

### SSL / certificate errors when cloning

If your network requires a proxy or custom CA bundle:

```bash
git config --global http.sslCAInfo /path/to/your/ca-bundle.crt
```

### Python version too old

`jellyfin-cleanup` requires **Python ≥ 3.11**. Check your version:

```bash
python3 --version
```

If it's older, install a newer version via [python.org](https://www.python.org/downloads/), your system package manager, or [pyenv](https://github.com/pyenv/pyenv).
