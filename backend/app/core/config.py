from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VLM_", env_file=".env")

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".vlm-inspector")
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.image_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
