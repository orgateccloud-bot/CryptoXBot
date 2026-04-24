#!/bin/bash
# Script de Deploy Local para Produção
# Uso: ./deploy.sh [ambiente]

set -e

ENVIRONMENT=${1:-production}
PROJECT_ID="rich-streamer-v4p9v"
REGION="us-central1"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/botbinance/botbinance"

echo "🚀 Iniciando deploy para $ENVIRONMENT..."

# Verificar se estamos no diretório correto
if [ ! -f "docker-compose.prod.yml" ]; then
    echo "❌ Arquivo docker-compose.prod.yml não encontrado!"
    exit 1
fi

# Verificar se .env.prod existe
if [ ! -f ".env.prod" ]; then
    echo "⚠️  Arquivo .env.prod não encontrado. Copiando exemplo..."
    cp .env.prod.example .env.prod
    echo "📝 Edite o arquivo .env.prod com suas configurações antes de continuar!"
    exit 1
fi

# Build da imagem
echo "🔨 Building Docker image..."
docker build --target production -t ${IMAGE_NAME}:latest .

# Deploy com docker-compose
echo "🚀 Deploying services..."
GCP_PROJECT_ID=${PROJECT_ID} docker-compose -f docker-compose.prod.yml up -d

# Aguardar inicialização
echo "⏳ Aguardando inicialização..."
sleep 30

# Verificar saúde dos serviços
echo "🔍 Verificando saúde dos serviços..."
if docker-compose -f docker-compose.prod.yml ps | grep -q "Up"; then
    echo "✅ Deploy realizado com sucesso!"
    echo ""
    echo "📊 Status dos serviços:"
    docker-compose -f docker-compose.prod.yml ps
    echo ""
    echo "📝 Logs do bot:"
    docker-compose -f docker-compose.prod.yml logs botbinance --tail=10
else
    echo "❌ Falha no deploy. Verificando logs..."
    docker-compose -f docker-compose.prod.yml logs
    exit 1
fi

echo ""
echo "🎯 Próximos passos:"
echo "1. Configure os secrets no GCP Secret Manager"
echo "2. Execute: gcloud builds submit --tag ${IMAGE_NAME}:latest"
echo "3. Configure o Compute Engine com a imagem"