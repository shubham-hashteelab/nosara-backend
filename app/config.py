from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://nosara:nosara@localhost:5432/nosara"
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "nosara-snags"
    MINIO_USE_SSL: bool = False
    VLLM_BASE_URL: str = "http://localhost:8000"
    VLLM_API_KEY: str = ""
    VLLM_MODEL: str = "Qwen/Qwen3-VL-4B-Instruct"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 10080  # 7 days
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "https://nosara-portal.vercel.app",
    ]

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
