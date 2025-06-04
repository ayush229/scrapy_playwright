# Use the official Playwright Python image.
# This image comes with Playwright, browsers, and their system dependencies pre-installed.
# 'jammy' refers to Ubuntu 22.04 LTS, which is stable and widely supported.
# Make sure the version (e.g., v1.43.0) matches or is close to your
# 'playwright' library version in requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

# Set the working directory inside the container
WORKDIR /app

# Copy only requirements.txt first to leverage Docker's build cache
# This helps Docker reuse layers if only your application code changes
COPY requirements.txt .

# Install Python dependencies
# --no-cache-dir: Reduces image size by not caching pip packages
# --upgrade pip: Ensures pip itself is up-to-date
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
# This should be the last COPY instruction to optimize caching
COPY . .

# Expose the port that your Flask application will listen on.
# Railway expects your application to listen on 8080 by default.
EXPOSE 8080

# Command to run your application.
# This tells Railway how to start your Flask application when the container runs.
# It assumes your Flask application is defined in 'main.py' and your Flask instance is named 'app'.
# We use 'python3' for clarity, though 'python' might work too depending on the base image.
CMD ["python3", "main.py"]
