FROM python:3.11-slim

# Create non-root user with UID 1000
RUN useradd -u 1000 -m -s /bin/bash appuser

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY handler.py .

# Change ownership to appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

CMD ["kopf", "run", "--standalone", "--all-namespaces", "handler.py"]