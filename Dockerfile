FROM python:3.11-slim

WORKDIR /app

# The web interface uses only Python standard library modules.
# Install optional desktop libraries if needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true

COPY . .

ENV PORT=8765
EXPOSE 8765

CMD ["python", "webapp.py"]
