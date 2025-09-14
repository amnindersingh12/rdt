# Use official slim Python 3.11 image as base
FROM python:3.11-slim

# Update package lists and upgrade existing packages;
# Install essential build tools and timezone data;
# Clean up apt cache to reduce image size.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential tzdata && \
    rm -rf /var/lib/apt/lists/*

# Set container timezone to Indian Standard Time (IST)
ENV TZ=Asia/Kolkata

# Upgrade pip and install a fixed version of wheel for compatibility
RUN pip install --no-cache-dir -U pip wheel

# Set working directory in the container
WORKDIR /app

# Copy only requirements file first to leverage Docker layer caching
COPY requirements.txt /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all remaining application files to the container
COPY . /app

# Default command to run the application
CMD ["python3", "main.py"]
