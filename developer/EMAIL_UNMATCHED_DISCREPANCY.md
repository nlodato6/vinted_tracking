# Email Sync: Unmatched Orders Discrepancy & Resolution Plan

## 1. Issue Description
During **Direct Email Sync**, certain orders are flagged in the preview as:
> **Unmatched Orders (N items):** *"No matching row in Sold_Items or Item_Listings (import your Sold closet HTML first)"*

However, upon inspecting the Google Sheet, rows corresponding to some of these items **already exist in the `Sold_Items` tab**.

---

## 2. Root Cause Analysis

In [`src/sheets/reconcile.py`](file:///Users/nicol/Desktop/vinted_tracking/src/sheets/reconcile.py), the matcher relies on the function `take_sold(title, txn)`:

```python
def take_sold(title: str, txn: str) -> Optional[int]:
    rows = sold_by_title.get(_norm(title), [])
    for i in rows:
        if i not in used_rows and claimed.get(i) == txn:
            used_rows.add(i)
            return i
    for i in rows:
        if i not in used_rows and i not in claimed and not str(sold[i][O]).strip():
            claimed[i] = txn
            used_rows.add(i)
            return i
    return None
```

Three specific constraints cause existing rows to fail matching:

### A. Non-Blank `Order_ID` in Sheet (`not str(sold[i][O]).strip()`)
- When matching a shipping-label email, the matcher requires that either:
  1. The row's `Order_ID` in `Sold_Items` already equals the transaction ID (`claimed.get(i) == txn`), OR
  2. The row's `Order_ID` is **completely blank** (`not str(sold[i][O]).strip()`).
- **Failure Case:** If a row in `Sold_Items` already contains an `Order_ID` (for instance, a Vinted numeric conversation ID from an earlier `My orders _ Vinted.html` import, or a placeholder/manual entry), the matcher skips the row to avoid overwriting what it assumes is a different order.

### B. Title Truncation in Vinted Shipping Emails
- Vinted shipping label notifications and subject lines frequently truncate item titles longer than ~35–40 characters with trailing ellipses (`...` or `…`).
- **Failure Case:**
  - Email title: `Vintage 90s Carhartt Canvas Work Jacket...`
  - Sheet title: `Vintage 90s Carhartt Canvas Work Jacket L Brown Canvas Coat`
  - Exact normalized match `_norm(email_title) == _norm(sheet_title)` returns `False`.

### C. HTML Entities & Punctuation Inconsistencies
- `_norm()` in `reconcile.py` only performs:
  ```python
  re.sub(r"\s+", " ", (title or "").replace("’", "'")).strip().lower()
  ```
- **Failure Case:** Differences between HTML-encoded characters in email bodies vs. raw text in Google Sheets:
  - `&amp;` vs. `&`
  - `&quot;` vs. `"`
  - En-dashes / em-dashes (`–`, `—`) vs. hyphens (`-`)
  - Non-breaking spaces (`\u00a0`)

---

## 3. Concrete Solution Architecture (To Implement)

When ready to implement, the following four enhancements will resolve these discrepancies:

### Step 1: Enhanced Title Normalization
Update `_norm(title)` in `src/sheets/reconcile.py` to:
1. Decode HTML entities: `html.unescape(title)`.
2. Strip trailing ellipses: `re.sub(r"(\.{3}|…)$", "", title).strip()`.
3. Normalize unicode quotes and dashes to ASCII standard hyphens and quotes.

### Step 2: Two-Tier Matcher (Exact $\rightarrow$ Prefix / Fuzzy)
If exact normalized title lookup yields no row:
1. **Prefix Match:** If the email title was truncated (ends in `...`), match any sheet row whose normalized title begins with the email title's prefix.
2. **Token Similarity Match:** For long titles, match rows sharing $> 85\%$ token overlap when no other unassigned candidate exists.

### Step 3: Permissive Order ID Re-assignment
In `take_sold`:
- If an existing row has an `Order_ID` that is from an HTML export (e.g. numeric conversation ID or format differing from transaction IDs) and has a **blank `Sale_Date`**, allow the shipping label email to populate `Sale_Date` and update the `Order_ID` to the shipping transaction ID.

### Step 4: UI Interactive Matching (Fallback)
In `app_pages/sync.py`, under the **Unmatched Orders** expander:
- Add a dropdown next to each unmatched order allowing the seller to pick an existing undated `Sold_Items` row to link manually with 1 click.
