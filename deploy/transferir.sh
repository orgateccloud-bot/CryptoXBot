#!/bin/bash
# =============================================================
#  BotBinance — Transferir código para Oracle Free Tier
#  Execute na sua máquina local (Windows Git Bash / WSL)
#
#  Uso: bash deploy/transferir.sh <IP_DO_SERVIDOR>
#  Ex:  bash deploy/transferir.sh 168.138.xx.xx
# =============================================================

IP=${1:?"Informe o IP do servidor: bash deploy/transferir.sh <IP>"}
USER="ubuntu"
KEY="$HOME/.ssh/oracle_key"   # caminho da chave SSH privada
DEST="/opt/botbinance"
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Transferindo BotBinance para $USER@$IP:$DEST ..."

# Criar pasta remota
ssh -i "$KEY" "$USER@$IP" "sudo mkdir -p $DEST && sudo chown $USER:$USER $DEST"

# Sincronizar arquivos (exclui segredos e dados locais)
rsync -avz --progress \
    --exclude 'config/settings.py' \
    --exclude 'data/*.db' \
    --exclude 'data/*.pkl' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'logs/' \
    --exclude '.git/' \
    --exclude 'venv/' \
    -e "ssh -i $KEY" \
    "$BOT_DIR/" "$USER@$IP:$DEST/"

echo ""
echo "Transferência concluída!"
echo ""
echo "Agora no servidor:"
echo "  ssh -i $KEY $USER@$IP"
echo "  cd $DEST"
echo "  bash deploy/setup.sh"
