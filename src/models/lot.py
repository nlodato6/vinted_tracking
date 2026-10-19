from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, computed_field


class InventoryLot(BaseModel):
    lot_id: str = Field(..., description="Unique Lot ID e.g. LOT-001")
    purchase_date: Optional[date] = Field(None, description="Date lot was acquired")
    source_vendor: str = Field("", description="Where the lot was purchased")
    total_cost: float = Field(0.0, ge=0, description="Total purchase price for the lot")
    total_articles: int = Field(0, ge=0, description="Number of clothing articles in the lot")
    status: str = Field("Active", description="Active, Fully Processed, or Depleted")
    notes: str = Field("", description="Additional notes or haul details")

    @computed_field
    def unit_cost(self) -> float:
        """Calculates cost per clothing item (Total Cost / Total Articles)."""
        if self.total_articles > 0:
            return round(self.total_cost / self.total_articles, 2)
        return 0.0
