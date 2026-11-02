# Guide 5: Update, back up and uninstall

[← Back to README](../../README.md) · Previous: [Using each screen](04-using-the-screens.md) · Next: [Mac & Linux →](06-mac-linux.md)

Everything personal (your API keys, journal and paper trades) lives in **one folder: `data`** inside your BlockRadar folder. Keep that folder and you keep everything.

---

## Update to a new version

1. **Close BlockRadar:** close the black `start.bat` window.
2. **Back up first** (see below). It takes 10 seconds.
3. Download the new version: <https://github.com/fkh786/BlockRadar> → green **Code** button → **Download ZIP**.
4. Right-click the ZIP → **Properties** → tick **Unblock** (if shown) → **OK**.
5. Right-click the ZIP → **Extract All…** → choose a temporary folder → **Extract**.
6. Open the extracted `BlockRadar-main` folder and **copy everything inside it**.
7. Paste it into **your existing BlockRadar folder**. When Windows asks, choose **Replace the files in the destination**.
   - Your `data` folder is safe: the download contains no files that overwrite your keys or trades.
8. Double-click `start.bat`. (If the new version needs extra packages, delete the `.venv` folder first; `start.bat` then reinstalls them in about a minute.)

---

## Back up your data

1. Close BlockRadar.
2. Copy the **`data`** folder somewhere safe (a USB drive, or a *private* cloud folder).

What's inside:
| File | What it holds |
|---|---|
| `settings.json` | Broker keys, secrets and login tokens, plus your alert settings. **Keep this private.** |
| `journal.db` | Your trade journal |
| `paper.db` | Your paper trades |
| other files | Daily instrument lists, re-downloaded automatically (no need to back up) |

**To restore:** copy your backed-up `data` folder back into the BlockRadar folder, replacing the existing one.

⚠️ **Never upload the `data` folder to GitHub, chats or public cloud links.** If `settings.json` ever leaks, generate new API keys and secrets at your broker straight away.

---

## Move to a new PC

1. On the old PC, back up the `data` folder.
2. On the new PC, follow [Guide 1: Install](01-install-windows.md).
3. Before starting it the first time, copy your `data` folder into the new BlockRadar folder.

---

## Uninstall

1. Close BlockRadar.
2. (Optional) Back up the `data` folder if you might come back.
3. Delete the BlockRadar folder. That removes everything: the app, its private Python environment (`.venv`) and your data.
4. (Optional) Revoke the API app at your broker (Upstox / Fyers / Zerodha developer page, or Dhan's API settings).
5. (Optional) Uninstall Python from **Settings → Apps** if nothing else uses it.

BlockRadar doesn't write to the Windows registry or anywhere outside its own folder.
