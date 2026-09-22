FROM python:3.13-slim@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md LICENSE NOTICE /app/
COPY src /app/src
RUN pip install --no-deps . && useradd --uid 10001 --create-home reliamesh && mkdir /data && chown reliamesh:reliamesh /data
USER 10001:10001
ENV RM_SQLITE_PATH=/data/reliamesh.db
EXPOSE 8080
CMD ["reliamesh", "serve", "--host", "0.0.0.0", "--port", "8080"]
