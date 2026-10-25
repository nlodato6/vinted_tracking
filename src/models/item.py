from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class ItemListing(BaseModel):
    item_sku: str = Field(..., description="Unique internal SKU e.g. SKU-1001")
    lot_id: str = Field("", description="Reference to InventoryLot.lot_id")
    title: str = Field(..., description="Vinted listing title")
    brand: str = Field("", description="Brand name")
    category: str = Field("", description="Garment category e.g. Outerwear, Tops")
    unit_cost: float = Field(0.0, ge=0, description="Allocated cost from lot")
    posted_price: float = Field(0.0, ge=0, description="Initial posted listing price")
    current_price: float = Field(0.0, ge=0, description="Current live asking price on Vinted")
    target_price: float = Field(0.0, ge=0, description="Legacy alias for posted price")
    price_history: str = Field("", description="History log of price changes")
    sold_price: Optional[float] = Field(None, ge=0, description="Realized sold price when sold")
    favorites: int = Field(0, ge=0, description="Count of favorites / likes on Vinted")
    views: int = Field(0, ge=0, description="Count of views on Vinted")
    vinted_id: str = Field("", description="Vinted item identifier")
    vinted_url: str = Field("", description="Direct URL to listing")
    status: str = Field("Draft", description="Draft, Listed, Reserved, Sold, or Archived")
    date_listed: Optional[date] = Field(None, description="Date posted to Vinted")
    last_synced: Optional[datetime] = Field(None, description="Last scraped timestamp")
    source: str = Field("HTML", description="Originating source: HTML, Email, etc.")
