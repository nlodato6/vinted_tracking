# 📋 Vinted Sales Tracking Pipeline — Remaining Setup Guide

> **Current Status**:
> * ✅ Google Cloud Service Account authenticated (`vinged-bot@...`)
> * ✅ Google Spreadsheet provisioned with all 6 tabs and formulas: [Open Sheet](https://docs.google.com/spreadsheets/d/1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg/edit)
> * ✅ GitHub repository created and backed up: [nlodato6/vinted_tracking](https://github.com/nlodato6/vinted_tracking)
> * ⏳ **Remaining**: Vinted session login, initial data population, and automation setup.

---

## 🚀 Step 1: Perform 1-Time Vinted Authentication

To scrape your live closet listings (`/member/items`), active prices, favorites, and sold orders (`/my-orders`), Playwright needs an authenticated session token:

1. Open your terminal in the project directory:
   ```bash
   cd /Users/nicol/Desktop/vinted_tracking
   source venv/bin/activate
   ```
2. Run the interactive login command:
   ```bash
   python -m src.cli login
   ```
3. A browser window will open to `vinted.com`.
4. Log in to your account (`tlodato24`) and solve any 2FA code or security check.
5. Once you are logged in and see your profile icon, return to the terminal and press **[ENTER]**.
6. The session cookies will be saved to `.session/vinted_state.json`.

> [!TIP]
> You can verify the session is active anytime by running:
> ```bash
> python -m src.cli check-session
> ```

---

## 🔄 Step 2: Run Your First Full Sync

Once authenticated, pull your live closet inventory and order history into Google Sheets:

```bash
# Sync both active listings and sold orders
python -m src.cli sync --type all
```

* **`Item_Listings` Tab**: Holds **active listings only** with live asking prices, heart/favorite counts, view counts, and category classifications.
* **`Sold_Items` Tab**: When an item sells, it is automatically moved here from `Item_Listings` with all the same fields intact, keeping your active listings uncluttered.
* **`Sales_Log` Tab**: Records all transactions (gross price, fees, net payout, COGS, packaging cost, and net profit margins).

---

## 📊 Step 3: Input Inventory Hauls & Packaging Supplies

To calculate **exact net profit margins** per sale, fill in your purchases in the spreadsheet:

### 1. In the `Inventory_Lots` Tab (Fully Automated!):
Whenever you buy clothing in bulk (thrift stores, wholesale, garage sales, bins):
* Enter a **`Lot_ID`** (e.g. `LOT-001`, `LOT-002`) and **`Purchase_Date`** (YYYY-MM-DD).
* Enter **`Total_Lot_Cost`** (e.g. `$120.00`) and **`Total_Articles`** (e.g. `24` items).
* **`Unit_Cost`** is **automatically calculated live** (`$5.00/item`) as soon as you type or change cost/articles.
* **`Items_Processed`** is **automatically updated** on every sync by counting garments linked to this lot across `Item_Listings` and `Sold_Items`.
* **`Status`** is **100% automated**: starts as `Pending`, shifts to `Active` as items are processed, and automatically moves to **`Completed`** as soon as all articles in that lot are sold!
* Any garment listed on the purchase date will automatically be assigned this `Lot_ID`.

### 2. In the `Shipping_Supplies` Tab:
Whenever you buy packaging materials:
* Enter poly mailers, packing tape, thermal labels, and boxes with quantity and total cost.
* Cell **`H2`** automatically calculates your **average packaging cost per shipment** (e.g. `$0.75`), which is automatically deducted from every sale in `Sales_Log` to calculate true net profit.

---

## ⚡ Step 4: Choose Your Automation Method

### Option A: On-Demand Manual Triggers (Simplest)
Whenever you list new clothing or adjust prices on Vinted, simply run:
```bash
python -m src.cli sync --type listings
```
When you make a sale:
```bash
python -m src.cli sync --type sales
```

---

### Option B: 24/7 GitHub Actions Cloud Sync (Configured & Ready! ☁️)
Your cloud sync is already configured in [`.github/workflows/vinted_sync.yml`](file:///Users/nicol/Desktop/vinted_tracking/.github/workflows/vinted_sync.yml) and runs on GitHub's servers **even when your laptop is closed or turned off**:

1. After logging in locally with `python -m src.cli login`, upload your session to GitHub once:
   ```bash
   python -m src.cli upload-session
   ```
2. **That's it!** GitHub Actions will automatically:
   * Run once a day at 14:00 UTC (9:00 AM Central Time).
   * Scrape your live closet and sold orders in the cloud.
   * Update your Google Sheet in the background.
3. **Trigger from your phone or browser**:
   * Open [GitHub Actions](https://github.com/nlodato6/vinted_tracking/actions).
   * Click **Vinted Automated Cloud Sync** $\rightarrow$ **Run workflow**.

---

### Option C: Real-Time Sales Webhook (`tlodato24@gmail.com`)
Since your Vinted notifications go to `tlodato24@gmail.com`, you can trigger instant synchronization whenever a sale email arrives:

1. Start the webhook server:
   ```bash
   python -m src.cli serve --port 8000
   ```
2. Set up an inbound email forwarder to `POST http://<your-host>:8000/webhook/vinted-email`.
   * **Free tools**: Zapier, Make.com, or Cloudflare Email Workers.
   * **Filter**: Emails from `*@vinted.com` with subject containing *"You've made a sale"* or *"Item sold"*.
   * When triggered, the server immediately fetches the sold order and logs it into your sheet without manual intervention.

---

## 🚨 Automated Authentication Alerts & Self-Healing

If Vinted prompts for verification or your session expires:
1. **Google Sheets Banner**: Row 2 of `Analytics_Dashboard` prominently switches to a coral red alert banner (`🔴 AUTHENTICATION REQUIRED`) with error details and clear recovery instructions.
2. **GitHub Issue & Push Notification**: GitHub automatically opens an issue (`⚠️ Vinted Authentication Expired — Re-login Required`) on your repository, triggering email and mobile notifications.
3. **One-Command Recovery**:
   ```bash
   python -m src.cli login
   python -m src.cli upload-session
   ```
   Uploading your refreshed token automatically resets the Google Sheet banner to mint green (`🟢 SYSTEM STATUS: Active & Authenticated`) and closes the GitHub Issue!

---

## 🛠️ CLI Quick Reference Cheat Sheet

| Command | What It Does |
| :--- | :--- |
| `python -m src.cli status` | Displays Google Sheets connection, session status, and config |
| `python -m src.cli login` | Opens browser for 1-time Vinted login session capture |
| `python -m src.cli upload-session` | Uploads session to GitHub Secrets and resets auth status |
| `python -m src.cli check-session` | Checks if your saved Vinted login token is still valid |
| `python -m src.cli sync --type listings` | Refreshes asking prices, favorites, and new listings |
| `python -m src.cli sync --type sales` | Scrapes sold orders and calculates net payouts |
| `python -m src.cli sync --type lots` | Recalculates unit costs, processed counts, and completion statuses |
| `python -m src.cli sync --type all` | Full sync of listings, sales, and lots |
| `python -m src.cli log-sale --sku SKU-1001 --price 45` | Logs external sale (e.g. FB Marketplace) and moves item to `Sold_Items` |
| `python -m src.cli serve --port 8000` | Starts the webhook listener server |
| `python -m src.cli scheduler` | Starts background automatic periodic sync daemon |
| `pytest -v` | Runs all 34 verification unit tests |
