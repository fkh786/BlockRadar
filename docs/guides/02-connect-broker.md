# Guide 2: Connect your broker

⏱️ About 10 minutes, one time · Then about 10 seconds each morning

[← Back to README](../../README.md) · Previous: [Install](01-install-windows.md) · Next: [Daily routine →](03-daily-routine.md)

BlockRadar gets live prices through **your own broker account's API** (a doorway the broker gives you for apps like this one). Your keys are stored only on your PC.

**Jump to your broker:** [Upstox](#upstox) · [Fyers](#fyers) · [Zerodha](#zerodha) · [Dhan](#dhan) · [Kotak Neo / Sharekhan](#kotak-neo-and-sharekhan)

| Broker | Cost of market data | Status |
|---|---|---|
| Upstox | Free | ✅ Ready, tested with a live account |
| Fyers | Free | 🧪 Beta |
| Zerodha | Kite Connect, ₹500/month (the free "Personal" plan has **no** market data) | 🧪 Beta |
| Dhan | Data API subscription, ₹499/month | 🧪 Beta |
| Kotak Neo, Sharekhan | — | ❌ Not supported |

🧪 **Beta** = built from the broker's official documentation and tested against a simulated API, but not yet confirmed with a real account. If it works (or doesn't) for you, please [open an issue](https://github.com/fkh786/BlockRadar/issues).

> **Keep BlockRadar running** (the black `start.bat` window open) while you do these steps. The login only works while the app is running.

> Broker websites change their layout from time to time. If a button has a slightly different name, look for something similar ("Create app", "New app", "API keys").

---

## Upstox

**A. Create your API app (one time)**
1. Log in at <https://account.upstox.com/developer/apps>.
2. Click **New App** (or *Create app*).
3. Fill in:
   - **App name:** `BlockRadar` (any name works)
   - **Redirect URL:** `http://127.0.0.1:8000/auth/callback` ← copy this exactly
   - **Postback URL:** leave empty
4. Accept the terms and create the app.
5. Open the app and keep the page open. You need its **API Key** and **API Secret**.

**B. Connect BlockRadar**
1. In BlockRadar, click **Settings** (top menu).
2. Under **Data source**, click the **Upstox** card.
3. Paste the **API key** and **API secret**. Leave *Redirect URL* as it is.
4. Click **Save keys**, then **Connect Upstox**.
5. Upstox's login page opens. Log in and approve.
6. You're sent back to the BlockRadar board. A progress bar shows the first load (about 2–3 minutes).

✅ Done. Each morning, click **Connect Upstox** in the yellow bar, because Upstox logins expire at 3:30 AM.

---

## Fyers

**A. Create your API app (one time)**
1. Log in at <https://myapi.fyers.in/dashboard>.
2. Click **Create App**.
3. Fill in:
   - **App name:** `BlockRadar`
   - **Redirect URL:** `http://127.0.0.1:8000/auth/fyers/callback` ← copy this exactly
   - **Permissions:** allow market data (and profile) access
4. Create the app. Note the **App ID** (it looks like `ABCD1234-100`, ending in **-100**) and the **Secret ID**.

**B. Connect BlockRadar**
1. BlockRadar → **Settings** → click the **Fyers** card.
2. Paste the **App ID** and **Secret ID**. Leave *Redirect URL* as it is.
3. Click **Save keys** → **Connect Fyers** → log in on the Fyers page (with your PIN/TOTP) → approve.
4. You're sent back to the board, and the first load starts (about 3 minutes).

✅ Done. Reconnect each morning when asked.

---

## Zerodha

⚠️ You need the **paid Kite Connect** plan (₹500 per month). The free *Kite Connect Personal* plan does **not** include market data, so BlockRadar can't use it.

**A. Create your Kite Connect app (one time)**
1. Log in at <https://developers.kite.trade>.
2. Create a new **Kite Connect** app (the paid type) and add credits if asked.
3. Set the **Redirect URL** to: `http://127.0.0.1:8000/auth/zerodha/callback`
4. Note the **API key** and **API secret**.

**B. Connect BlockRadar**
1. BlockRadar → **Settings** → click the **Zerodha** card.
2. Paste the **API key** and **API secret** → **Save keys** → **Connect Zerodha** → log in → approve.
3. You're sent back to the board, and the first load starts.

✅ Done. Zerodha logins expire around 6 AM, so reconnect each morning.

ℹ️ Zerodha has no option-chain API, so BlockRadar builds the option chain itself and calculates IV and Greeks.

---

## Dhan

⚠️ You need Dhan's **Data API** subscription (₹499 per month) for charts and live prices.

**A. Get your access token**
1. Log in at <https://web.dhan.co>.
2. Open **My Profile** → **Access DhanHQ APIs** (or *DhanHQ Trading APIs*).
3. Check that the **Data API** subscription shows as active.
4. Click **Generate Access Token**. Copy the token. Also note your **Client ID** (shown on your profile).

**B. Connect BlockRadar**
1. BlockRadar → **Settings** → click the **Dhan** card.
2. Paste your **Client ID** and the **Access token**.
3. Click **Save & connect**.
4. The first load takes about **7–8 minutes**, because Dhan allows only 1 chart request per second. After that it updates every minute.

✅ Done. Dhan tokens last 24 hours. When BlockRadar says the token has expired, generate a new one and paste it the same way.

---

## Kotak Neo and Sharekhan

These appear in the picker as **Not supported**:
- **Kotak Neo:** its API provides no historical candles, and order blocks need past price data.
- **Sharekhan:** its public API has no REST price-quote endpoint and doesn't document its intraday candle intervals.

If you have one of these accounts and want to help add support, please open an issue.

---

## Common problems

| You see | What to do |
|---|---|
| Broker page says **"redirect URL mismatch"** or **"invalid redirect"** | The Redirect URL in your broker app must match BlockRadar's **exactly**, including `http://`, `127.0.0.1` and `:8000`. |
| **"Login link expired or didn't start from this app"** | Click **Connect** again from inside BlockRadar. Don't reuse an old login tab. |
| Browser shows **"can't reach 127.0.0.1"** after logging in | The black `start.bat` window was closed. Start it again, then click Connect. |
| **"Data API subscription not active"** (Dhan) | Activate the Data API plan on web.dhan.co. |
| **"No market-data permission"** (Zerodha) | Your app is on the free Personal plan. Upgrade to Kite Connect. |
| Yellow bar says **"login needed"** | Normal each morning. Click the link in the bar. |

---

Next: [Guide 3: Your daily routine →](03-daily-routine.md)
