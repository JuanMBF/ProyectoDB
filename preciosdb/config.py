"""Settings read from environment variables (or a local .env file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLite by default so the project runs without Docker; docker-compose switches it to MySQL.
    database_url: str = "sqlite:///./data/precios.db"

    # Raw zone. Empty disables it: prices are still saved to the relational database.
    mongo_url: str = ""
    mongo_database: str = "preciosdb"

    # Polite scraping: one request at a time with a pause between them.
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 20.0
    user_agent: str = "Mozilla/5.0 (compatible; preciosdb/1.0; +https://github.com/JuanMBF/ProyectoDB)"

    # Coto's catalog search is served by Constructor.io. The key is the public one embedded in
    # Coto's own website JavaScript, it is not a secret.
    coto_constructor_url: str = "https://ac.cnstrc.com"
    coto_constructor_key: str = "key_r6xzz4IAoTWcipni"
    coto_product_base_url: str = "https://www.cotodigital.com.ar/sitios/cdigi/productos"

    dia_base_url: str = "https://diaonline.supermercadosdia.com.ar"
    carrefour_base_url: str = "https://www.carrefour.com.ar"


settings = Settings()
