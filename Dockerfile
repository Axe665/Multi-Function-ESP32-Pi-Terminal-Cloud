FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
# pg_dump 给 worker 的每日备份用；api 用不到但不值得为它多打一个镜像
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY cloud ./cloud
COPY contracts ./contracts
RUN useradd -r -u 10001 cloud \
 && mkdir -p /app/data /backups \
 && chown -R cloud /app/data /backups
USER cloud
ENV CLOUD_DATA_DIR=/app/data CLOUD_BACKUP_DIR=/backups
EXPOSE 8002
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8002/api/v1/health')"
CMD ["python", "-m", "uvicorn", "cloud.main:app", "--host", "0.0.0.0", "--port", "8002"]
