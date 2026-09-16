from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database - own instance, fully independent from DSS/servis and from
    # Evidence naradi (no shared tables, no shared DB, no shared session).
    kj_db_name: str = "kniha_jizd"
    kj_db_user: str = "kniha_jizd_app"
    kj_db_password: str = ""
    db_host: str = "kniha-jizd-postgres"
    db_port: int = 5432

    # Auth secret - generated once, stored in .env, never in Git. Must differ
    # from every other app's SESSION_SECRET_KEY on the shared VPS.
    session_secret_key: str

    environment: str = "production"

    # Photo attachments (odometer shots, fuel receipts, defect photos,
    # vehicle/service photos). Matches /opt/kniha-jizd/data/photos on the VPS.
    photos_dir: str = "/opt/kniha-jizd/data/photos"
    # Vehicle documents (TP/OTP/green card/...) - PDFs and images alike, kept
    # apart from photos because they are never resized and are served under
    # a stricter permission gate.
    documents_dir: str = "/opt/kniha-jizd/data/documents"

    # --- E-mail notifications (Etapa 7) --------------------------------
    # With smtp_host empty the app still records every notification in the
    # database and shows it in-app; only the actual mail send is skipped.
    # A mail outage must never break a trip/reservation workflow.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "kniha-jizd@azunimb.cz"
    smtp_starttls: bool = True

    # --- Map service (Etapa 9) -----------------------------------------
    # Orientation-only road-distance check for a typed route. "none"
    # disables it; no tracking, no stored GPS points, ever.
    maps_provider: str = "none"
    maps_nominatim_url: str = "https://nominatim.openstreetmap.org"
    maps_osrm_url: str = "https://router.project-osrm.org"
    maps_user_agent: str = "kniha-jizd/0.1"
    maps_timeout_seconds: float = 6.0

    # --- OCR (Etapa 5) --------------------------------------------------
    # Helper only - every OCR result is shown to the user for confirmation
    # and the app works fully with ocr_provider="none".
    ocr_provider: str = "none"
    ocr_tesseract_cmd: str = ""

    @property
    def photos_path(self) -> Path:
        return Path(self.photos_dir)

    @property
    def documents_path(self) -> Path:
        return Path(self.documents_dir)

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.kj_db_user}:{self.kj_db_password}"
            f"@{self.db_host}:{self.db_port}/{self.kj_db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
