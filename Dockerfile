FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
# Create a dummy package to cache dependency installation
RUN mkdir -p /app/app && touch /app/app/__init__.py
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Now copy the actual source code
COPY app /app/app
COPY examples /app/examples

# Update the app package itself without redownloading dependencies
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
