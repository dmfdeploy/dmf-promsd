FROM python:3.12-slim

ARG VERSION=unknown
ARG GIT_SHA=unknown

LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/dmfdeploy/dmf-promsd"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md VERSION ./
COPY src ./src

RUN pip install --no-cache-dir .
RUN groupadd --gid 1000 dmf && \
    useradd --uid 1000 --gid dmf --home-dir /app --create-home --shell /usr/sbin/nologin dmf && \
    chown -R dmf:dmf /app

EXPOSE 8000

USER dmf

CMD ["uvicorn", "dmf_promsd.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
