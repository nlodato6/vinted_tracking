"""Google Sheets tab schemas, column headers, and default formulas."""

TAB_INVENTORY_LOTS = "Inventory_Lots"
TAB_ITEM_LISTINGS = "Item_Listings"
TAB_SOLD_ITEMS = "Sold_Items"
TAB_SALES_LOG = "Sales_Log"
TAB_SHIPPING_SUPPLIES = "Shipping_Supplies"
TAB_ANALYTICS_DASHBOARD = "Analytics_Dashboard"

SHEET_TABS = [
    TAB_INVENTORY_LOTS,
    TAB_ITEM_LISTINGS,
    TAB_SOLD_ITEMS,
    TAB_SALES_LOG,
    TAB_SHIPPING_SUPPLIES,
    TAB_ANALYTICS_DASHBOARD,
]

LOTS_HEADERS = [
    "Lot_ID",
    "Purchase_Date",
    "Source_Vendor",
    "Total_Lot_Cost",
    "Total_Articles",
    "Unit_Cost",
    "Items_Processed",
    "Status",
    "Notes",
]

LISTINGS_HEADERS = [
    "Item_SKU",
    "Lot_ID",
    "Title",
    "Brand",
    "Category",
    "Unit_Cost",
    "Target_Price",
    "Current_Price",
    "Favorites",
    "Views",
    "Vinted_ID",
    "Vinted_URL",
    "Status",
    "Date_Listed",
    "Last_Synced",
]

SOLD_ITEMS_HEADERS = list(LISTINGS_HEADERS)

SALES_HEADERS = [
    "Order_ID",
    "Platform",
    "Item_SKU",
    "Item_Title",
    "Brand",
    "Category",
    "Favorites",
    "Sale_Date",
    "Sold_Price",
    "Seller_Fees",
    "Net_Payout",
    "Item_Cost",
    "Packaging_Cost",
    "Net_Profit",
    "Margin_%",
    "Order_Status",
]

SUPPLIES_HEADERS = [
    "Supply_ID",
    "Purchase_Date",
    "Item_Description",
    "Vendor",
    "Quantity",
    "Total_Cost",
    "Unit_Cost",
    "Avg_Cost_Per_Shipment",
]
