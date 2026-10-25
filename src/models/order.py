from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, computed_field


class SalesOrder(BaseModel):
    order_id: str = Field(..., description="Order ID or transaction reference")
    platform: str = Field("Vinted", description="Sales platform: Vinted, FB Marketplace, Depop, etc.")
    item_sku: str = Field("", description="Internal SKU reference from ItemListing")
    item_title: str = Field(..., description="Title of sold article")
    brand: str = Field("", description="Brand name e.g. Carhartt, Nike")
    category: str = Field("", description="Garment type e.g. Outerwear, Tops, Bottoms")
    favorites: int = Field(0, ge=0, description="Favorites count on the item when sold")
    sale_date: Optional[date] = Field(None, description="Date sold")
    sold_price: float = Field(0.0, ge=0, description="Gross selling price")
    seller_fees: float = Field(0.0, ge=0, description="Platform seller deductions if any")
    net_payout: float = Field(0.0, ge=0, description="Actual amount credited to balance")
    item_cost: float = Field(0.0, ge=0, description="Unit cost of garment from InventoryLot")
    packaging_cost: float = Field(0.0, ge=0, description="Allocated packaging & shipping supply cost")
    vinted_status: str = Field("Completed", description="In Transit, Delivered, Completed, Cancelled")
    source: str = Field("HTML", description="Originating source: HTML, Email, etc.")
    quantity: int = Field(1, ge=1, description="Quantity of garments in transaction (e.g. 2 for Bundle 2 items)")
    buyer_username: str = Field("", description="Buyer username if present")
    cancellation_reason: str = Field("", description="Reason or status details if transaction was cancelled/refunded")

    @computed_field
    def net_profit(self) -> float:
        """Net Profit = Net Payout - Item Acquisition Cost - Packaging Supply Cost."""
        return round(self.net_payout - self.item_cost - self.packaging_cost, 2)

    @computed_field
    def margin_percent(self) -> float:
        """Profit margin percentage on gross sold price."""
        if self.sold_price > 0:
            return round((self.net_profit / self.sold_price) * 100, 2)
        return 0.0
