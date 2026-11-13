# KERP backend image (T-0004). Build context: backend/
#
#   docker build -t kerp-backend backend
#   docker run --rm -p 8000:8000 kerp-backend
#
# Reproducible: every Python dependency comes from a hashed lock file
# (requirements.lock / requirements-build.lock, see README "Container image") and is
# installed as a pre-built wheel only (--only-binary=:all:), so no compiler is needed
# and nothing is built from an sdist. The base image is pinned by tag + multi-arch index
# digest, written in each FROM (not via ARG) so Dependabot (.github/dependabot.yml) can
# propose digest refreshes. Keep both FROM lines identical.

# --- build: runtime virtualenv + the kerp-backend wheel -----------------------------------
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_INPUT=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src

# Runtime dependencies first so this layer is cached until the lock changes.
COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r requirements.lock

# Build backend (hatchling) in a throwaway venv; only the wheel leaves this stage.
COPY requirements-build.lock ./
RUN python -m venv /opt/buildenv \
    && /opt/buildenv/bin/pip install --require-hashes --only-binary=:all: -r requirements-build.lock

COPY pyproject.toml README.md ./
COPY app ./app
RUN /opt/buildenv/bin/hatchling build --target wheel --directory /wheels \
    && /opt/venv/bin/pip install --no-deps --no-index --only-binary=:all: /wheels/kerp_backend-*.whl \
    && /opt/venv/bin/pip check

# --- runtime ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime

# Production (the default) refuses to start without KERP_DEFAULT_TENANT_ID; auth also needs a
# JWT signing key and KERP_REDIS_URL. See README.md "Container image".
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KERP_ENVIRONMENT=production

RUN groupadd --system --gid 10001 kerp \
    && useradd --system --uid 10001 --gid kerp --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin kerp

COPY --from=build /opt/venv /opt/venv

WORKDIR /srv/kerp
USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
