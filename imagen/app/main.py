"""
Image Labeler - backend
Sube una imagen a S3, la analiza con Amazon Rekognition y guarda las
etiquetas detectadas en DynamoDB.
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("image-labeler")

# --- Configuración (viene de variables de entorno de la task de ECS) --------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET_NAME = os.getenv("BUCKET_NAME")
TABLE_NAME = os.getenv("TABLE_NAME", "ImageLabels")
MAX_LABELS = int(os.getenv("MAX_LABELS", "15"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "70"))
MAX_FILE_MB = 5  # Rekognition acepta hasta 15 MB desde S3

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}

s3 = boto3.client("s3", region_name=AWS_REGION)
rekognition = boto3.client("rekognition", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(TABLE_NAME)

app = FastAPI(title="Image Labeler", version="1.0.0")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# --- Utilidades ------------------------------------------------------------
def to_plain(obj):
    """DynamoDB devuelve Decimal; el JSON no lo entiende."""
    if isinstance(obj, list):
        return [to_plain(i) for i in obj]
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def presigned_url(key: str, seconds: int = 3600) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=seconds,
    )


# --- Endpoints -------------------------------------------------------------
@app.get("/health")
def health():
    """Lo usa el health check del Application Load Balancer."""
    return {"status": "ok", "region": AWS_REGION, "table": TABLE_NAME}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    if not BUCKET_NAME:
        raise HTTPException(500, "Falta la variable de entorno BUCKET_NAME")

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Solo se aceptan imágenes JPG o PNG")

    data = await file.read()
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(400, f"La imagen supera los {MAX_FILE_MB} MB")

    image_id = str(uuid.uuid4())
    key = f"uploads/{image_id}{ALLOWED_TYPES[file.content_type]}"

    # 1. Guardar el original en S3
    try:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=data,
            ContentType=file.content_type,
        )
    except ClientError as e:
        log.exception("Error subiendo a S3")
        raise HTTPException(502, f"No se pudo guardar la imagen: {e}")

    # 2. Analizar con Rekognition (lee directo desde S3, no reenvía bytes)
    try:
        result = rekognition.detect_labels(
            Image={"S3Object": {"Bucket": BUCKET_NAME, "Name": key}},
            MaxLabels=MAX_LABELS,
            MinConfidence=MIN_CONFIDENCE,
        )
    except ClientError as e:
        log.exception("Error en Rekognition")
        raise HTTPException(502, f"Rekognition falló: {e}")

    labels = [
        {
            "name": lb["Name"],
            "confidence": Decimal(str(round(lb["Confidence"], 2))),
            "parents": [p["Name"] for p in lb.get("Parents", [])],
            "instances": len(lb.get("Instances", [])),
        }
        for lb in result.get("Labels", [])
    ]

    item = {
        "imageId": image_id,
        "filename": file.filename,
        "s3Key": key,
        "contentType": file.content_type,
        "sizeBytes": len(data),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "labelCount": len(labels),
        "labels": labels,
    }

    # 3. Persistir el resultado en DynamoDB
    try:
        table.put_item(Item=item)
    except ClientError as e:
        log.exception("Error escribiendo en DynamoDB")
        raise HTTPException(502, f"No se pudo guardar el resultado: {e}")

    log.info("Analizada %s -> %d etiquetas", file.filename, len(labels))

    response = to_plain(item)
    response["imageUrl"] = presigned_url(key)
    return JSONResponse(response)


@app.get("/api/items")
def list_items(limit: int = 12):
    """Últimos análisis. Para una tabla pequeña de clase, Scan es suficiente."""
    try:
        resp = table.scan(Limit=100)
    except ClientError as e:
        raise HTTPException(502, f"No se pudo leer DynamoDB: {e}")

    items = sorted(
        to_plain(resp.get("Items", [])),
        key=lambda i: i.get("createdAt", ""),
        reverse=True,
    )[:limit]

    for it in items:
        it["imageUrl"] = presigned_url(it["s3Key"])
    return {"count": len(items), "items": items}


@app.get("/api/items/{image_id}")
def get_item(image_id: str):
    resp = table.get_item(Key={"imageId": image_id})
    if "Item" not in resp:
        raise HTTPException(404, "No existe ese análisis")
    item = to_plain(resp["Item"])
    item["imageUrl"] = presigned_url(item["s3Key"])
    return item


# --- Frontend estático servido por el mismo contenedor ---------------------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))