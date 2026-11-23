FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# PORT is injected by the host (Koyeb/Render/Railway); config.py reads it. Secrets come from the host's environment, never from the image.
CMD ["python", "main.py"]
