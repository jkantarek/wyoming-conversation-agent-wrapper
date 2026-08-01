FROM python:3.11-slim

WORKDIR /app

# Install curl, nodejs, npm and bun for omp
RUN apt-get update && apt-get install -y curl nodejs npm unzip && rm -rf /var/lib/apt/lists/*
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
