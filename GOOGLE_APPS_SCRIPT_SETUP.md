# 📬 Standalone Google Apps Script Setup (for tsavoie90@gmail.com)

This guide walks you through setting up the 24/7 serverless email parser inside **`tsavoie90@gmail.com`** so it automatically reads Vinted notification emails, moves sold items, logs offers, increments favorites, and updates your live Google Sheet every 5 minutes in Google's cloud.

---

## Step 1: Ensure Spreadsheet Access
1. Open your live [Google Spreadsheet](https://docs.google.com/spreadsheets/d/1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg/edit) (logged in as `nicashlod@gmail.com`).
2. Click the green **Share** button in the top right.
3. Add **`tsavoie90@gmail.com`** with **Editor** permissions.
4. Click **Send**.

---

## Step 2: Create the Apps Script Project in tsavoie90
1. Open a new **Incognito / Private browser window**.
2. Go to [https://script.google.com](https://script.google.com) and log in **only as `tsavoie90@gmail.com`**.
3. In the top left, click **+ New project**.
4. Rename "Untitled project" at the top to: **`Vinted Email Sync`**.

---

## Step 3: Paste the Script Code
Delete any default text in `Code.gs` and paste the following complete code:

```javascript
/**
 * Standalone Vinted Email Parser for tsavoie90@gmail.com
 * Runs directly in the account receiving Vinted emails and updates the shared Google Sheet.
 */
const SPREADSHEET_ID = "1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg";

function processVintedEmails() {
  const labelName = 'Vinted-Processed';
  let label = GmailApp.getUserLabelByName(labelName);
  if (!label) {
    label = GmailApp.createLabel(labelName);
  }

  // Searches emails from no-reply@vinted.com not yet labeled Vinted-Processed
  const query = `from:no-reply@vinted.com -label:${labelName}`;
  const threads = GmailApp.search(query, 0, 25);
  
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const wsListings = ss.getSheetByName('Item_Listings');
  const wsSold = ss.getSheetByName('Sold_Items');
  const wsDash = ss.getSheetByName('Analytics_Dashboard');

  if (threads.length === 0) {
    Logger.log("No new Vinted emails found to process.");
    return;
  }

  if (!wsListings || !wsSold) {
    Logger.log("Error: Worksheets Item_Listings or Sold_Items not found.");
    return;
  }

  // 1. Build cache of existing sold items to prevent duplicate sales
  const soldData = wsSold.getDataRange().getValues();
  const sHeaders = soldData.length > 0 ? soldData[0] : [];
  const sOrderIdIdx = sHeaders.indexOf('Order_ID');
  const sUrlIdx = sHeaders.indexOf('Vinted_URL');

  const existingOrderIds = new Set();
  const existingSoldVintedIds = new Set();
  for (let s = 1; s < soldData.length; s++) {
    const oId = String(soldData[s][sOrderIdIdx] || '').trim();
    if (oId) existingOrderIds.add(oId);
    const urlVal = String(soldData[s][sUrlIdx] || '').trim();
    const vMatch = urlVal.match(/items\/(\d+)/);
    if (vMatch) existingSoldVintedIds.add(vMatch[1]);
  }

  // 2. Read active listings
  let listingsData = wsListings.getDataRange().getValues();
  if (listingsData.length <= 1) return;

  const lHeaders = listingsData[0];
  const lSkuIdx = lHeaders.indexOf('Item_SKU');
  const lTitleIdx = lHeaders.indexOf('Title');
  const lFavIdx = lHeaders.indexOf('Favorites');
  const lIdIdx = lHeaders.indexOf('Vinted_ID');
  const lPriceIdx = lHeaders.indexOf('Current_Price');
  const lPostedIdx = lHeaders.indexOf('Posted_Price');
  const lHistIdx = lHeaders.indexOf('Price_History');
  const lSyncedIdx = lHeaders.indexOf('Last_Synced');

  let processedCount = 0;

  threads.forEach(thread => {
    const messages = thread.getMessages();
    messages.forEach(msg => {
      const subject = msg.getSubject();
      const body = msg.getPlainBody() || msg.getBody();
      const date = msg.getDate();
      const dateStr = Utilities.formatDate(date, Session.getScriptTimeZone(), 'yyyy-MM-dd');
      const mdStr = Utilities.formatDate(date, Session.getScriptTimeZone(), 'MM/dd');

      // ---------------------------------------------------------
      // 1. FAVORITE NOTIFICATION
      // ---------------------------------------------------------
      if (/was just favorited/i.test(subject) || /favorited your item/i.test(body)) {
        let title = '';
        const matchTitle = subject.match(/^(.*?)\s+was just favorited/i);
        if (matchTitle) title = matchTitle[1].replace(/["']/g, '').trim().toLowerCase();

        const matchId = body.match(/items\/(\d+)/);
        const vintedId = matchId ? matchId[1] : '';

        for (let r = 1; r < listingsData.length; r++) {
          const rowId = String(listingsData[r][lIdIdx]).replace(/,/g, '').trim();
          const rowTitle = String(listingsData[r][lTitleIdx]).trim().toLowerCase();

          if ((vintedId && rowId === vintedId) || (title && rowTitle.includes(title))) {
            const currentFav = Number(listingsData[r][lFavIdx]) || 0;
            const newFav = currentFav + 1;
            wsListings.getRange(r + 1, lFavIdx + 1).setValue(newFav);
            listingsData[r][lFavIdx] = newFav;
            Logger.log(`Incremented favorites for ${rowTitle} -> ${newFav}`);
            processedCount++;
            break;
          }
        }
      }

      // ---------------------------------------------------------
      // 2. OFFER RECEIVED NOTIFICATION
      // ---------------------------------------------------------
      else if (/new offer for/i.test(subject) || /instead of/i.test(body)) {
        let title = '';
        const matchTitle = subject.match(/new offer for\s+(.+)$/i);
        if (matchTitle) title = matchTitle[1].replace(/["']/g, '').trim().toLowerCase();

        let offerPrice = 0.0;
        let listedPrice = 0.0;
        const priceMatch = body.match(/(\$?\s*\d+(?:\.\d{2})?)\s+instead of\s+(\$?\s*\d+(?:\.\d{2})?)/i);
        if (priceMatch) {
          offerPrice = parseFloat(priceMatch[1].replace(/[^\d.]/g, '')) || 0.0;
          listedPrice = parseFloat(priceMatch[2].replace(/[^\d.]/g, '')) || 0.0;
        }

        const matchId = body.match(/items\/(\d+)/);
        const vintedId = matchId ? matchId[1] : '';

        for (let r = 1; r < listingsData.length; r++) {
          const rowId = String(listingsData[r][lIdIdx]).replace(/,/g, '').trim();
          const rowTitle = String(listingsData[r][lTitleIdx]).trim().toLowerCase();

          if ((vintedId && rowId === vintedId) || (title && (rowTitle.includes(title) || title.includes(rowTitle)))) {
            if (lHistIdx !== -1) {
              const currentHist = String(listingsData[r][lHistIdx] || '').trim();
              const offerPriceStr = `$${offerPrice.toFixed(2)}`;

              if (currentHist.includes(offerPriceStr) && currentHist.includes(mdStr)) {
                break;
              }

              let offerEntry = `${mdStr} Offer: ${offerPriceStr}`;
              if (listedPrice > 0) offerEntry += ` (was $${listedPrice.toFixed(2)})`;
              const updatedHist = currentHist ? `${currentHist} | ${offerEntry}` : offerEntry;
              wsListings.getRange(r + 1, lHistIdx + 1).setValue(updatedHist);
              listingsData[r][lHistIdx] = updatedHist;
              Logger.log(`Logged offer for ${rowTitle}: ${offerEntry}`);
              processedCount++;
              break;
            }
          }
        }
      }

      // ---------------------------------------------------------
      // 3. SALE NOTIFICATION
      // ---------------------------------------------------------
      else if (/you sold an item|you sold and item|you've made a sale|item sold/i.test(subject) || /congratulations on your sale/i.test(body)) {
        let soldPrice = 0.0;
        const priceMatches = body.match(/\$\s*(\d+(?:\.\d{2})?)/g);
        if (priceMatches && priceMatches.length > 0) {
          soldPrice = parseFloat(priceMatches[0].replace(/[^\d.]/g, '')) || 0.0;
        }

        const matchId = body.match(/items\/(\d+)/);
        const vintedId = matchId ? matchId[1] : '';

        let orderId = '';
        const orderMatch = body.match(/(?:transaction|inbox)\/([a-zA-Z0-9-]+)/);
        if (orderMatch) {
          orderId = orderMatch[1];
        } else {
          orderId = 'ORD-' + Math.floor(date.getTime() / 1000);
        }

        if (existingOrderIds.has(orderId) || (vintedId && existingSoldVintedIds.has(vintedId))) {
          Logger.log(`Order ${orderId} already recorded in Sold_Items. Skipping.`);
          return;
        }

        let targetRowIdx = -1;
        for (let r = 1; r < listingsData.length; r++) {
          const rowId = String(listingsData[r][lIdIdx]).replace(/,/g, '').trim();
          if (vintedId && rowId === vintedId) {
            targetRowIdx = r + 1;
            break;
          }
        }

        const nextSoldRow = wsSold.getLastRow() + 1;

        if (targetRowIdx > 0) {
          const rowData = wsListings.getRange(targetRowIdx, 1, 1, wsListings.getLastColumn()).getValues()[0];
          const finalPrice = soldPrice > 0 ? soldPrice : (Number(rowData[lPriceIdx]) || Number(rowData[lPostedIdx]) || 0);

          const soldRow = [
            rowData[lSkuIdx],                                           // A: Item_SKU
            rowData[1],                                                 // B: Lot_ID
            rowData[lTitleIdx],                                         // C: Title
            rowData[3],                                                 // D: Brand
            rowData[4],                                                 // E: Category
            `=IFERROR(VLOOKUP(B${nextSoldRow}, Inventory_Lots!A:F, 6, FALSE), 0)`, // F: Unit_Cost
            rowData[lPostedIdx],                                        // G: Posted_Price
            rowData[lPriceIdx],                                         // H: Current_Price
            `=IF(OR(G${nextSoldRow}="", H${nextSoldRow}=""), "", IF(AND(ISNUMBER(G${nextSoldRow}), ISNUMBER(H${nextSoldRow}), H${nextSoldRow}<G${nextSoldRow}), "YES", "NO"))`,
            `=IF(OR(G${nextSoldRow}="", H${nextSoldRow}=""), "", IF(AND(ISNUMBER(G${nextSoldRow}), ISNUMBER(H${nextSoldRow}), H${nextSoldRow}<G${nextSoldRow}), G${nextSoldRow}-H${nextSoldRow}, 0))`,
            rowData[10],                                                // K: Price_History
            finalPrice,                                                 // L: Sold_Price
            `=IF(AND(ISNUMBER(G${nextSoldRow}), ISNUMBER(L${nextSoldRow}), G${nextSoldRow}>0), G${nextSoldRow}-L${nextSoldRow}, 0)`,
            `=IF(OR(G${nextSoldRow}="", L${nextSoldRow}=""), "", IF(L${nextSoldRow}>=G${nextSoldRow}, "Original Price", IF(L${nextSoldRow}=H${nextSoldRow}, "Lowered Price", "Accepted Offer")))`,
            dateStr,                                                    // O: Sale_Date
            'Vinted',                                                   // P: Platform
            `=IF(P${nextSoldRow}="FB Marketplace (Local)", 0, Shipping_Supplies!$H$2)`,
            `=IF(AND(ISNUMBER(L${nextSoldRow}), ISNUMBER(F${nextSoldRow}), ISNUMBER(Q${nextSoldRow})), L${nextSoldRow}-F${nextSoldRow}-Q${nextSoldRow}, IF(ISNUMBER(L${nextSoldRow}), L${nextSoldRow}, 0))`,
            `=IF(AND(ISNUMBER(L${nextSoldRow}), L${nextSoldRow}>0), R${nextSoldRow}/L${nextSoldRow}, 0)`,
            rowData[lFavIdx],                                           // T: Favorites
            rowData[12],                                                // U: Views
            orderId,                                                    // V: Order_ID
            rowData[14],                                                // W: Vinted_URL
            'Sold',                                                     // X: Status
            rowData[16],                                                // Y: Date_Listed
          ];

          wsSold.appendRow(soldRow);
          wsListings.deleteRow(targetRowIdx);

          existingOrderIds.add(orderId);
          if (vintedId) existingSoldVintedIds.add(vintedId);
          Logger.log(`Moved ${rowData[lTitleIdx]} to Sold_Items.`);
          processedCount++;
        }
      }
    });

    // Tag thread with label so it is never re-evaluated
    thread.addLabel(label);
  });

  // Update Analytics_Dashboard Row 2 Status Banner with last email parsed timestamp
  if (wsDash) {
    const currentBanner = String(wsDash.getRange('A2').getValue() || '');
    const nowTime = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
    const hMatch = currentBanner.match(/Last HTML Import:\s*([^|]+)/);
    const htmlTime = hMatch ? hMatch[1].trim() : 'Not yet run';
    const newBanner = `🟢 SYSTEM STATUS: Active | Last HTML Import: ${htmlTime} | Last Email Parsed: ${nowTime}`;
    wsDash.getRange('A2').setValue(newBanner);
  }
}
```

---

## Step 4: Run Initial Test & Authorize
1. Click the **Save** icon (`Cmd + S` or `Ctrl + S`).
2. Make sure the dropdown at the top says `processVintedEmails`, then click **Run**.
3. Google will show an **"Authorization required"** modal:
   - Click **Review permissions**.
   - Select your **`tsavoie90@gmail.com`** account.
   - Click **Advanced** (bottom left of modal) $\rightarrow$ Click **Go to Vinted Email Sync (unsafe)**.
   - Click **Allow**.
4. The script will execute. Check the **Execution log** at the bottom — it will search `from:no-reply@vinted.com` and log actions!

---

## Step 5: Add the 5-Minute Recurring Trigger
1. In the left navigation menu of Apps Script, click the **Triggers (clock icon)**.
2. In the bottom right, click **+ Add Trigger**.
3. Configure the trigger:
   - **Choose which function to run**: `processVintedEmails`
   - **Choose which deployment should run**: `Head`
   - **Select event source**: `Time-driven`
   - **Select type of time based trigger**: `Minutes timer`
   - **Select minute interval**: `Every 5 minutes`
4. Click **Save**.

🎉 **That's it!** The script will now run in Google's cloud every 5 minutes 24/7 inside `tsavoie90@gmail.com`.
