# ---- Imagen base ligera y con soporte oficial -----------------------------
FROM python:3.12-slim

# No generar .pyc y enviar logs sin buffer (para verlos en CloudWatch)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Dependencias primero: aprovecha la caché de capas de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la aplicación
COPY app/ ./app/

# Usuario sin privilegios (buena práctica y requisito común de rúbrica)
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# Health check que también usa el ALB en /health
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]