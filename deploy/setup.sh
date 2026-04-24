#!/bin/bash
# =============================================================
#  BotBinance — Script de instalação no Oracle Free Tier (ARM)
#  Ubuntu 22.04 / Ampere A1
#  Execute: bash deploy/setup.sh
# =============================================================

set -e   # para se qualquer comando falhar

echo "============================================"
echo "  BotBinance — Setup Oracle Free Tier"
echo "============================================"

# ── 1. Atualizar sistema ────────────────────────────────────
echo "[1/7] Atualizando sistema..."
sudo apt-get update -y && sudo apt-get upgrade -y

# ── 2. Instalar dependências do sistema ─────────────────────
echo "[2/7] Instalando dependências do sistema..."
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    nginx git curl unzip \
    build-essential libssl-dev libffi-dev \
    python3-dev

# ── 3. Criar usuário botbinance (opcional, mais seguro) ──────
echo "[3/7] Configurando diretório..."
BOT_DIR="/opt/botbinance"
sudo mkdir -p $BOT_DIR
sudo chown $USER:$USER $BOT_DIR

# ── 4. Copiar arquivos (já devem estar em /opt/botbinance) ───
echo "[4/7] Criando ambiente virtual Python..."
cd $BOT_DIR
python3 -m venv venv
source venv/bin/activate

# ── 5. Instalar dependências Python ─────────────────────────
echo "[5/7] Instalando pacotes Python..."
pip install --upgrade pip
pip install -r requirements.txt

# ── 6. Criar pastas necessárias ─────────────────────────────
echo "[6/7] Criando estrutura de pastas..."
mkdir -p data logs

# ── 7. Configurar settings.py ───────────────────────────────
echo "[7/7] Verificando configuração..."
if [ ! -f "config/settings.py" ]; then
    cp config/settings_template.py config/settings.py
    echo ""
    echo "  ATENÇÃO: Edite config/settings.py com suas chaves!"
    echo "  nano config/settings.py"
    echo ""
fi

echo ""
echo "============================================"
echo "  Instalação concluída!"
echo ""
echo "  Próximos passos:"
echo "  1. nano config/settings.py  (inserir chaves)"
echo "  2. sudo cp deploy/botbinance.service /etc/systemd/system/"
echo "  3. sudo cp deploy/dashboard.service  /etc/systemd/system/"
echo "  4. sudo systemctl daemon-reload"
echo "  5. sudo systemctl enable botbinance dashboard"
echo "  6. sudo systemctl start  botbinance dashboard"
echo "  7. sudo cp deploy/nginx.conf /etc/nginx/sites-available/botbinance"
echo "  8. sudo ln -s /etc/nginx/sites-available/botbinance /etc/nginx/sites-enabled/"
echo "  9. sudo systemctl restart nginx"
echo "============================================"
