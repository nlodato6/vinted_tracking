# Streamlit Frontend Implementation Plan: Vinted HTML Import & Reconciliation

## Overview
This document outlines the architectural design and execution roadmap to build a lightweight, modern web frontend using **Streamlit** for the Vinted Sales & Inventory Tracking system.

The frontend allows the user to drag and drop their 3 saved Vinted HTML files (**Active Listings**, **Sold Listings**, and **My Orders**), view an immediate reconciliation preview, and execute a zero-duplication sync to the live Google Sheet with a single click.

---

## 1. Key Architectural Decisions

### Q1: What is the most efficient way to deal with duplications since the uploaded file contains the whole HTML every time?
**Two-Phase Set-Based In-Memory Reconciliation:**
Rather than performing slow, rate-limited, row-by-row queries against Google Sheets, the synchronization engine uses a **bulk read-and-diff strategy**:

1. **Phase 1: Fetch Current Sheet State in 2 Calls**
   - Download active items (`Item_Listings!A:R`) and sold items (`Sold_Items!A:Y`) into memory.
   - Build fast in-memory hash indexes:
     - `active_ids = {vinted_id: item}`
     - `sold_ids = {vinted_id: item}`
     - `sold_order_ids = {order_uuid: item}`
     - `active_titles = {normalized_title: item}`

2. **Phase 2: In-Memory Diff & Bucket Categorization**
   - Parse the uploaded HTML files directly into memory.
   - Categorize parsed items into 3 clear buckets:
     - **No-Op / Exact Duplicates**: Item already in `Item_Listings` with identical price, or order already recorded in `Sold_Items`. **Skipped completely with 0 Google API writes.**
     - **Inventory Transitions (Active → Sold)**: Item currently exists in `Item_Listings`, but appears in `My Orders` or `Sold Listings`. Queued to be moved to `Sold_Items` and removed from `Item_Listings`.
     - **Net New Listings**: Items whose `vinted_id` does not exist in `Item_Listings` or `Sold_Items`. Queued for single-batch append.

3. **Phase 3: Atomic Batch Execution**
   - Execute updates in bulk (1 batch append for new listings, 1 batch move for sales, 1 dashboard status update).
   - **Result**: Even if the uploaded HTML has hundreds of items, the entire sync finishes in seconds using fewer than 5 API calls, completely eliminating 429 quota errors.

---

### Q2: Should the HTML files be deleted after?
**Ephemeral In-Memory Handling (Zero Disk Footprint):**
- Streamlit’s `st.file_uploader` yields `io.BytesIO` objects held in RAM. 
- **The files never need to be written to the hard drive at all.**
- The BeautifulSoup parser reads directly from the byte stream: `BeautifulSoup(uploaded_file.getvalue(), "html.parser")`.
- Once the sync finishes or the browser tab closes, the operating system's garbage collector frees the memory.
- **Benefits**: Maximum security, zero file clutter, zero disk cleanup maintenance.

---

### Q3: Can this be a Streamlit app deployed on Streamlit Cloud for free?
**Yes — 100% Free on Streamlit Community Cloud:**
- **Hosting**: Streamlit Community Cloud connects directly to the GitHub repo (`https://github.com/nlodato6/vinted_tracking`).
- **Secrets Management**: Google Service Account credentials (`credentials.json`) and the Spreadsheet ID are stored encrypted in Streamlit Cloud's built-in **Secrets Manager** (`st.secrets`).
- **Live URL**: Gives you a permanent public or private HTTPS URL (e.g., `https://vinted-tracker.streamlit.app`) accessible from your phone, tablet, or laptop.

---

## 2. System Architecture & Data Flow

