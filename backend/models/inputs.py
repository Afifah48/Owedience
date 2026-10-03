"""Payer-supplied context. These models do not establish obligations."""
from typing import Literal
from pydantic import Field
from backend.models.domain import Model

Category = Literal['food_dining','travel_transport','shopping','accommodation','entertainment','gifts','groceries','electronics','delivery_orders','utilities','other']
SplitPreference = Literal['infer','equal','known_consumption','custom']

class FileInput(Model):
    filename: str = Field(min_length=1,max_length=200)
    mime_type: str = Field(min_length=1,max_length=100)
    data_base64: str = Field(min_length=1,max_length=14_000_000)

class ItemAssignment(Model):
    item_index: int = Field(ge=0,strict=True)
    consumers: list[str] = Field(default_factory=list)
    sharing: Literal['unsure','equal','amounts','percentages'] = 'unsure'
    amounts: dict[str,int] = Field(default_factory=dict)
    percentages: dict[str,str] = Field(default_factory=dict)

class Attachment(Model):
    id: str
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    evidence_id: str
    kind: Literal['receipt','audio']
