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

import threading
import time
from datetime import datetime

import requests

from config.runtime_settings import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

# ── I-9: por que 0 de 8 alertas eram entregues ────────────────
# A guarda era `if not TELEGRAM_TOKEN`, que testa VAZIO. O .env do projeto traz
# 'your_telegram_bot_token_here' -- uma string TRUTHY. Ela atravessava a guarda,
# o POST ia para um token invalido, e a funcao devolvia False SEM LOG. Resultado
# medido: 10 call sites reais, 8 tipos de alerta, zero entregas em 4 meses -- em
# silencio absoluto.
#
# Mesma classe de problema que binance_conta.py ja resolvia para as chaves da
# Binance. Deteccao por substring, nao por igualdade, porque o placeholder varia
# entre .env, .env.example e a documentacao.
_PLACEHOLDERS = ("your_", "_here", "xxx", "placeholder", "changeme", "seu_token", "coloque")

# Falha de ENTREGA e ela mesma um incidente -- e alertar sobre isso pelo canal
# que falhou nao funciona. Vai para bot_events (que agora tem leitor), com
# debounce, porque os call sites disparam por ciclo.
_ESCALA_INTERVALO_S = 900
_estado_envio = {"ultima_falha_alertada": 0.0, "em_falha": False}
_lock_envio = threading.Lock()


