import os


class Settings:
    """환경변수 기반 설정. docker-compose 에서 주입됩니다."""

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://insurance:insurance@localhost:5432/insurance",
    )
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


settings = Settings()