```
[ User Browser / Phone ]
         │
         ▼ (Drag & Drop 3 HTML Files)
[ Streamlit Web App (app.py) ]
         │
         ├──> In-Memory Buffer (BytesIO - No Disk Save)
         ├──> HTML Parsing (src/html_parser.py)
         │
         ▼
[ In-Memory Reconciliation Engine ] <── (2 Bulk Read Calls) ── [ Google Sheets ]
         │
         ├── Compare Vinted IDs & Order UUIDs
         ├── Categorize:
         │     ├── No-Op Duplicates (0 Writes)
         │     ├── Active → Sold Transitions
         │     └── Net New Listings
         │
         ▼
[ Interactive Preview & Diff Display ]
         │
         ▼ (User clicks "Run Sync")
[ Atomic Batch Write (<5 API Calls) ] ───────────────────────> [ Google Sheets ]
                                                               (Item_Listings, Sold_Items,
                                                                Inventory_Lots, Dashboard)
```

---

## 3. User Interface & Feature Design

### Visual Structure
1. **Header & Status Banner**:
   - Displays live Google Sheets connection status (`🟢 Connected`).
   - Shows last HTML import timestamp and email parser timestamp.
   - Clickable direct link to open the Google Spreadsheet.
2. **Smart Upload Zone**:
   - Supports uploading 1, 2, or all 3 HTML files at once.
   - Automatically detects whether each uploaded file is:
     - `Active Closet` (contains `data-testid="product-item-id-"` without sold badges)
     - `Sold Closet` (contains sold badges)
     - `My Orders` (contains `data-testid="my-orders-item"` or `/inbox/` links)
3. **Reconciliation Summary**:
   - 🟢 **New Listings**: Number of brand new items to be added.
   - 🔵 **Sold Transitions**: Number of active items transitioning to sold.
   - ⚪ **Duplicates Skipped**: Number of items already tracked and unchanged.
   - 🟡 **Metrics Updated**: Number of existing listings with updated stats.
4. **Detailed Expandable Diffs**:
   - Collapsible dataframes displaying item titles, prices, brands, and categories before committing.
5. **Run Sync Button**:
   - Triggers the atomic batch update with a progress bar and celebratory notification (`🎈 Sync Complete!`).

---

## 4. Implementation Steps & Task Breakdown

### Phase 1: Core Streamlit Application (`app.py`)
- Create `app.py` in the workspace root.
- Build `detect_html_type(file_bytes: bytes) -> str` to auto-categorize uploaded files.
- Integrate `parse_closet_html` and `parse_orders_html` to accept `io.BytesIO` / strings directly.
- Implement the in-memory reconciliation logic that compares parsed items against the Google Sheet state.

### Phase 2: Credentials & Secrets Adaptation
- Update `src/sheets/client.py` to support dual authentication:
  - If running locally: loads `credentials.json` or `.env`.
  - If running on Streamlit Cloud: loads credentials directly from `st.secrets["gcp_service_account"]`.

### Phase 3: UI Polishing & Styling
- Clean dark/light theme responsive styling.
- Metric tiles for **Active Listings**, **Sold Items**, **Unprocessed Articles**, and **Gross Margin**.
- Informative alerts and error handling.

### Phase 4: Local Testing & Validation
- Run `streamlit run app.py` locally and verify:
  - File upload of the 3 HTML files.
  - Zero duplicate rows created.
  - Dashboard row 2 timestamp updates accurately.

### Phase 5: Streamlit Community Cloud Deployment
- Add `streamlit` to `requirements.txt`.
- Push repository to GitHub.
- Open [share.streamlit.io](https://share.streamlit.io), connect repository `nlodato6/vinted_tracking`, branch `master`, file `app.py`.
- Paste Google Service Account JSON into **App Settings → Secrets**.
- Deploy and verify live operation on mobile and desktop.

---

## 5. Security & Maintenance Checklist

| Concern | Strategy |
| :--- | :--- |
| **API Quota Management** | Set-based diffing restricts API calls to $\le 5$ per sync run, preventing 429 quota exhaustion. |
| **Credential Safety** | Never committed to Git; stored in `.env` locally and in `st.secrets` in the cloud. |
| **File Privacy** | HTML files are processed exclusively in RAM and discarded immediately. |
| **Data Integrity** | Non-destructive matching: existing SKUs and historical sales records are never overwritten or deleted. |
