FROM python:3.11-slim

# Install system dependencies: ffmpeg, OpenCV runtime libs, Chromium (for study guide PDF compilation), fonts, Deno, and bgutil-pot
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    ca-certificates \
    curl \
    unzip \
    chromium \
    chromium-sandbox \
    fonts-liberation \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    && curl -fsSL https://deno.land/install.sh | sh -s -- -y \
    && cp /root/.deno/bin/deno /usr/local/bin/deno \
    && curl -fSL -A "Mozilla/5.0" "https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/releases/download/v0.8.1/bgutil-pot-linux-x86_64" -o /usr/local/bin/bgutil-pot \
    && chmod +x /usr/local/bin/bgutil-pot \
    && rm -rf /var/lib/apt/lists/*

# Set up a non-root user
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:/usr/local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

# Install Python requirements and latest yt-dlp nightly
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt && pip install --no-cache-dir --user --pre -U "yt-dlp"

# Copy application source
COPY --chown=user . $HOME/app

# Ensure jobs directory and cache are created and writable
RUN mkdir -p $HOME/app/jobs/_cache

ENV PORT=8080
EXPOSE 8080

CMD ["python3", "main.py"]
