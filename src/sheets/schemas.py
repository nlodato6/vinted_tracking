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
    "Posted_Price",
    "Current_Price",
    "Price_Lowered",
    "Price_Drop",
    "Price_History",
    "Favorites",
    "Views",
    "Vinted_ID",
    "Vinted_URL",
    "Status",
    "Date_Listed",
    "Last_Synced",
    "Source",
]

SOLD_ITEMS_HEADERS = [
    "Item_SKU",
    "Lot_ID",
    "Title",
    "Brand",
    "Category",
    "Unit_Cost",
    "Posted_Price",
    "Current_Price",
    "Price_Lowered",
    "Price_Drop",
    "Price_History",
    "Sold_Price",
    "Price_Discount",
    "Sale_Type",
    "Sale_Date",
    "Platform",
    "Packaging_Cost",
    "Net_Profit",
    "Margin_%",
    "Favorites",
    "Views",
    "Order_ID",
    "Vinted_URL",
    "Status",
    "Date_Listed",
    "Source",
]

# Kept for backward compatibility if referenced
SALES_HEADERS = list(SOLD_ITEMS_HEADERS)

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
