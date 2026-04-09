from shared.config.settings import AppSettings


def get_settings() -> AppSettings:
    """Return application settings instance (lazy factory used by dependencies)."""
    return AppSettings()
