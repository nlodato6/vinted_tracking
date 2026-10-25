# 📋 Vinted Sales Tracking Pipeline — Status & Next Steps

> **Current System State**:
> * ✅ **Pipeline Architecture**: Clean, bot-free HTML file imports & email parsing (Zero Playwright bot blocks, zero login hassles).
> * ✅ **Google Spreadsheet**: Fully synchronized live with **159 active listings** and **147 sold items** ([Open Sheet](https://docs.google.com/spreadsheets/d/1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg/edit)).
> * ✅ **Inventory Lots**: `LOT-001` automatically tracks **306** processed items and unit costs.
> * ✅ **Dashboard Status Banner**: Displays live timestamp of last HTML import and last email parsed.
> * 📄 **Frontend Implementation Plan**: Ready in [`STREAMLIT_FRONTEND_PLAN.md`](./STREAMLIT_FRONTEND_PLAN.md).

---

## 🚀 How to Run HTML Imports Today (CLI)

Whenever you save new HTML pages from your browser (`Cmd + S` / `Ctrl + S`), you can sync them with:

```bash
# 1. Import active closet listings:
PYTHONPATH=. venv/bin/python -m src.cli import-closet "tlodato24 - member profile _ Vinted_active.html" --lot "LOT-001"

# 2. Import sales & completed orders:
PYTHONPATH=. venv/bin/python -m src.cli import-orders "My orders _ Vinted_all.html"

# 3. Recalculate Inventory Lots:
PYTHONPATH=. venv/bin/python -m src.cli sync-lots

# 4. Check system status & timestamps:
PYTHONPATH=. venv/bin/python -m src.cli status
```

---

## 🎨 Upcoming Next Step: Streamlit Web Frontend

We have formulated a complete architecture plan to replace manual CLI commands with a modern drag-and-drop web application:

* **File**: Read the complete plan in [`STREAMLIT_FRONTEND_PLAN.md`](./STREAMLIT_FRONTEND_PLAN.md).
* **Key Features**:
  1. **Drag & Drop Upload**: Upload all 3 HTML files at once.
  2. **Smart In-Memory Deduplication**: No-op duplicates are filtered out in RAM before touching Google Sheets, using $\le 5$ API calls per sync.
  3. **Zero Disk Footprint**: Uploaded files stay in memory and are discarded immediately after syncing.
  4. **Free Cloud Hosting**: Deployable to [Streamlit Community Cloud](https://share.streamlit.app) for free using Streamlit Secrets.
