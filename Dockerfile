FROM python:3.14.6-slim-bookworm

WORKDIR /app

# Install Node 24 via NodeSource, curl, unzip
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y nodejs unzip && \
    rm -rf /var/lib/apt/lists/*

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