def token_configurado() -> bool:
    """True se token e chat_id parecem reais (nao placeholder)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if len(TELEGRAM_TOKEN) < 20:
        return False
    baixo = TELEGRAM_TOKEN.lower()
    return not any(p in baixo for p in _PLACEHOLDERS)


def _registrar_falha_de_entrega(detalhe: str) -> None:
    """Grava a falha em bot_events, com debounce. NUNCA levanta."""
    agora = time.time()
    with _lock_envio:
        novo = not _estado_envio["em_falha"]
        vencido = (agora - _estado_envio["ultima_falha_alertada"]) > _ESCALA_INTERVALO_S
        if not (novo or vencido):
            return
        _estado_envio["em_falha"] = True
        _estado_envio["ultima_falha_alertada"] = agora
    try:
        import database

        database.salvar_bot_event(
            "alerta_nao_entregue",
            f"Falha ao entregar alerta por Telegram: {detalhe}. "
            f"O canal de escalonamento esta CEGO — incidentes nao chegam a ninguem.",
            service="worker",
            severity="CRITICAL",
        )
    except Exception:
        pass


def _enviar(mensagem, *, devolver_detalhe: bool = False):
    """Envia mensagem via Telegram API.

    Devolve bool (compatibilidade com os 10 call sites) ou, com
    devolver_detalhe=True, a tupla (ok, detalhe) usada pelo modo --teste.
    """
    if not token_configurado():
        motivo = (
            "TELEGRAM_TOKEN/CHAT_ID ausentes"
            if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID
            else "TELEGRAM_TOKEN e placeholder, nao um token real"
        )
        print(f"[TELEGRAM] NAO ENVIADO: {motivo}")
        _registrar_falha_de_entrega(motivo)
        return (False, motivo) if devolver_detalhe else False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        detalhe = f"{type(e).__name__}: {e}"
        print(f"[TELEGRAM] Erro de rede: {detalhe}")
        _registrar_falha_de_entrega(detalhe)
        return (False, detalhe) if devolver_detalhe else False

    if r.status_code == 200:
        with _lock_envio:
            if _estado_envio["em_falha"]:
                _estado_envio["em_falha"] = False
                print("[TELEGRAM] Entrega restabelecida.")
        mid = None
        try:
            mid = (r.json() or {}).get("result", {}).get("message_id")
        except Exception:
            pass
        return (True, f"message_id={mid}") if devolver_detalhe else True

    # ANTES: `return r.status_code == 200` e nada mais. O corpo da resposta da
    # Telegram diz exatamente o que esta errado (token invalido, chat nao
    # encontrado, bot bloqueado) e era descartado.
    corpo = ""
    try:
        corpo = str(r.text)[:200]
    except Exception:
        pass
    detalhe = f"HTTP {r.status_code} — {corpo}"
    print(f"[TELEGRAM] FALHA na entrega: {detalhe}")
    _registrar_falha_de_entrega(detalhe)
    return (False, detalhe) if devolver_detalhe else False


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


def alerta_circuit_breaker(motivo, acao):
    """`acao` descreve o que REALMENTE aconteceu — cada chamador tem um estado
    distinto (bloqueio até destravar manual, até reset diário, suspensão
    automática por volatilidade...). Até 2026-08-19 o rodapé era fixo: "O bot
    foi pausado. Revise manualmente antes de reativar." — mentiu no episódio
    de WS das 04:43 (nada pausou; o WS se auto-curou 14 min depois) e pedia
    ação manual que não existia. Parâmetro OBRIGATÓRIO de propósito: rodapé
    genérico é como essa classe de mentira nasce."""
    msg = (
        f"⚡ <b>CIRCUIT BREAKER ATIVADO</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🔒 <b>Motivo:</b> {motivo}\n\n"
        f"<i>{acao}</i>"
    )
    return _enviar(msg)


def alerta_ws_indisponivel(rotulo, falhas_seguidas, erro):
    """WS caído NÃO é circuit breaker: nada pausa, nada exige ação manual.
    O bot segue avaliando; CVD/OBI degradam para neutro enquanto o dado
    estiver velho, e a reconexão é automática (aviso ao recuperar)."""
    msg = (
        f"🔌 <b>DADO AO VIVO INDISPONÍVEL</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"🔒 <b>Motivo:</b> WebSocket {rotulo}: {falhas_seguidas} falhas "
        f"seguidas de conexão. Último erro: {erro}\n\n"
        f"<i>O bot NÃO pausou: segue avaliando, com os componentes CVD/OBI "
        f"degradados para neutro enquanto o dado estiver velho. Reconexão "
        f"automática em curso — aviso quando recuperar. Nenhuma ação manual "
        f"necessária.</i>"
    )
    return _enviar(msg)


def alerta_ws_recuperado(rotulo, falhas_seguidas):
    """O tudo-limpo que faltou em 19/08 04:57: o operador recebeu o alarme e
    nunca a recuperação (o vigia só encaminha CRITICAL)."""
    msg = (
        f"🔌 <b>DADO AO VIVO RECUPERADO</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"WebSocket {rotulo} reconectado após {falhas_seguidas} falhas "
        f"seguidas. CVD/OBI voltam a acumular do stream."
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


def relatorio_diario(pnl_dia, trades_dia, saldo_atual, win_rate, saldo_erro=None):
    """Relatório das 18h. Régua RAZÃO: 0/0 não é 0% (win_rate None → '—') e
    saldo ilegível não é saldo zero (saldo_erro → 'sem leitura')."""
    emoji = "📈" if pnl_dia >= 0 else "📉"
    sinal = "+" if pnl_dia >= 0 else ""
    wr_txt = "—" if win_rate is None else f"{win_rate:.1f}%"
    if saldo_erro:
        saldo_txt = "sem leitura (erro na conta)"
    elif saldo_atual is None:
        saldo_txt = "—"
    else:
        saldo_txt = f"${saldo_atual:.2f}"
    msg = (
        f"{emoji} <b>RELATÓRIO DIÁRIO — CryptoXbot</b>\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n"
        f"💰 <b>PnL do Dia:</b> {sinal}${pnl_dia:.2f}\n"
        f"📊 <b>Trades fechados:</b> {trades_dia}\n"
        f"🎯 <b>Win Rate:</b> {wr_txt}\n"
        f"💼 <b>Saldo USDT (conta spot):</b> {saldo_txt}\n\n"
        f"<i>Paper trading — gerado automaticamente pelo CryptoXbot</i>"
    )
    return _enviar(msg)


def testar_conexao():
    msg = (
        f"✅ <b>CryptoXbot conectado!</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"🤖 Alertas Telegram ativos e funcionando."
    )
    ok = _enviar(msg)
    print(f"[TELEGRAM] {'Conexao OK' if ok else 'Falha na conexao'}")
    return ok


_LOTE_ESCALONAMENTO = 20


def escalar_eventos_criticos(desde_id: int | None = None) -> tuple[int, int | None]:
    """Le bot_events CRITICAL novos e os escala por Telegram (I-9).

    Devolve (quantos_escalados, ultimo_id_visto) para consumo incremental — sem
    isso, cada varredura re-alertaria todo o historico.

    Le em ordem CRESCENTE de propósito: `crescente=True` traz os mais ANTIGOS
    ainda nao vistos. Com a leitura DESC (mais novos primeiro) e um cursor que
    avanca para o maior id, um lote cheio pularia para sempre tudo que ficasse
    abaixo dele — perda silenciosa na tabela de incidentes, que e precisamente o
    defeito que esta frente existe para eliminar.

    Nao escala o proprio 'alerta_nao_entregue': avisar por Telegram que o
    Telegram falhou e loop inutil.
    """
    try:
        import database

        eventos = database.listar_bot_events(
            limite=_LOTE_ESCALONAMENTO,
            severidade="CRITICAL",
            desde_id=desde_id,
            crescente=True,
        )
    except Exception as e:
        print(f"[TELEGRAM] Nao foi possivel ler bot_events: {e}")
        return 0, desde_id

    if not eventos:
        return 0, desde_id

    maior = max(int(e["id"]) for e in eventos)
    if len(eventos) >= _LOTE_ESCALONAMENTO:
        # Nunca truncar em silencio: dizer que ha fila e o que este lote cobriu.
        print(
            f"[TELEGRAM] Lote cheio ({len(eventos)} eventos, ate id {maior}); "
            f"pode haver mais na fila — a proxima varredura continua daqui."
        )
    enviados = 0
    for ev in sorted(eventos, key=lambda x: x["id"]):
        if ev.get("event_type") == "alerta_nao_entregue":
            continue
        msg = (
            f"🚨 <b>{ev.get('event_type', 'evento')}</b>\n"
            f"⏰ {str(ev.get('timestamp'))[:19]}\n"
            f"🔧 {ev.get('service') or '-'}"
            f"{' · ' + ev['symbol'] if ev.get('symbol') else ''}\n\n"
            f"{str(ev.get('message'))[:900]}"
        )
        if _enviar(msg):
            enviados += 1
    return enviados, maior


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Alertas Telegram — diagnostico")
    ap.add_argument(
        "--teste",
        action="store_true",
        help="prova a entrega de ponta a ponta e imprime o message_id devolvido pela API",
    )
    ap.add_argument("--escalar", action="store_true", help="escala bot_events CRITICAL pendentes")
    args = ap.parse_args()

    if args.escalar:
        n, ult = escalar_eventos_criticos()
        print(f"escalados: {n} (ultimo id visto: {ult})")
        raise SystemExit(0 if n >= 0 else 1)

    # Padrao e --teste: e o criterio de saida da frente I-9.
    print(f"  token_configurado(): {token_configurado()}")
    ok, detalhe = _enviar(
        f"✅ <b>CryptoXbot — teste de entrega</b>\n"
        f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"Se esta mensagem chegou, o canal de escalonamento funciona.",
        devolver_detalhe=True,
    )
    print(f"  entrega: {'OK' if ok else 'FALHOU'} — {detalhe}")
    if not ok:
        print()
        print("  O canal de alerta esta CEGO. Enquanto isso durar, nenhum incidente")
        print("  (circuit breaker, stop atingido, posicao sem protecao, thread morta)")
        print("  chega a um humano. Configure TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no .env.")
    sys.exit(0 if ok else 1)
