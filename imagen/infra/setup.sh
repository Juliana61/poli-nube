#!/usr/bin/env bash
# Crea los recursos de AWS una sola vez. Ejecutar con AWS CLI ya configurado.
set -euo pipefail

REGION=us-east-1
CUENTA=$(aws sts get-caller-identity --query Account --output text)
BUCKET=jcr-image-labeler
TABLA=jcr-ImageLabels
REPO=jcr-image-labeler

echo "Cuenta: $CUENTA | Bucket: $BUCKET"

# 1) Bucket de S3 para los originales (privado; se sirven con URLs firmadas)
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# 2) Tabla de DynamoDB (bajo demanda: no hay que aprovisionar capacidad)
aws dynamodb create-table \
  --table-name "$TABLA" \
  --attribute-definitions AttributeName=imageId,AttributeType=S \
  --key-schema AttributeName=imageId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION"

# 3) Repositorio de ECR donde vivirá la imagen del contenedor
aws ecr create-repository --repository-name "$REPO" --region "$REGION"

# 4) Rol de tarea con los permisos de infra/iam-policy.json
cat > /tmp/trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
"Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
aws iam create-role --role-name jcr-imageLabelerTaskRole \
  --assume-role-policy-document file:///tmp/trust.json

sed "s/<BUCKET>/$BUCKET/g; s/<CUENTA>/$CUENTA/g" infra/iam-policy.json > /tmp/policy.json
aws iam put-role-policy --role-name jcr-imageLabelerTaskRole \
  --policy-name imageLabelerAccess --policy-document file:///tmp/policy.json

echo "Listo. Registra la task definition y crea el servicio de ECS."
