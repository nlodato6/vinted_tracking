# 🏷️ Vinted Automated Sales & Inventory Tracking Pipeline

A fully automated sync pipeline connecting **Vinted (`vinted.com`)** directly to **Google Sheets**. Tracks clothing inventory lots, unit cost allocations, live listing prices, realized sold amounts, shipping supplies, and net profit margins.

---

## 🚀 Features

* **🧥 Clothing Buys & Lot Cost Allocation**:
  * Input clothing bundles/hauls with total cost and article count.
  * Calculates exact unit cost per garment: `Unit Cost = Total Cost / Total Articles`.
  * Automatically links unit cost to individual item listings and sales via `VLOOKUP`.
* **📦 Packaging & Shipping Supply Tracker**:
  * Tracks poly mailers, thermal labels, boxes, and tape.
  * Calculates an amortized packaging cost per parcel that is factored into each sale's net profit.
* **🕵️ Playwright Scraping Engine (vinted.com)**:
  * Scrapes live closet asking prices, views, favorites, and listing statuses.
  * Scrapes completed orders, final sold prices, seller fee deductions, and net payouts.
  * Uses stealth configurations and persistent session tokens (`.session/vinted_state.json`) to bypass Cloudflare/bot checks without repetitive logins.
* **❤️ Favorites & Buyer Intent Analytics**:
  * Automatically tracks the favorite count (heart icon) and view count on every listing card.
  * Measures buyer conversion: Average favorites on sold garments vs. active unsold inventory.
  * Highlights high-interest active items (≥5 favorites) that are prime candidates for price drop offers.
