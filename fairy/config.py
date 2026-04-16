import os

from pydantic import BaseModel, Field


class Settings(BaseModel):
    sprites_token: str = Field(default_factory=lambda: os.environ.get("SPRITES_TOKEN", ""))
    sprites_base_url: str = "https://api.sprites.dev"
    default_timeout: int = 600
    sprite_name_prefix: str = "fairy"


settings = Settings()
