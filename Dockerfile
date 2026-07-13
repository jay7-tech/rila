# Use official Python runtime as a parent image
FROM python:3.11-slim

# Install ffmpeg for video processing
RUN apt-get update && apt-get install -y ffmpeg libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Set working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
# We install the CPU version of PyTorch first so it doesn't download 4GB+ of Nvidia CUDA libraries
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Command is specified in docker-compose.yml
