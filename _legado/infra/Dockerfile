# Estágio 1: Build (Compilação)
FROM python:3.11-slim as builder

WORKDIR /app

# Instala dependências do sistema necessárias para compilar pacotes Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Estágio 2: Runtime (Execução)
FROM python:3.11-slim

# Instala curl e ntpdate para resolver o problema de rede e relógio do WSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ntpdate \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia as libs instaladas do estágio anterior
COPY --from=builder /install /usr/local
COPY . .

# Garante que o usuário botuser tenha permissão
RUN groupadd -r botuser && useradd -r -g botuser botuser \
    && mkdir -p data logs \
    && chown -R botuser:botuser /app

USER botuser

CMD ["python", "main.py"]