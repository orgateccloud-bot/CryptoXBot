# 🚀 BotBinance - Production Deployment Guide

## Visão Geral

Este guia cobre o deployment seguro e automatizado do BotBinance HFT na Google Cloud Platform.

## 📋 Pré-requisitos

### GCP Setup
1. **Projeto GCP**: `rich-streamer-v4p9v`
2. **APIs Habilitadas**:
   - Secret Manager API
   - Artifact Registry API
   - Compute Engine API
   - Cloud Logging API

3. **Service Account**:
   ```bash
   gcloud iam service-accounts create github-actions-deployer \
     --description="GitHub Actions Deployer" \
     --display-name="GitHub Actions Deployer"
   ```

4. **IAM Permissions**:
   ```bash
   gcloud projects add-iam-policy-binding rich-streamer-v4p9v \
     --member="serviceAccount:github-actions-deployer@rich-streamer-v4p9v.iam.gserviceaccount.com" \
     --role="roles/artifactregistry.writer"

   gcloud projects add-iam-policy-binding rich-streamer-v4p9v \
     --member="serviceAccount:github-actions-deployer@rich-streamer-v4p9v.iam.gserviceaccount.com" \
     --role="roles/compute.instanceAdmin.v1"
   ```

### Secrets no GCP Secret Manager
```bash
# Criar secrets
echo -n "your-binance-api-key" | gcloud secrets create binance-api-key --data-file=-
echo -n "your-binance-api-secret" | gcloud secrets create binance-api-secret --data-file=-

# Conceder acesso à service account
gcloud secrets add-iam-policy-binding binance-api-key \
  --member="serviceAccount:github-actions-deployer@rich-streamer-v4p9v.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding binance-api-secret \
  --member="serviceAccount:github-actions-deployer@rich-streamer-v4p9v.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## 🔧 Configuração Local

### 1. Arquivo de Ambiente
```bash
cp .env.prod.example .env.prod
# Edite .env.prod com suas configurações
```

### 2. Teste Local
```bash
# Build e teste local
./deploy-prod.sh

# Verificar logs
docker-compose -f docker-compose.prod.yml logs -f botbinance
```

## 🚀 Deployment na GCP

### Via GitHub Actions (Recomendado)
1. **Push para main**: O CI/CD é acionado automaticamente
2. **Monitoramento**: Acompanhe no GitHub Actions tab
3. **Verificação**: O pipeline executa smoke tests automaticamente

### Via Cloud Build (Manual)
```bash
# Build da imagem
gcloud builds submit --tag us-central1-docker.pkg.dev/rich-streamer-v4p9v/botbinance/botbinance:latest

# Deploy na VM
gcloud compute instances update-container botbinance-vm \
  --container-image us-central1-docker.pkg.dev/rich-streamer-v4p9v/botbinance/botbinance:latest \
  --container-env-file .env.prod \
  --zone us-central1-a
```

## 📊 Monitoramento

### Logs
```bash
# Logs do container
gcloud logging read "resource.type=gce_instance AND resource.labels.instance_name=botbinance-vm" --limit=50

# Logs estruturados
gcloud logging read "jsonPayload.logger=websocket" --limit=20
```

### Métricas
- **Prometheus**: `http://<vm-ip>:9090`
- **Health Check**: `http://<vm-ip>:8000/health`
- **Métricas Custom**: `http://<vm-ip>:8000/metrics`

## 🔒 Segurança

### Princípios Implementados
- **Zero Trust**: Secrets nunca em código ou containers
- **Least Privilege**: Service accounts com permissões mínimas
- **Network Isolation**: Containers em rede interna
- **Read-only Filesystem**: Proteção contra modificações
- **Resource Limits**: Prevenção de resource exhaustion

### Auditoria
```bash
# Verificar acesso aos secrets
gcloud secrets describe binance-api-key --format="value(metadata.createTime)"

# Logs de auditoria
gcloud logging read "protoPayload.methodName=AccessSecretVersion" --limit=10
```

## 🐛 Troubleshooting

### Problemas Comuns

**Container não inicia:**
```bash
# Verificar logs detalhados
docker-compose -f docker-compose.prod.yml logs botbinance

# Verificar variáveis de ambiente
docker-compose -f docker-compose.prod.yml exec botbinance env
```

**Secrets não encontrados:**
```bash
# Verificar se secrets existem
gcloud secrets list

# Testar acesso
gcloud secrets access binance-api-key --version=latest
```

**Build falha:**
```bash
# Verificar imagem no Artifact Registry
gcloud artifacts docker images list us-central1-docker.pkg.dev/rich-streamer-v4p9v/botbinance

# Logs do build
gcloud builds list --limit=5
```

## 📈 Escalabilidade

### Horizontal Scaling
```bash
# Adicionar mais instâncias
gcloud compute instance-groups managed create botbinance-group \
  --base-instance-name botbinance \
  --size 3 \
  --template botbinance-template
```

### Vertical Scaling
```bash
# Aumentar recursos da VM
gcloud compute instances set-machine-type botbinance-vm \
  --machine-type n1-standard-2 \
  --zone us-central1-a
```

## 🔄 Rollback

### Rollback via GitHub
1. Revert commit no GitHub
2. Push para main
3. CI/CD fará deploy automático da versão anterior

### Rollback Manual
```bash
# Listar versões disponíveis
gcloud artifacts docker images list us-central1-docker.pkg.dev/rich-streamer-v4p9v/botbinance/botbinance

# Deploy versão específica
gcloud compute instances update-container botbinance-vm \
  --container-image us-central1-docker.pkg.dev/rich-streamer-v4p9v/botbinance/botbinance:<commit-sha>
```

---

## 📞 Suporte

Para issues críticos:
1. Verifique logs no Cloud Logging
2. Execute smoke tests localmente
3. Abra issue no repositório com logs anexados

**Happy Trading! 🚀📈**