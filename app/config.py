import os


class Settings:
    """환경변수 기반 설정. docker-compose 에서 주입됩니다."""

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://insurance:insurance@localhost:5432/insurance",
    )
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

    # RabbitMQ (worker 가 메시지를 소비). compose 에서 rabbitmq 호스트로 덮어씀.
    rabbitmq_url: str = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    # worker 가 소비할 요청 큐 이름
    request_queue: str = os.getenv("REQUEST_QUEUE", "agent.requests")


settings = Settings()
