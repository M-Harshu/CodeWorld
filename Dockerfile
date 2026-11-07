# ---- build the UI ----
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- API + built UI in one image (CPU only) ----
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=ui /ui/dist /app/ui
ENV PYTHONPATH=/app/backend CODEWORLD_DATA_DIR=/data CODEWORLD_UI_DIR=/app/ui
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "codeworld.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
