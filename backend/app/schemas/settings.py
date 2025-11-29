from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    """Application settings schema."""

    auto_archive: bool = Field(default=True, description="Automatically archive prints when completed")
    save_thumbnails: bool = Field(default=True, description="Extract and save preview images from 3MF files")
    capture_finish_photo: bool = Field(default=True, description="Capture photo from printer camera when print completes")
    default_filament_cost: float = Field(default=25.0, description="Default filament cost per kg")
    currency: str = Field(default="USD", description="Currency for cost tracking")


class AppSettingsUpdate(BaseModel):
    """Schema for updating settings (all fields optional)."""

    auto_archive: bool | None = None
    save_thumbnails: bool | None = None
    capture_finish_photo: bool | None = None
    default_filament_cost: float | None = None
    currency: str | None = None
