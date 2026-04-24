# 🏗️ BotBinance - Terraform Infrastructure

Este diretório contém a infraestrutura como código (IaC) para o deployment do BotBinance na GCP usando Terraform.

## 📋 Pré-requisitos

1. **Terraform** (>= 1.0.0)
2. **Google Cloud SDK** configurado
3. **Projeto GCP** criado: `rich-streamer-v4p9v`

## 🚀 Inicialização

### 1. Autenticação
```bash
gcloud auth application-default login
gcloud config set project rich-streamer-v4p9v
```

### 2. Inicializar Terraform
```bash
cd terraform
terraform init
```

### 3. Planejar mudanças
```bash
terraform plan -var="project_id=rich-streamer-v4p9v"
```

### 4. Aplicar infraestrutura
```bash
terraform apply -var="project_id=rich-streamer-v4p9v"
```

## 📊 Recursos Criados

### 🔐 Segurança
- **Service Account**: `github-actions-deployer` para CI/CD
- **Workload Identity**: Autenticação segura do GitHub Actions
- **Secret Manager**: Armazenamento seguro das chaves da Binance
- **IAM Roles**: Princípio do menor privilégio

### 🐳 Container Registry
- **Artifact Registry**: Repositório para imagens Docker
- **Vulnerability Scanning**: Análise automática de segurança

### 🖥️ Compute
- **Compute Engine VM**: Instância COS otimizada para containers
- **Container-Optimized OS**: Sistema operacional seguro e minimalista
- **Resource Limits**: Controle de CPU e memória

### 🌐 Networking
- **Firewall Rules**: Acesso controlado às portas
- **VPC Network**: Rede isolada por padrão

### 💾 Storage
- **State Bucket**: Armazenamento do estado do Terraform
- **Versioning**: Controle de versão do state

## 🔧 Configuração

### Variáveis
```hcl
variable "project_id" {
  default = "rich-streamer-v4p9v"
}

variable "region" {
  default = "us-central1"
}

variable "zone" {
  default = "us-central1-a"
}
```

### Secrets
Após o deploy inicial, configure os secrets:
```bash
# API Key da Binance
echo -n "your-api-key" | gcloud secrets versions add binance-api-key --data-file=-

# API Secret da Binance
echo -n "your-api-secret" | gcloud secrets versions add binance-api-secret --data-file=-
```

## 📈 Outputs

Após `terraform apply`, os seguintes valores estarão disponíveis:
- `vm_external_ip`: IP público da VM
- `artifact_registry_repository`: URL do registry
- `service_account_email`: Email da service account

## 🔄 Atualizações

### Modificar Recursos
```bash
# Alterar tipo da VM
terraform plan -var="machine_type=e2-standard-2"

# Aplicar mudanças
terraform apply
```

### Rollback
```bash
# Ver histórico
terraform state list

# Rollback para versão anterior
terraform plan -destroy -target=google_compute_instance.botbinance_vm
```

## 🐛 Troubleshooting

### Problemas Comuns

**Erro de autenticação:**
```bash
gcloud auth application-default login
```

**State corrompido:**
```bash
terraform state pull > backup.tfstate
terraform init -force-copy
```

**Recursos órfãos:**
```bash
terraform import google_compute_instance.botbinance_vm projects/rich-streamer-v4p9v/zones/us-central1-a/instances/botbinance-vm
```

## 🔒 Segurança

### Princípios
- **Infrastructure as Code**: Toda infraestrutura versionada
- **Immutable Infrastructure**: Containers não modificáveis
- **Least Privilege**: Permissões mínimas necessárias
- **Secrets Management**: Chaves nunca no código

### Auditoria
```bash
# Verificar IAM policies
gcloud projects get-iam-policy rich-streamer-v4p9v

# Logs de auditoria
gcloud logging read "protoPayload.methodName=SetIamPolicy" --limit=10
```

## 📊 Monitoramento

### GCP Monitoring
- **Uptime Checks**: Monitoramento de disponibilidade
- **Logs-based Metrics**: Métricas baseadas em logs
- **Dashboards**: Painéis customizados

### Terraform Cloud (Opcional)
Para equipes maiores, considere usar Terraform Cloud para:
- **Remote State**: State compartilhado e seguro
- **Policy Checks**: Sentinel policies
- **Cost Estimation**: Estimativa de custos

## 🧹 Cleanup

Para destruir toda a infraestrutura:
```bash
terraform destroy -var="project_id=rich-streamer-v4p9v"
```

⚠️ **Atenção**: Isso irá destruir todos os recursos, incluindo dados persistentes!

---

## 📞 Suporte

Para issues com infraestrutura:
1. Verifique `terraform plan` para mudanças pendentes
2. Consulte logs do GCP para erros
3. Abra issue com `terraform state` anexado