* **👔 Clothing Category & Brand Performance Leaderboard**:
  * Uncovers what clothing types sell best and for the most money (Outerwear, Sweaters & Hoodies, Pants & Jeans, Tops, Footwear, etc.).
  * Real-time metrics per category: Sell-Through Rate %, Average Selling Price, Total Revenue, Average Net Profit, and Average Likes.
  * Brand performance leaderboard (Carhartt, Nike, Levi's, North Face, Patagonia, Vintage, etc.).
* **⚡ Multi-Modal Trigger Pipeline**:
  * **On-Demand CLI**: Run `python -m src.cli sync` immediately after posting new items or modifying prices.
  * **Inbound Email Webhook**: FastAPI endpoint (`/webhook/vinted-email`) accepts email notifications (via SendGrid, Mailgun, Cloudflare, etc.) for instant sale updates.
  * **Background Scheduler**: Periodic APScheduler service for hands-off reconciliation.
* **📊 Pre-Engineered Google Sheets KPI Dashboard**:
  * Gross Revenue, Net Payouts, Cost of Goods Sold (COGS), Supply Expenses, Net Profit, and ROI %.

---

## 📋 Google Sheets Layout & Formulas

| Tab Name | Purpose | Key Formulas / Calculations |
| :--- | :--- | :--- |
| **`Inventory_Lots`** | Hauls & bulk buys | `Unit_Cost`: `=IF(E2>0, D2/E2, 0)`<br>`Items_Processed`: `=COUNTIF(Item_Listings!B:B, A2)` |
| **`Item_Listings`** | Active & historic closet items | `Unit_Cost`: `=IFERROR(VLOOKUP(B2, Inventory_Lots!A:F, 6, FALSE), 0)`<br>`Favorites`: Live scraped heart count<br>`Category`: Inferred or custom apparel category |
| **`Sales_Log`** | Realized sales & payouts | `Brand`: `=IFERROR(VLOOKUP(B2, Item_Listings!A:E, 4, FALSE), "")`<br>`Category`: `=IFERROR(VLOOKUP(B2, Item_Listings!A:E, 5, FALSE), "")`<br>`Favorites`: `=IFERROR(VLOOKUP(B2, Item_Listings!A:I, 9, FALSE), 0)`<br>`Item_Cost`: `=IFERROR(VLOOKUP(B2, Item_Listings!A:F, 6, FALSE), 0)`<br>`Packaging_Cost`: `=Shipping_Supplies!$H$2`<br>`Net_Profit`: `=J2 - K2 - L2`<br>`Margin_%`: `=IF(H2>0, M2/H2, 0)` |
| **`Shipping_Supplies`** | Tape, mailers, labels, boxes | `Unit_Cost`: `=IF(E2>0, F2/E2, 0)`<br>`Avg_Supply_Cost`: `=IF(SUM(E2:E)>0, SUM(F2:F)/SUM(E2:E), 0.75)` |
| **`Analytics_Dashboard`** | Financial KPIs, Favorites & Categories | **Financials**: Gross Revenue, Net Profit, COGS, Unsold Capital<br>**Favorites**: Avg Favs on Sold vs Active, High-Interest Items<br>**Categories**: Sell-Through %, Avg Sale Price, Total Revenue & Profit by Garment Type<br>**Brands**: Revenue & Profit by Brand |

---

## 🛠️ Setup Instructions

### 1. Installation

```bash
# Clone or enter directory
cd /Users/nicol/Desktop/vinted_tracking

# Activate the virtual environment
source venv/bin/activate

# Dependencies are already installed in venv, or run:
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment (`.env`)

Copy the template:
```bash
cp .env.example .env
```

Edit `.env` with your preferred settings:
```ini
GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json
GOOGLE_SHEET_ID=your_google_sheet_id_here
VINTED_DOMAIN=vinted.com
VINTED_USERNAME=your_seller_username
```

### 3. Google Sheets Service Account Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `Vinted-Tracker`).
3. Enable the **Google Sheets API** and **Google Drive API**.
4. Go to **Credentials** -> **Create Credentials** -> **Service Account**.
5. Once created, click on the Service Account -> **Keys** -> **Add Key** -> **Create new key (JSON)**.
6. Save the downloaded JSON file as `credentials.json` in this project folder.
7. Open your Google Sheet in your browser and click **Share**. Add the service account email (found inside `credentials.json`, ending in `@...iam.gserviceaccount.com`) as an **Editor**.

### 4. Initialize Google Sheets Tabs & Formulas

Run the automatic sheet provisioner:
```bash
python -m src.cli init-sheet
```
This automatically builds and formats all 5 tabs, header rows, color themes, formulas, and the KPI dashboard!

### 5. Authenticate with Vinted (1-Time Setup)

Launch the interactive login session:
```bash
python -m src.cli login
```
* A Chromium browser window will open to `vinted.com`.
* Log in with your account credentials and solve any 2FA or CAPTCHA check.
* Once your profile is visible, return to the terminal and press **[ENTER]**.
* Your session tokens are safely saved to `.session/vinted_state.json` and reused headlessly for all future automated runs.

You can verify your session anytime with:
```bash
python -m src.cli check-session
```

---

## 💻 CLI Commands & Usage

### Check System Status
```bash
python -m src.cli status
```

### On-Demand Sync (Run when new items are listed)
```bash
# Sync active listings and price changes
python -m src.cli sync --type listings

# Sync sold orders and net payouts
python -m src.cli sync --type sales

# Full sync (both listings and sales)
python -m src.cli sync --type all
```

### Start Webhook Server (FastAPI)
```bash
python -m src.cli serve --port 8000
```
* **Health Check**: `GET http://localhost:8000/health`
* **Manual API Trigger**: `POST http://localhost:8000/sync` with `{"sync_type": "all"}`
* **Inbound Email Webhook**: `POST http://localhost:8000/webhook/vinted-email`

### Start Background Scheduler (Periodic Cron)
```bash
python -m src.cli scheduler
```
Runs a periodic reconciliation every `SYNC_INTERVAL_HOURS` (default: 6 hours).

---

## 🚨 Automated Authentication Alerts & Recovery

When your Vinted authentication session expires or requires verification:
1. **Google Sheets Banner Alert**: Row 2 of `Analytics_Dashboard` turns into a coral red alert banner (`🔴 AUTHENTICATION REQUIRED`) with error context and re-login instructions.
2. **GitHub Issue & Push Notification**: An issue titled `⚠️ Vinted Authentication Expired — Re-login Required` is automatically created on your repository (triggering email and mobile push notifications to you). If multiple runs fail, it adds update timestamps without duplicate issues.
3. **One-Step Resolution**:
   ```bash
   # 1. Log in via browser
   python -m src.cli login
   # 2. Upload updated token to GitHub Secrets
   python -m src.cli upload-session
   ```
   Once uploaded, the Google Sheet status banner automatically turns back to soft green (`🟢 SYSTEM STATUS: Active & Authenticated`) and the GitHub Issue is automatically commented and closed!

---

## 🧪 Testing

Run the test suite:
```bash
pytest -v
```
All unit and integration tests validate cost allocation formulas, price parsing, Vinted ID extraction, webhook alert triggers, and authentication notification dispatchers.
