"""
Alertas Telegram — BotBinance
===============================
Envia notificações para o seu celular via Telegram.

CONFIGURAÇÃO (uma vez só):
  1. Abra o Telegram e busque por: @BotFather
  2. Envie: /newbot → siga as instruções → copie o TOKEN
  3. Busque seu bot pelo nome e envie qualquer mensagem
  4. Acesse: https://api.telegram.org/bot<TOKEN>/getUpdates
     Copie o "chat_id" do resultado
  5. Preencha TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no config/settings.py

Alertas enviados:
  - Sinal de COMPRA ou VENDA gerado
  - Trade aberto e fechado (com PnL)
  - Stop Loss atingido
  - Circuit Breaker ativado
  - Relatório diário (18h)
"""

from datetime import datetime

import requests

from config.runtime_settings import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN


def _enviar(mensagem):
    """Envia mensagem via Telegram API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token ou Chat ID nao configurados. Pule este modulo.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": mensagem,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM] Erro: {e}")
        return False


# ── Tipos de alertas ──────────────────────────────────────────


def alerta_sinal(tipo, preco, stop, target, filtros_ok, filtros_total, ml_prob=None, par="BTCUSDT"):
    emoji = "🟢" if tipo == "COMPRA" else "🔴"
    ml_txt = f"\n📊 <b>ML Prob:</b> {ml_prob*100:.1f}%" if ml_prob else ""
    msg = (
        f"{emoji} <b>SINAL {tipo}</b> — {par}\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"💰 <b>Preço:</b> ${preco:,.2f}\n"
        f"🛑 <b>Stop Loss:</b> ${stop:,.2f}\n"
        f"🎯 <b>Take Profit:</b> ${target:,.2f}\n"
        f"✅ <b>Filtros:</b> {filtros_ok}/{filtros_total}{ml_txt}\n\n"
        f"⚠️ <i>Aguarde confirmação do executor antes de operar.</i>"
    )
    return _enviar(msg)


def alerta_trade_aberto(tipo, preco, tamanho_btc, stop, target):
    emoji = "📈" if tipo == "LONG" else "📉"
    msg = (
        f"{emoji} <b>TRADE ABERTO</b> — {tipo}\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"💰 <b>Entrada:</b> ${preco:,.2f}\n"
        f"📦 <b>Tamanho:</b> {tamanho_btc:.6f} BTC (${tamanho_btc*preco:,.2f})\n"
        f"🛑 <b>Stop Loss:</b> ${stop:,.2f}\n"
        f"🎯 <b>Target 1:</b> ${target:,.2f}"
    )
    return _enviar(msg)


def alerta_trade_fechado(tipo, entrada, saida, pnl_usdt, pnl_pct, motivo):
    emoji = "✅" if pnl_usdt >= 0 else "❌"
    sinal = "+" if pnl_usdt >= 0 else ""
    msg = (
        f"{emoji} <b>TRADE FECHADO</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🎯 <b>Motivo:</b> {motivo}\n"
        f"📥 <b>Entrada:</b> ${entrada:,.2f}\n"
        f"📤 <b>Saída:</b> ${saida:,.2f}\n"
        f"💵 <b>PnL:</b> {sinal}${pnl_usdt:.2f} ({sinal}{pnl_pct:.2f}%)"
    )
    return _enviar(msg)


def alerta_stop(preco, pnl_usdt):
    msg = (
        f"🛑 <b>STOP LOSS ATINGIDO</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"💰 <b>Preço:</b> ${preco:,.2f}\n"
        f"💸 <b>Perda:</b> -${abs(pnl_usdt):.2f}\n\n"
        f"<i>O bot continua monitorando o próximo sinal.</i>"
    )
    return _enviar(msg)


def alerta_circuit_breaker(motivo):
    msg = (
        f"⚡ <b>CIRCUIT BREAKER ATIVADO</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🔒 <b>Motivo:</b> {motivo}\n\n"
        f"<i>O bot foi pausado. Revise manualmente antes de reativar.</i>"
    )
    return _enviar(msg)


def alerta_trailing_stop(novo_stop, pico):
    msg = (
        f"📐 <b>Trailing Stop Ajustado</b>\n"
        f"Novo stop: ${novo_stop:,.2f}\n"
        f"Pico atingido: ${pico:,.2f}"
    )
    return _enviar(msg)


def alerta_persistencia_falhou(symbol, tipo, preco, tamanho_btc):
    msg = (
        f"🚨 <b>URGENTE — POSIÇÃO SEM REGISTRO NO BANCO</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"⚠️ Ordem <b>{tipo}</b> preenchida em {symbol} @ ${preco:,.2f} "
        f"({tamanho_btc:.6f}), mas o banco de dados falhou ao persistir a "
        f"posição após novas tentativas.\n\n"
        f"<i>A proteção (stop/OCO) já está na exchange. Verifique manualmente "
        f"se um restart do bot recupera esta posição.</i>"
    )
    return _enviar(msg)


def relatorio_diario(pnl_dia, trades_dia, saldo_atual, win_rate):
    emoji = "📈" if pnl_dia >= 0 else "📉"
    sinal = "+" if pnl_dia >= 0 else ""
    msg = (
        f"{emoji} <b>RELATÓRIO DIÁRIO — BotBinance</b>\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n"
        f"💰 <b>PnL do Dia:</b> {sinal}${pnl_dia:.2f}\n"
        f"📊 <b>Trades:</b> {trades_dia}\n"
        f"🎯 <b>Win Rate:</b> {win_rate:.1f}%\n"
        f"💼 <b>Saldo Atual:</b> ${saldo_atual:.2f}\n\n"
        f"<i>Gerado automaticamente pelo BotBinance</i>"
    )
    return _enviar(msg)


def testar_conexao():
    msg = (
        f"✅ <b>BotBinance conectado!</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"🤖 Alertas Telegram ativos e funcionando."
    )
    ok = _enviar(msg)
    print(f"[TELEGRAM] {'Conexao OK' if ok else 'Falha na conexao'}")
    return ok


if __name__ == "__main__":
    testar_conexao()
