FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AI_LIBRARY_DB_PATH=/data/prompts.sqlite3 AI_LIBRARY_STATIC_DIR=/app/frontend/dist
WORKDIR /app
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ ./backend/
RUN pip install --no-cache-dir --no-deps ./backend \
    && groupadd --gid 1000 library && useradd --uid 1000 --gid library --no-create-home library \
    && mkdir /data && chown library:library /data
COPY --from=frontend /build/dist ./frontend/dist/
COPY presets/ ./presets/
USER library
EXPOSE 8765
CMD ["ai-library", "serve", "--host", "0.0.0.0", "--port", "8765"]
