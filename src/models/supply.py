from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, computed_field


class ShippingSupply(BaseModel):
    supply_id: str = Field(..., description="Unique supply ID e.g. SUP-01")
    purchase_date: Optional[date] = Field(None, description="Date supplies were purchased")
    item_description: str = Field(..., description="Supply name e.g. 10x13 Poly Mailers, Bubble Wrap")
    vendor: str = Field("", description="Store or site e.g. Amazon, Uline")
    quantity: int = Field(1, ge=1, description="Number of units in package/roll")
    total_cost: float = Field(0.0, ge=0, description="Total price paid for batch")

    @computed_field
    def unit_cost(self) -> float:
        """Unit cost per individual packaging item."""
        if self.quantity > 0:
            return round(self.total_cost / self.quantity, 3)
        return 0.0
