FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema necessárias para numpy/scipy/XGBoost
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Layer de cache: dependências antes do código
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Diretórios de runtime (gitignored; criados aqui para o container)
RUN mkdir -p data logs

# Padrão: worker. Sobrescrever no docker-compose para dashboard.
CMD ["python", "main.py", "--simulacao", "--intervalo", "15"]
