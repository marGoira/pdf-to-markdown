# Use a slim image to keep the footprint small
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=prod
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8000

# Install minimal system dependencies for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install dependencies first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create a non-privileged user for security
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# Expose the port defined in ENV
EXPOSE 8000

# Use python to run the script
CMD ["python", "main.py"]