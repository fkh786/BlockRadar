# Guide 6: Install on Mac or Linux

[← Back to README](../../README.md) · Previous: [Update, back up, uninstall](05-update-backup-uninstall.md)

`start.bat` is for Windows only. On Mac or Linux you run four commands once, then one command each day.

---

## Requirements

- **macOS 12 or newer**, or a recent Linux (Ubuntu 22.04+, Debian 12+, Fedora 38+…)
- **Python 3.10 – 3.13**, 4 GB RAM, 200 MB free disk, internet

**Check your Python:** open **Terminal** and run
```bash
python3 --version
```
- **Mac:** if it's missing or older than 3.10, install it from <https://www.python.org/downloads/> or with Homebrew: `brew install python@3.12`
- **Ubuntu/Debian:** `sudo apt install python3 python3-venv`

---

## First-time setup

1. Download the ZIP from <https://github.com/fkh786/BlockRadar> (**Code → Download ZIP**) and unzip it, or run
   `git clone https://github.com/fkh786/BlockRadar.git`
2. In Terminal, go into the folder and set it up:

```bash
cd ~/Downloads/BlockRadar-main        # or wherever you unzipped / cloned it
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Start BlockRadar (every day)

```bash
cd ~/Downloads/BlockRadar-main
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000> in your browser. Keep the Terminal window open; press `Ctrl + C` to stop.

From here, everything is the same as on Windows: [Guide 2: Connect your broker](02-connect-broker.md).

---

## Optional: a one-word start command

Add this line to `~/.zshrc` (Mac) or `~/.bashrc` (Linux), changing the path to your folder:
```bash
alias blockradar='cd ~/Downloads/BlockRadar-main && source .venv/bin/activate && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
```
Open a new Terminal and type `blockradar`.

---

⚠️ Always keep `--host 127.0.0.1`. Don't change it to `0.0.0.0`: that would expose your broker keys to other devices on your network.
