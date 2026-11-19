FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN npm install -g pnpm@11.19.0 && pnpm install --frozen-lockfile
COPY frontend/ ./
COPY backend/app /build/backend/app
RUN pnpm build

FROM python:3.13-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/seed.py ./seed.py
COPY --from=frontend /build/backend/app/static ./app/static
ENV APP_ENV=production
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
