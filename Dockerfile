# Use a base image that's compatible with Playwright's requirements.
# Ubuntu 22.04 (jammy) or 24.04 (noble) are good choices.
# For Playwright, it's often recommended to use the playwright-provided base images
# or a standard Ubuntu/Debian image and install dependencies.
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

# If you're using a different base image (e.g., standard Python image),
# you'd need to install the dependencies explicitly:
# FROM python:3.12-slim-bookworm
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     libxdamage1 \
#     libxext6 \
#     libxfixes3 \
#     libxrandr2 \
#     libgbm1 \
#     libxcb1 \
#     libxkbcommon0 \
#     libpango-1.0-0 \
#     libcairo2 \
#     libasound2t64 \
#     # And any other browsers/dependencies like ffmpeg, etc.
#     # For example, if you need Chromium, Firefox, WebKit:
#     # chromium-browser \
#     # firefox \
#     # webkit2gtk-4.0 \
#     && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# (Optional) If your Playwright script needs to be run directly on startup
# CMD ["python", "your_script.py"]
