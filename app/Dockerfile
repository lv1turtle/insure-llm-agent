FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary 사용으로 빌드 의존성 불필요
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
