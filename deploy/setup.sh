#!/bin/bash
# =============================================================
#  BinanceXBot — Setup VPS (Ubuntu 22.04 / 24.04)
#  Testado: Hetzner CX22, DigitalOcean Droplet, Oracle ARM
#
#  Uso:
#    git clone https://github.com/orgateccloud-bot/CryptoXBot /opt/binancexbot
#    cd /opt/binancexbot
#    bash deploy/setup.sh
# =============================================================

set -euo pipefail

BOT_DIR="/opt/binancexbot"
VENV="$BOT_DIR/.venv"

echo "============================================"
echo "  BinanceXBot — Setup VPS"
echo "============================================"

# ── 1. Sistema ──────────────────────────────────────────────
echo "[1/6] Atualizando sistema..."
sudo apt-get update -y && sudo apt-get upgrade -y
sudo apt-get install -y python3.11 python3.11-venv python3-pip \
    git curl build-essential libssl-dev libffi-dev python3-dev \
    caddy

# ── 2. Ambiente Python ──────────────────────────────────────
echo "[2/6] Criando venv e instalando dependências..."
cd "$BOT_DIR"
python3.11 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt

# ── 3. Diretórios de runtime ────────────────────────────────
echo "[3/6] Criando estrutura de pastas..."
mkdir -p data logs

# ── 4. Variáveis de ambiente ────────────────────────────────
echo "[4/6] Verificando .env..."
if [ ! -f "$BOT_DIR/.env" ]; then
    cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
    echo ""
    echo "  ATENÇÃO: edite o arquivo .env antes de iniciar:"
    echo "  nano $BOT_DIR/.env"
    echo ""
fi

# ── 5. Serviços systemd ─────────────────────────────────────
echo "[5/6] Registrando serviços systemd..."
sudo cp deploy/bxbot-worker.service    /etc/systemd/system/
sudo cp deploy/bxbot-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bxbot-worker bxbot-dashboard

# ── 6. Caddy (HTTPS) ────────────────────────────────────────
echo "[6/6] Configurando Caddy..."
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
echo ""
echo "  Edite /etc/caddy/Caddyfile e substitua SEU-DOMINIO.COM."
echo "  Depois: sudo systemctl reload caddy"
echo ""

echo "============================================"
echo "  Setup concluído!"
echo ""
echo "  Próximos passos:"
echo "  1. nano $BOT_DIR/.env            # preencher credenciais"
echo "  2. nano /etc/caddy/Caddyfile     # configurar domínio"
echo "  3. sudo systemctl start bxbot-worker bxbot-dashboard"
echo "  4. sudo systemctl reload caddy"
echo ""
echo "  Verificar:"
echo "  journalctl -u bxbot-worker -f"
echo "  journalctl -u bxbot-dashboard -f"
echo "  curl http://localhost:8080/health"
echo "============================================"
