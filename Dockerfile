FROM python:3.15.1-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Remove this exact security update when the pinned base includes DSA-6530-1.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade libpcre2-8-0=10.46-1~deb13u3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md LICENSE NOTICE /app/
COPY src /app/src
RUN pip install --no-deps . \
    && python -m pip uninstall --yes pip setuptools wheel \
    && python -c "import pathlib, shutil, sysconfig; shutil.rmtree(pathlib.Path(sysconfig.get_path('stdlib')) / 'ensurepip')" \
    && useradd --uid 10001 --create-home reliamesh \
    && mkdir /data && chown reliamesh:reliamesh /data \
    && find / -xdev -type f -perm /6000 -exec chmod a-s '{}' +
USER 10001:10001
ENV RM_SQLITE_PATH=/data/reliamesh.db
EXPOSE 8080
CMD ["reliamesh", "serve", "--host", "0.0.0.0", "--port", "8080"]
