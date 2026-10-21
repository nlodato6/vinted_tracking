# 👕 SpreadThrift — Complete User & Feature Guide
> **"From inbox alerts to bottom-line gains."**  
> An automated bookkeeping, inventory tracking, and net profit analytics suite for Vinted resellers and thrift flippers.

---

## 📑 Table of Contents
1. [Overview & Core Architecture](#-overview--core-architecture)
2. [Getting Started & Authentication](#-getting-started--authentication)
3. [Connecting Your Google Sheet](#-connecting-your-google-sheet)
4. [Feature Walkthrough: Sync & Batch Import](#-feature-walkthrough-sync--batch-import)
   - [Tab 1: HTML Closet & Orders Import](#1-html-closet--orders-import)
   - [Tab 2: Inventory Lots & COGS Allocation](#2-inventory-lots--cogs-allocation)
   - [Tab 3: Packaging & Shipping Supplies](#3-packaging--shipping-supplies)
   - [Tab 4: Real-Time Gmail Sync (IMAP Automation)](#4-real-time-gmail-sync-imap-automation)
5. [Feature Walkthrough: Metrics & Analytics Dashboard](#-feature-walkthrough-metrics--analytics-dashboard)
6. [Feature Walkthrough: User Feedback Module](#-feature-walkthrough-user-feedback-module)
7. [Administrator & Telemetry Hub](#-administrator--telemetry-hub)
8. [Data Privacy, Security & Disclaimers](#-data-privacy-security--disclaimers)
9. [Frequently Asked Questions & Troubleshooting](#-frequently-asked-questions--troubleshooting)

---

## 🎯 Overview & Core Architecture

SpreadThrift eliminates messy manual spreadsheets and expensive subscription databases for clothing resellers.

* **100% Private Cloud Storage**: SpreadThrift operates on a decentralized architecture. Your sales data, inventory records, and financial analytics live inside **your own personal Google Drive** via Google Sheets.
* **Zero Middleman Databases**: We do not store or monetize your sales data. If you ever leave SpreadThrift, your spreadsheet remains fully functional and accessible in your Google Drive.
* **Instant Automation**: Parses Vinted HTML pages and Gmail notifications to extract real transaction prices, platform fees, shipping costs, and unit costs automatically.

---

## 🔑 Getting Started & Authentication

When you open SpreadThrift ([`http://localhost:8501`](http://localhost:8501)), you are greeted with the login portal:

### 1. Sign in with Google (OAuth 2.0)
* **Who it's for**: All regular users and resellers.
* **How it works**: Click the blue **"🔑 Sign in with Google"** button. Authenticate with your Google Account to automatically grant SpreadThrift permission to read and update your personal Google Sheets.
* **Privacy**: Only accesses the spreadsheets you specifically choose to connect or create.

### 2. Local / Development Access
* **Who it's for**: Developers and local single-user instances.
* **How it works**: Click **"🚀 Continue with Local Credentials"** to authenticate using the local Google Cloud service account file (`credentials.json`).

### 3. Administrator Direct Unlock
* **Who it's for**: The application owner / administrator.
* **How it works**: Expand the **Administrator Verification** container, enter your admin master passcode (default: `spreadthrift-admin-2026`), and unlock the **Admin & Telemetry Hub**.

---

## 📊 Connecting Your Google Sheet

SpreadThrift reads and writes directly to a standardized Google Sheets template.

### 1. Copy the Master Template
1. In the left sidebar, click the **"📋 Copy Sheet Template"** button.
2. A new browser tab opens in Google Sheets. Click **Make a copy**.
3. This creates a fresh, fully configured spreadsheet in your personal Google Drive with all formulas, conditional formatting, and dashboard views pre-built.

### 2. Link Your Sheet ID
1. Copy the Spreadsheet ID from your browser URL bar:
   ```text
   https://docs.google.com/spreadsheets/d/1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg/edit
                                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
   ```
2. Paste either the full URL or just the ID into the sidebar field: **"Spreadsheet ID or URL"**.
3. SpreadThrift instantly verifies and connects to the sheet.
4. Click **"🔗 Open Live Sheet"** in the sidebar at any time to open your spreadsheet in a new tab.

### The 5 Core Worksheets Inside Your Sheet
| Worksheet Name | Purpose |
| :--- | :--- |
| **`Item_Listings`** | Active wardrobe items currently listed on Vinted (Title, Price, Lot ID, Image URL). |
| **`Sold_Items`** | Completed sales with sale price, buyer protection fees, shipping cost, packaging deduction, and net profit. |
| **`Inventory_Lots`** | Thrift hauls, pallet buys, and garage sale lots. Tracks total cost and computes per-item cost-of-goods-sold (COGS). |
| **`Shipping_Supplies`** | Packaging supplies (poly mailers, boxes, bubble wrap, labels). Automatically calculates per-package cost deductions. |
| **`Analytics_Dashboard`** | Live spreadsheet formulas for gross sales, total net profit, active inventory count, and profit margins. |

---

## 🔄 Feature Walkthrough: Sync & Batch Import

Navigate to **"Sync & Batch Import"** in the sidebar navigation. This page houses all data-intake pipelines across 4 dedicated tabs:

---

### 1. HTML Closet & Orders Import
The fastest way to ingest your existing Vinted history without typing anything manually.

1. **How to get the HTML file from Vinted**:
   * On your computer, open Google Chrome or Safari and go to **vinted.com**.
   * Go to **"My Orders"** (for sold items) or your **Public Profile / Wardrobe** (for active listings).
   * Press `Ctrl + S` (Windows) or `Cmd + S` (Mac) $\rightarrow$ save format as **"Webpage, Complete"** or **"HTML Only"**.
2. **Uploading to SpreadThrift**:
   * Drag and drop the `.html` file into the upload zone.
   * SpreadThrift automatically parses the document, extracting:
     * Item Title & Brand
     * Listed Price or Final Sale Price
     * Buyer Protection Fee & Shipping Cost
     * Status (`Sold`, `Active`, `Cancelled`)
     * High-res Item Image URL & Direct Vinted Link
3. **Review & Reconcile**:
   * Inspect the preview table.
   * Choose your target inventory lot (e.g. `LOT-001`).
   * Click **"Commit to Google Sheets"**. SpreadThrift guarantees **zero duplicates** by cross-referencing existing item IDs.

---

### 2. Inventory Lots & COGS Allocation
Accurate net profit requires knowing your true Cost of Goods Sold (COGS). The **Inventory Lots** module automates unit economics.

1. **Creating a Haul/Lot**:
   * Enter a Lot Identifier (e.g. `LOT-2026-OCT-A`, `Goodwill Thrift Haul`).
   * Enter the **Source** (e.g., Goodwill, Estate Sale, Thrift Store).
   * Enter the **Total Cost Paid** (e.g., `$85.00`).
   * Enter the **Total Number of Items** acquired (e.g., `17 items`).
2. **Automatic Cost Allocation**:
   * SpreadThrift calculates your uniform cost: `$85.00 ÷ 17 = $5.00 / item`.
3. **Profit Tracking**:
   * When items assigned to this lot are sold, SpreadThrift automatically deducts `$5.00` COGS, ensuring your realized profit calculations are 100% accurate.

---

### 3. Packaging & Shipping Supplies
Never let packaging costs eat into your margins unnoticed.

1. **Tracking Supplies**:
   * Add common materials: Poly Mailers (10x13), Bubble Wrap, Shipping Tape, Thermal Labels, Tissue Paper.
   * Input the bulk purchase price and quantity (e.g. 100 poly mailers for `$14.00` = `$0.14` per mailer).
2. **Automated Deductions**:
   * SpreadThrift sums your supply costs per order (e.g. `$0.14` mailer + `$0.05` label + `$0.06` tape = `$0.25` packaging cost).
   * Automatically deducts packaging expenses on every sale in the `Sold_Items` worksheet.

---

### 4. Real-Time Gmail Sync (IMAP Automation)
Hands-free sale tracking directly from your email notifications.

1. **How it works**:
   * Connects securely to your Gmail inbox via a Google App Password.
   * Searches for Vinted order confirmation emails (`"You sold an item!"`, `"Offer accepted"`).
   * Automatically parses the sold price, buyer username, shipping label status, and item title.
2. **Running a Sync**:
   * Click **"Check Inbox & Sync New Sales"**.
   * Any newly received sale alerts are extracted, formatted, and appended to your Google Sheet without manual intervention.

---

## 📈 Feature Walkthrough: Metrics & Analytics Dashboard

Navigate to **"Metrics & Analytics"** in the sidebar. This dashboard computes real-time business performance analytics using Plotly charts:

* **Top KPI Metric Cards**:
  * **Gross Sales**: Total revenue generated from all completed transactions.
  * **Realized Net Profit**: True cash profit after subtracting item COGS, packaging supplies, and seller fees.
  * **Average Profit Margin %**: Overall margin efficiency across your closet.
  * **Active Inventory Value**: Total retail value of items currently listed in your closet.
  * **Items Sold vs. Active**: Turnover ratio of sold merchandise.
* **Visual Analytics Charts**:
  * **Sales & Profit Timeline**: Area chart tracking daily/monthly sales volume and net income trends.
  * **Margin Distribution**: Histogram identifying your highest-margin clothing categories.
  * **Top Performing Brands**: Ranked bar chart showing which brands sell fastest and net the highest profit.
  * **Price vs. Profit Scatter Plot**: Helps spot underpriced gems or slow-moving high-ticket items.
* **Interactive Filters**:
  * Filter analytics by Date Range (Last 7 Days, Last 30 Days, Year-to-Date, or All Time).
  * Filter by specific Inventory Lot or Brand.

---

## 💬 Feature Walkthrough: User Feedback Module

Located directly in the left sidebar under **"💬 Feedback & Requests"**:

1. **Opening the Popover**:
   * Click the **"💬 Feedback & Requests"** button in the sidebar.
2. **Submitting Feedback**:
   * **Category**: Choose `Feature Request`, `Bug Report`, `General Feedback`, or `Other`.
   * **Star Rating**: Rate your experience from 1 to 5 stars (`st.feedback("stars")`).
   * **Message**: Describe what you'd like added or report any glitch you encountered.
   * Click **"Send Feedback"**.
3. **What Happens Behind the Scenes**:
   * You will receive an immediate confirmation toast.
   * The submission is recorded in the local telemetry cache and **automatically appended to the `User_Feedback` tab** in the Google Sheet for the admin team to review.

---

## 🛡️ Administrator & Telemetry Hub

Accessible to authorized administrators by clicking **"Admin Dashboard"** in the sidebar (or entering the master admin passcode):

* **Top KPIs**: Total registered users, active sessions today, total app events logged, user feedback count, and unresolved errors.
* **Tab 1: 💬 User Feedback**:
  * View all user suggestions, ratings, and bug reports.
  * Filter by category or lifecycle status (`🆕 New`, `👀 Reviewed`, `🛠️ In Progress`, `✅ Resolved`).
  * Quick status transition buttons (`Mark Reviewed`, `Mark In Progress`, `Mark Resolved`).
  * **Internal Admin Notes**: Private expandable notes on each card to link GitHub issues or release milestones.
  * **"🔄 Sync to Google Sheet"**: 1-click push to sync any offline feedback directly to Google Sheets.
* **Tab 2: 🚨 Live Errors & Alerts**:
  * Real-time monitoring of any uncaught Python exceptions or API timeouts across user sessions.
  * Inspect full stack traces, page origins, and affected user emails.
  * Mark errors as resolved individually or in bulk.
* **Tab 3: 📈 User Adoption & Metrics**:
  * Daily user event area charts.
  * Feature usage breakdown (e.g. percentage of users using HTML import vs. Lot management).
  * User activity leaderboard.
* **Tab 4: 📋 Real-Time Activity Feed**:
  * Audit log displaying user actions in chronological order.
* **Tab 5: ⚙️ Admin Operations & Google Sheets**:
  * Test Google Sheets cloud storage connection and worksheet status.
  * Reset telemetry database for testing or staging maintenance.

---

## ⚖️ Data Privacy, Security & Disclaimers

### Decentralized Data Philosophy
* **SpreadThrift has no middleman database storing your sales.** Your data is stored directly in your own Google Drive spreadsheet.
* **No AI Training on Your Data**: SpreadThrift complies strictly with the Google API Services User Data Policy. Your spreadsheets and emails are never used to train machine learning models.
* **Third-Party Trademark Disclaimer**: SpreadThrift is an independent software tool and is **not affiliated with, authorized, maintained, sponsored, or endorsed by Vinted, Google LLC, or Meta Platforms, Inc.** All trademarks belong to their respective owners.

### Built-in Legal Modals
* Click **"🛡️ Privacy Policy"** or **"📜 Terms of Service"** on the login screen or in the sidebar expander (`⚖️ Legal & Compliance`) to read the full agreements directly inside the app.

---

## ❓ Frequently Asked Questions & Troubleshooting

### Q: Google shows "Error 400: redirect_uri_mismatch" when signing in.
* **Fix**: In Google Cloud Console $\rightarrow$ **APIs & Services $\rightarrow$ Credentials**, click your OAuth Client ID $\rightarrow$ scroll to **Authorized redirect URIs** $\rightarrow$ add `http://localhost:8501` (ensure there is no trailing slash) $\rightarrow$ click **Save**.

### Q: Google shows "SpreadThrift has not completed the Google verification process" (Error 403).
* **Fix**: In Google Cloud Console $\rightarrow$ **OAuth consent screen** $\rightarrow$ scroll to **Test users** $\rightarrow$ add the Google email address you are trying to sign in with $\rightarrow$ click **Save**.

### Q: Why does SpreadThrift say "Spreadsheet not found or permission denied"?
* **Fix**: If using a Service Account (`credentials.json`), ensure you shared your Google Sheet with the service account email (found in `credentials.json` under `client_email`) with **Editor** permissions. If using "Sign in with Google", ensure your account has edit permissions on the spreadsheet.

### Q: Will updating my spreadsheet manually in Google Sheets break SpreadThrift?
* **Answer**: No! You can add custom columns, format colors, or edit notes directly in Google Sheets. As long as you do not delete or rename the standard column headers, SpreadThrift will seamlessly sync.

---

*Need support or want to request a feature? Use the in-app **💬 Feedback & Requests** button in the sidebar or email [support@spreadthrift.com](mailto:support@spreadthrift.com).*
