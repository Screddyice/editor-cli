"""Path-free edit decisions exposed to the agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Cut(StrictModel):
    asset_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    kind: Literal["speech", "broll"] = "speech"
    speed: float = Field(default=1, ge=0.25, le=4)
    volume: float = Field(default=1, ge=0, le=2)
    grade: Literal["none", "neutral", "warm"] = "none"
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end - self.start < 0.1:
            raise ValueError("Cuts must contain at least 100 ms")
        return self


class Title(StrictModel):
    text: str = Field(min_length=1, max_length=200)
    start: float = Field(ge=0)
    duration: float = Field(gt=0, le=120)
    position: Literal["top", "center", "bottom"] = "center"


class Overlay(StrictModel):
    asset_id: str
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    source_start: float = Field(default=0, ge=0)
    layout: Literal["full", "pip"] = "full"


class Music(StrictModel):
    asset_id: str
    volume: float = Field(default=0.12, ge=0, le=1)
    duck: bool = True


class EditPlan(StrictModel):
    cuts: list[Cut] = Field(min_length=1, max_length=500)
    width: int = Field(default=1920, ge=128, le=3840)
    height: int = Field(default=1080, ge=128, le=3840)
    fps: int = Field(default=30, ge=12, le=60)
    titles: list[Title] = Field(default_factory=list, max_length=100)
    overlays: list[Overlay] = Field(default_factory=list, max_length=100)
    captions: bool = False
    music: Music | None = None

    @model_validator(mode="after")
    def timeline(self):
        if self.width % 2 or self.height % 2:
            raise ValueError("Output dimensions must be even")
        duration = sum((c.end - c.start) / c.speed for c in self.cuts)
        if duration > 7200:
            raise ValueError("Direct edits are limited to two hours")
        if any(t.start + t.duration > duration + 0.001 for t in self.titles + self.overlays):
            raise ValueError("Titles and overlays must fit the output timeline")
        return self
