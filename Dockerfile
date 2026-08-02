# Install Node 24 from official image (safer than curl)
FROM node:24-slim AS node-source

# Main image: Python 3.14 slim bookworm
FROM python:3.14.6-slim-bookworm

WORKDIR /app

# Copy entire Node 24 installation from official image (verified packages, no curl)
COPY --from=node-source /usr/local/ /usr/local/

# Install unzip for OMP extraction
RUN apt-get update && apt-get install -y unzip && rm -rf /var/lib/apt/lists/*

# Install bun
RUN npm install -g bun

# Install OMP globally
RUN npm install -g @oh-my-pi/pi-coding-agent

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
COPY config/ config/

# Set volume for persistent configuration
VOLUME /app/config

# Wyoming port
EXPOSE 10300
# Web UI / API port
EXPOSE 8080

CMD ["python", "-m", "src.main"]
