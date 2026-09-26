FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2
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
