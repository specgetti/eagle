# App image: eagle-web (default command) and eagle-worker (`eagle worker`).
# Stage 1: build the React UI
FROM registry.access.redhat.com/ubi9/nodejs-20:latest AS ui
WORKDIR /opt/app-root/src
COPY --chown=1001:0 frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY --chown=1001:0 frontend/ ./
RUN npm run build

# Stage 2: API server and worker with the built UI
FROM registry.access.redhat.com/ubi9/python-312:latest
WORKDIR /opt/app-root/src
RUN pip install --no-cache-dir uv
COPY backend/pyproject.toml backend/uv.lock ./
# Dependencies first, so a code change does not reinstall them (layer cache).
RUN uv sync --no-dev --frozen --no-install-project
COPY backend/eagle ./eagle
RUN uv sync --no-dev --frozen
COPY --from=ui /opt/app-root/src/dist ./ui

ENV PATH=/opt/app-root/src/.venv/bin:$PATH \
    EAGLE_FRONTEND_DIST=/opt/app-root/src/ui \
    EAGLE_AUTO_CREATE_SCHEMA=false \
    EAGLE_COOKIE_SECURE=true
EXPOSE 8000
# OpenShift runs with an arbitrary UID in group 0; the UBI image is prepared for that.
# Migrations: `eagle migrate` (initContainer). Worker: `eagle worker`.
CMD ["uvicorn", "--factory", "eagle.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
