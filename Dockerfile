FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache .

ENV PYTHONPATH=/app/src
EXPOSE 8000 9000
