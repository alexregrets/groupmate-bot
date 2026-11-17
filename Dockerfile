FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home groupmate \
    && mkdir /app/state && chown groupmate:groupmate /app/state
COPY groupmate ./groupmate
COPY prompts ./prompts
COPY main.py .
USER groupmate
CMD ["python", "main.py"]
