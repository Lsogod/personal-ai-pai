from __future__ import annotations

from pydantic import BaseModel, Field


class ToolPolicyItem(BaseModel):
    source: str
    name: str
    description: str = ""
    enabled: bool = True


class SkillPolicyItem(BaseModel):
    source: str
    slug: str
    name: str
    description: str = ""
    enabled: bool = True


class UserCustomizationResponse(BaseModel):
    user_id: int
    tools: list[ToolPolicyItem]
    skills: list[SkillPolicyItem]


class ToolPolicyUpdateRequest(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool


class SkillPolicyUpdateRequest(BaseModel):
    source: str = Field(min_length=1, max_length=32)
    slug: str = Field(min_length=1, max_length=128)
    enabled: bool

