FROM python:3.11-slim

WORKDIR /app

# Install dependencies needed for compiling psycopg2 and other tools
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

# Expose the API port
EXPOSE 8001

# Command to run Uvicorn
CMD ["python", "-m", "uvicorn", "Back:app", "--host", "0.0.0.0", "--port", "8001"]
