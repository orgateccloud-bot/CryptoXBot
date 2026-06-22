#!/bin/bash

# BotBinance Docker Deployment Script
# ===================================
# Scripts para facilitar deployment com Docker

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Função de log
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}" >&2
}

warn() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

info() {
    echo -e "${BLUE}[INFO] $1${NC}"
}

# Verificar se Docker está instalado
check_docker() {
    if ! command -v docker &> /dev/null; then
        error "Docker não está instalado. Instale o Docker primeiro."
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        error "Docker Compose não está instalado."
        exit 1
    fi

    log "Docker e Docker Compose estão instalados."
}

# Verificar variáveis de ambiente necessárias
check_env() {
    if [ -z "$BINANCE_API_KEY" ]; then
        warn "BINANCE_API_KEY não definida. Usando testnet por padrão."
        export BINANCE_TESTNET=true
    fi

    if [ -z "$BINANCE_API_SECRET" ]; then
        warn "BINANCE_API_SECRET não definida. Usando testnet por padrão."
        export BINANCE_TESTNET=true
    fi
}

# Build da imagem
build() {
    log "Construindo imagem Docker..."
    docker-compose build --no-cache
    log "Imagem construída com sucesso!"
}

# Iniciar serviços
start() {
    check_env
    log "Iniciando serviços..."
    docker-compose up -d
    log "Serviços iniciados!"

    # Aguardar health check
    info "Aguardando bot ficar saudável..."
    sleep 10

    # Verificar status
    status
}

# Parar serviços
stop() {
    log "Parando serviços..."
    docker-compose down
    log "Serviços parados!"
}

# Reiniciar serviços
restart() {
    stop
    start
}

# Verificar status dos serviços
status() {
    info "Status dos serviços:"
    docker-compose ps

    # Verificar health do bot
    if docker-compose ps botbinance | grep -q "Up"; then
        log "BotBinance está rodando!"
    else
        warn "BotBinance não está rodando."
    fi
}

# Ver logs
logs() {
    if [ -n "$1" ]; then
        docker-compose logs -f "$1"
    else
        docker-compose logs -f
    fi
}

# Executar testes no container
test() {
    log "Executando testes no container..."
    docker-compose -f docker-compose.dev.yml --profile testing run --rm tests
}

# Limpar containers e imagens não utilizadas
clean() {
    warn "Isso irá remover containers, imagens e volumes não utilizados."
    read -p "Continuar? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log "Limpando Docker..."
        docker system prune -f
        docker volume prune -f
        log "Limpeza concluída!"
    fi
}

# Deploy para produção (exemplo)
deploy_prod() {
    warn "Deploy para produção requer configuração específica."
    info "Certifique-se de que as variáveis de produção estão configuradas."
    read -p "Continuar com deploy? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        export BINANCE_TESTNET=false
        log "Deploying para produção..."
        docker-compose -f docker-compose.prod.yml up -d
        log "Deploy concluído!"
    fi
}

# Menu de ajuda
help() {
    echo "BotBinance Docker Deployment Script"
    echo ""
    echo "Uso: $0 [comando]"
    echo ""
    echo "Comandos:"
    echo "  build     - Construir imagem Docker"
    echo "  start     - Iniciar serviços"
    echo "  stop      - Parar serviços"
    echo "  restart   - Reiniciar serviços"
    echo "  status    - Verificar status dos serviços"
    echo "  logs      - Ver logs (opcional: nome do serviço)"
    echo "  test      - Executar testes no container"
    echo "  clean     - Limpar containers e imagens não utilizadas"
    echo "  deploy    - Deploy para produção (experimental)"
    echo "  help      - Mostrar esta ajuda"
    echo ""
    echo "Exemplos:"
    echo "  $0 start"
    echo "  $0 logs botbinance"
    echo "  $0 test"
}

# Função principal
main() {
    check_docker

    case "${1:-help}" in
        build)
            build
            ;;
        start)
            start
            ;;
        stop)
            stop
            ;;
        restart)
            restart
            ;;
        status)
            status
            ;;
        logs)
            logs "$2"
            ;;
        test)
            test
            ;;
        clean)
            clean
            ;;
        deploy)
            deploy_prod
            ;;
        help|*)
            help
            ;;
    esac
}

# Executar função principal
main "$@"