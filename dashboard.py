"""

Dashboard BotBinance — Servidor Flask + SocketIO
Abre no navegador: http://localhost:5000

APIs disponíveis:
  GET  /api/estado            → estado completo BTC
  GET  /api/estado/<symbol>   → estado por par
  GET  /api/pares             → estado de todos os pares ativos
  GET  /api/trades            → últimos trades grandes
  GET  /api/sinais            → histórico de sinais
  GET  /api/score/<symbol>    → score detalhado por par
  GET  /api/ml/<symbol>       → probabilidade ML por par
  GET  /api/risco             → gestão de risco
  WS   /                      → Socket.IO (eventos: update, trade, score_update)
"""

import hmac as _hmac
import json
import os
import sys
import threading
import time
from datetime import datetime

import requests
import websocket
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

import database
from config.runtime_settings import (
    APP_ENV,
    CORS_ORIGINS,
    CORS_SAME_ORIGIN_ONLY,
    PORT,
    REST_BASE_URL,
    SECRET_KEY,
    SYMBOL_WS,
    WHALE_BTC_VOLUME,
    WS_BASE_URL,
)

# Console Windows padrao e cp1252, e este arquivo imprime "→"/"─"/emoji: o
# primeiro deles matava execucao MANUAL com UnicodeEncodeError (sob NSSM nao
# ha console, por isso o servico 24/7 nunca sentiu). Mesma guarda dos scripts.
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

app = Flask(__name__, static_folder="frontend/dist", static_url_path="")
app.config["SECRET_KEY"] = SECRET_KEY
# Flask-CORS e Socket.IO tem semanticas diferentes para "so mesma origem":
# [] no Flask-CORS nega qualquer cross-origin (visita direta ao dashboard nao
# usa CORS, entao nao e afetada); None no Socket.IO faz o engineio deduzir a
# origem a partir do proprio request (ver CORS_SAME_ORIGIN_ONLY em
# config/runtime_settings.py) -- [] quebraria o websocket ate para o proprio
# dashboard.
CORS(
    app,
    resources={r"/api/*": {"origins": [] if CORS_SAME_ORIGIN_ONLY else CORS_ORIGINS}},
)
socketio = SocketIO(
    app,
    cors_allowed_origins=None if CORS_SAME_ORIGIN_ONLY else CORS_ORIGINS,
    async_mode="threading",
)

# P0-5: token opcional para todas as rotas /api/*. Se DASHBOARD_TOKEN estiver
# no ambiente, exigimos "Authorization: Bearer <token>" ou "?token=<token>".
_DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

# P2: rate limit leve, em processo (sem dependencia nova) — janela fixa por IP
# nas rotas /api/*. Suficiente para um dashboard pessoal single-user; barra
# scraping/DoS trivial se algum dia exposto na rede.
_RATE_LIMITE = int(os.getenv("DASHBOARD_RATE_LIMIT", "120"))  # req/janela
_RATE_JANELA = 60.0  # segundos
_rate_hits: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


@app.before_request
def _rate_limit_api():
    if not request.path.startswith("/api/"):
        return None
    ip = request.remote_addr or "?"
    agora = time.time()
    with _rate_lock:
        janela = [t for t in _rate_hits.get(ip, []) if agora - t < _RATE_JANELA]
        if len(janela) >= _RATE_LIMITE:
            _rate_hits[ip] = janela
            return jsonify({"erro": "rate limit excedido"}), 429
        janela.append(agora)
        _rate_hits[ip] = janela
    return None


@app.before_request
def _exigir_token_api():
    if not _DASHBOARD_TOKEN or not request.path.startswith("/api/"):
        return None
    fornecido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not fornecido:
        fornecido = request.args.get("token", "")
    if not _hmac.compare_digest(fornecido, _DASHBOARD_TOKEN):
        return jsonify({"erro": "nao autorizado"}), 401
    return None


@app.after_request
def _headers_seguranca(resp):
    # P2: CSP + hardening. Scripts/estilos so do proprio host + inline (o
    # dashboard usa <script>/<style> inline). Fontes do Google permitidas
    # (nao executam codigo). connect-src inclui o WS do proprio host.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; base-uri 'self'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return resp


BASE_URL = REST_BASE_URL  # P0-1: endpoint unico (spot por padrao) vindo do config
BASE_FAPI = "https://fapi.binance.com"

PARES_ATIVOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


# ── Estado global por par ──────────────────────────────────────
def _estado_inicial(symbol):
    return {
        "symbol": symbol,
        "preco": 0.0,
        "variacao_24h": 0.0,
        "maximo_24h": 0.0,
        "minimo_24h": 0.0,
        "volume_24h": 0.0,
        "ema20": 0.0,
        "ema50": 0.0,
        "rsi": 0.0,
        "atr": 0.0,
        "tendencia": "—",
        "tend_4h": "—",
        "funding": 0.0,
        "cvd": 0.0,
        "compras": 0.0,
        "vendas": 0.0,
        "sinal": "AGUARDAR",
        "score": 0,
        "score_decisao": "AGUARDAR",
        "score_detail": {},
        "stop_loss": None,
        "take_profit": None,
        "ml_prob": None,
        "ml_ensemble": None,
        "ml_xgb": None,
        "ml_lstm": None,
        "ml_confianca": "—",
        "regime": "—",
        "regime_score": 0,
        "fear_greed": 0,
        "fear_greed_pt": "—",
        "pressao_ob": "—",
        "liq_compra": 0.0,
        "liq_venda": 0.0,
        "ultima_atualizacao": "—",
        "params": {},
    }


estados = {s: _estado_inicial(s) for s in PARES_ATIVOS}
trades_recentes = []
preco_historico = {s: [] for s in PARES_ATIVOS}
eventos_sistema = []  # lista de eventos em tempo real (max 200)
_lock = threading.Lock()


# ── Sistema de Eventos ─────────────────────────────────────────

_TIPOS_EVENTO = {
    "SINAL": {"cor": "#00ff88", "icone": "◈"},
    "SCORE": {"cor": "#00d4ff", "icone": "◉"},
    "REGIME": {"cor": "#9945ff", "icone": "▣"},
    "ALERTA": {"cor": "#ff3366", "icone": "⚠"},
    "BALEIA": {"cor": "#ffcc00", "icone": "🐋"},
    "INFO": {"cor": "#6b7394", "icone": "▸"},
}


def addSystemEvent(tipo, titulo, mensagem):
    """Cria e emite um evento de sistema via Socket.IO."""
    meta = _TIPOS_EVENTO.get(tipo, _TIPOS_EVENTO["INFO"])
    ev = {
        "id": int(time.time() * 1000),
        "tipo": tipo,
        "titulo": titulo,
        "mensagem": mensagem,
        "ts": datetime.now().strftime("%H:%M:%S"),
        "cor": meta["cor"],
        "icone": meta["icone"],
    }
    with _lock:
        eventos_sistema.insert(0, ev)
        if len(eventos_sistema) > 200:
            eventos_sistema.pop()
    socketio.emit("evento", ev)


# ── Helpers indicadores ────────────────────────────────────────


def _klines(symbol, intervalo, limite=60):
    r = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": intervalo, "limit": limite},
        timeout=8,
    )
    k = r.json()
    return {
        "fechamento": [float(x[4]) for x in k],
        "maxima": [float(x[2]) for x in k],
        "minima": [float(x[3]) for x in k],
        "volume": [float(x[5]) for x in k],
    }


def _ema(vals, periodo):
    k = 2 / (periodo + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return round(e, 2)


def _rsi(vals, periodo=14):
    g, p = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        g.append(max(d, 0))
        p.append(max(-d, 0))
    mg = sum(g[-periodo:]) / periodo
    mp = sum(p[-periodo:]) / periodo
    return round(100 - (100 / (1 + mg / mp)), 2) if mp else 100.0


def _atr(maximas, minimas, fechamentos, periodo=14):
    trs = []
    for i in range(1, len(fechamentos)):
        h, l, pc = maximas[i], minimas[i], fechamentos[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs[-periodo:]) / periodo, 2) if trs else 0.0


# ── Coleta de dados por par (a cada 30s) ──────────────────────


def atualizar_par(symbol):
    try:
        # Ticker 24h
        t = requests.get(
            f"{BASE_URL}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=8
        ).json()

        # Order book
        ob = requests.get(
            f"{BASE_URL}/api/v3/depth", params={"symbol": symbol, "limit": 20}, timeout=8
        ).json()
        liq_c = sum(float(p) * float(q) for p, q in ob.get("bids", []))
        liq_v = sum(float(p) * float(q) for p, q in ob.get("asks", []))

        # Klines 1H e 4H
        k1h = _klines(symbol, "1h", 60)
        k4h = _klines(symbol, "4h", 60)

        f1h = k1h["fechamento"]
        f4h = k4h["fechamento"]

        ema20 = _ema(f1h[-20:], 20)
        ema50 = _ema(f1h, 50)
        rsi = _rsi(f1h)
        atr = _atr(k1h["maxima"], k1h["minima"], f1h)

        ema20_4h = _ema(f4h[-20:], 20)
        ema50_4h = _ema(f4h, 50)

        preco = float(t["lastPrice"])

        tend = "ALTA" if preco > ema20 > ema50 else "BAIXA" if preco < ema20 < ema50 else "LATERAL"
        tend_4h = (
            "ALTA"
            if f4h[-1] > ema20_4h > ema50_4h
            else "BAIXA" if f4h[-1] < ema20_4h < ema50_4h else "LATERAL"
        )

        # Funding (apenas futuros BTC)
        funding = 0.0
        if symbol == "BTCUSDT":
            try:
                fr = requests.get(
                    f"{BASE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=5
                ).json()
                funding = round(float(fr.get("lastFundingRate", 0)) * 100, 4)
            except Exception:
                pass

        # Capturar estado anterior para detectar mudanças
        with _lock:
            sinal_anterior = estados[symbol]["sinal"]
            score_anterior = estados[symbol]["score"]
            regime_anterior = estados[symbol]["regime"]

        # Estratégia + Score — SOMENTE LEITURA (E-7)
        #
        # `analisar()` gravava uma linha em `sinais` a cada chamada, e este loop
        # do dashboard a chamava a cada 30s POR PAR, apenas para exibir. Ou seja:
        # a camada de apresentacao escrevia na mesma tabela que relatorio_gate.py
        # le para julgar se existe edge, e o fazia ~2.880 vezes por dia por par,
        # em concorrencia com o worker. Desde E-7 a escrita e explicita
        # (otimizada.registrar_sinal) e so o worker chama — `analisar()` e puro,
        # entao esta chamada aqui virou inofensiva por construcao, nao por
        # convencao.
        sinal_res = {}
        try:
            from estrategias.otimizada import analisar

            with _lock:
                cvd_snap = estados[symbol]["cvd"]
            sinal_res = analisar(symbol=symbol, cvd_atual=cvd_snap)
        except Exception:
            pass

        from config.params_pares import get_params

        params = get_params(symbol)

        with _lock:
            e = estados[symbol]
            e["preco"] = preco
            e["variacao_24h"] = float(t["priceChangePercent"])
            e["maximo_24h"] = float(t["highPrice"])
            e["minimo_24h"] = float(t["lowPrice"])
            e["volume_24h"] = float(t["volume"])
            e["ema20"] = ema20
            e["ema50"] = ema50
            e["rsi"] = rsi
            e["atr"] = atr
            e["tendencia"] = tend
            e["tend_4h"] = tend_4h
            e["funding"] = funding
            e["pressao_ob"] = "COMPRA" if liq_c > liq_v else "VENDA"
            e["liq_compra"] = liq_c
            e["liq_venda"] = liq_v
            e["ultima_atualizacao"] = datetime.now().strftime("%H:%M:%S")
            e["params"] = {
                "stop_pct": params["stop_pct"],
                "target_pct": params["target_pct"],
                "rsi_min": params["rsi_min"],
                "rsi_max": params["rsi_max"],
            }

            if sinal_res:
                e["sinal"] = sinal_res.get("sinal", "AGUARDAR")
                e["score"] = sinal_res.get("score", 0)
                e["score_decisao"] = sinal_res.get("score_decisao", "AGUARDAR")
                e["stop_loss"] = sinal_res.get("stop_loss")
                e["take_profit"] = sinal_res.get("take_profit")
                e["tend_4h"] = sinal_res.get("tend_4h", tend_4h)
                e["regime"] = sinal_res.get("regime", "—")
                e["regime_score"] = sinal_res.get("regime_score", 0)
                e["fear_greed"] = sinal_res.get("fear_greed", 0)
                e["fear_greed_pt"] = sinal_res.get("fear_greed_pt", "—")
                e["ml_prob"] = sinal_res.get("ml_prob")
                e["ml_ensemble"] = sinal_res.get("ml_ensemble")
                e["ml_xgb"] = sinal_res.get("ml_xgb")
                e["ml_lstm"] = sinal_res.get("ml_lstm")
                e["ml_confianca"] = sinal_res.get("ml_confianca", "—")
                sr = sinal_res.get("score_result", {})
                e["score_detail"] = sr.get("scores", {})

            preco_historico[symbol].append({"t": datetime.now().strftime("%H:%M"), "v": preco})
            if len(preco_historico[symbol]) > 120:
                preco_historico[symbol].pop(0)

        # Emitir para browsers
        with _lock:
            payload = dict(estados[symbol])
            novo_sinal = estados[symbol]["sinal"]
            novo_score = estados[symbol]["score"]
            novo_regime = estados[symbol]["regime"]
        payload["preco_historico"] = list(preco_historico[symbol])
        socketio.emit("update", payload)
        socketio.emit(f"update_{symbol.lower()}", payload)

        # ── Eventos de mudança de estado ──────────────────────
        sym = symbol.replace("USDT", "")

        # Sinal novo (diferente do anterior e não-trivial)
        if novo_sinal != sinal_anterior and novo_sinal != "AGUARDAR":
            sl = payload.get("stop_loss")
            tp = payload.get("take_profit")
            sc = payload.get("score", 0)
            msg = (
                f"Score {sc} | Stop ${sl:,.2f} | Target ${tp:,.2f}" if sl and tp else f"Score {sc}"
            )
            addSystemEvent("SINAL", f"{sym} {novo_sinal}", msg)

        elif novo_sinal == "AGUARDAR" and sinal_anterior != "AGUARDAR":
            addSystemEvent(
                "INFO", f"{sym} sinal encerrado", f"Voltou para AGUARDAR | Score {novo_score}"
            )

        # Score cruzou limiar CHEIO (subiu)
        if novo_score >= 70 > score_anterior:
            addSystemEvent(
                "SCORE", f"{sym} SCORE CHEIO", f"Score {novo_score}/100 — Condições excelentes"
            )

        # Score caiu abaixo do mínimo
        if novo_score < 60 <= score_anterior:
            addSystemEvent(
                "ALERTA",
                f"{sym} score abaixo mínimo",
                f"Score {novo_score}/100 — Abaixo do limiar de operação",
            )

        # Mudança de regime
        if novo_regime != regime_anterior and novo_regime not in ("—", "INDEFINIDO"):
            regime_fmt = novo_regime.replace("TENDENCIA_", "").replace("_", " ")
            cor_ev = "REGIME" if "ALTA" in novo_regime else "ALERTA"
            addSystemEvent(
                cor_ev,
                f"{sym} regime: {regime_fmt}",
                f"Anterior: {regime_anterior.replace('TENDENCIA_', '').replace('_', ' ')}",
            )

    except Exception as e:
        print(f"[SNAPSHOT {symbol} ERRO] {e}")


def loop_snapshots():
    while True:
        for symbol in PARES_ATIVOS:
            threading.Thread(target=atualizar_par, args=(symbol,), daemon=True).start()
        time.sleep(30)


# ── WebSocket Binance BTC (CVD em tempo real) ──────────────────


def ws_on_message(ws_app, message):
    data = json.loads(message)
    price = float(data["p"])
    quantity = float(data["q"])
    is_buyer_maker = data["m"]
    direcao = "VENDA" if is_buyer_maker else "COMPRA"

    with _lock:
        if is_buyer_maker:
            estados["BTCUSDT"]["cvd"] -= quantity
            estados["BTCUSDT"]["vendas"] += quantity
        else:
            estados["BTCUSDT"]["cvd"] += quantity
            estados["BTCUSDT"]["compras"] += quantity

    if quantity >= 0.1:
        try:
            # E-8: passa o trade_id do agregado para que esta escrita DEDUPE
            # contra a do worker, que consome o mesmo stream @aggTrade e ja
            # passava o id.
            #
            # Sem ele, `salvar_trade` insere sem chave de conflito: medido em
            # 2026-08-08, 2.488.666 linhas com trade_id NULL (esta rota) contra
            # 386.124 com id (o worker). Ou seja ~87% da tabela `trades` era
            # duplicata que a dedupe existente nao tinha como pegar — o campo
            # existia, a clausula ON CONFLICT existia, e este chamador
            # simplesmente nao preenchia a chave.
            #
            # `a` e o id do trade AGREGADO no payload de @aggTrade (nao `t`, que
            # e o id do trade individual — mesma distincao que causou o bug de
            # CVD corrigido em P0).
            database.salvar_trade(
                price,
                quantity,
                direcao,
                WHALE_BTC_VOLUME,
                symbol="BTCUSDT",
                trade_id=data.get("a"),
            )
        except Exception:
            pass

    if quantity >= 0.5:
        trade = {
            "hora": datetime.now().strftime("%H:%M:%S"),
            "symbol": "BTCUSDT",
            "direcao": direcao,
            "preco": price,
            "volume": round(quantity, 3),
            "valor_usdt": round(price * quantity, 0),
            "baleia": quantity >= WHALE_BTC_VOLUME,
        }
        with _lock:
            trades_recentes.insert(0, trade)
            if len(trades_recentes) > 60:
                trades_recentes.pop()
            cvd_atual = estados["BTCUSDT"]["cvd"]

        socketio.emit("trade", {**trade, "cvd": round(cvd_atual, 3)})

        if trade["baleia"]:
            valor_fmt = (
                f"${trade['valor_usdt']/1e3:.0f}K"
                if trade["valor_usdt"] < 1e6
                else f"${trade['valor_usdt']/1e6:.2f}M"
            )
            addSystemEvent(
                "BALEIA",
                f"BTC BALEIA {direcao}",
                f"{round(quantity, 2)} BTC — {valor_fmt} @ ${price:,.0f}",
            )


def iniciar_ws():
    url = f"{WS_BASE_URL}/ws/{SYMBOL_WS}@aggTrade"  # P0-1: spot por padrao
    while True:
        ws = websocket.WebSocketApp(url, on_message=ws_on_message)
        ws.run_forever(ping_interval=30, ping_timeout=10)
        time.sleep(5)


# ── Rotas Flask ────────────────────────────────────────────────


@app.route("/")
def index():
    # Tenta servir o frontend Lovable compilado; fallback para template Flask
    try:
        return send_from_directory("frontend/dist", "index.html")
    except Exception:
        return render_template("dashboard.html")


@app.route("/vendor/<path:filename>")
def vendor(filename):
    # P2: libs (socket.io, chart.js) servidas localmente — zero confianca em CDN
    # externo (elimina risco de supply-chain e funciona offline).
    return send_from_directory("static/vendor", filename)


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "role": "dashboard",
            "database": database.backend_info(),
        }
    )


@app.route("/ready")
def ready():
    ok = database.healthcheck()
    return jsonify(
        {
            "status": "ok" if ok else "degraded",
            "role": "dashboard",
            "database": database.backend_info(),
        }
    ), (200 if ok else 503)


@app.route("/api/pares")
def api_pares():
    """Estado resumido de todos os pares ativos."""
    with _lock:
        return jsonify(
            [
                {
                    "symbol": s,
                    "preco": estados[s]["preco"],
                    "variacao_24h": estados[s]["variacao_24h"],
                    "sinal": estados[s]["sinal"],
                    "score": estados[s]["score"],
                    "tendencia": estados[s]["tendencia"],
                    "tend_4h": estados[s]["tend_4h"],
                    "rsi": estados[s]["rsi"],
                    "regime": estados[s]["regime"],
                }
                for s in PARES_ATIVOS
            ]
        )


@app.route("/api/estado")
@app.route("/api/estado/<symbol>")
def api_estado(symbol="BTCUSDT"):
    symbol = symbol.upper()
    if symbol not in estados:
        return jsonify({"erro": f"Par {symbol} nao ativo"}), 404
    with _lock:
        payload = dict(estados[symbol])
    payload["preco_historico"] = list(preco_historico.get(symbol, []))
    return jsonify(payload)


@app.route("/api/score/<symbol>")
def api_score(symbol="BTCUSDT"):
    symbol = symbol.upper()
    with _lock:
        e = estados.get(symbol, {})
    return jsonify(
        {
            "symbol": symbol,
            "score": e.get("score", 0),
            "decisao": e.get("score_decisao", "AGUARDAR"),
            "detalhes": e.get("score_detail", {}),
            "sinal": e.get("sinal", "AGUARDAR"),
            "regime": e.get("regime", "—"),
            "regime_score": e.get("regime_score", 0),
            "fear_greed": e.get("fear_greed", 0),
            "tend_4h": e.get("tend_4h", "—"),
            "ml_ensemble": e.get("ml_ensemble"),
            "ml_xgb": e.get("ml_xgb"),
            "ml_lstm": e.get("ml_lstm"),
            "ml_confianca": e.get("ml_confianca", "—"),
            "params": e.get("params", {}),
        }
    )


@app.route("/api/eventos")
def api_eventos():
    """Eventos do PROPRIO processo do dashboard (deque em memoria).

    Nao confundir com /api/bot_events, que le a tabela `bot_events` escrita pelo
    WORKER — sao fontes diferentes e este endpoint nunca viu um incidente do bot.
    """
    with _lock:
        return jsonify(list(eventos_sistema[:100]))


@app.route("/api/bot_events")
def api_bot_events():
    """LEITOR da tabela bot_events (I-9).

    A tabela tinha 14 escritores no worker e ZERO leitor em todo o repositorio:
    thread_crash CRITICAL, divergencia local-vs-exchange, fill sem persistencia,
    reidratacao recusada e a trava permanente todos escreviam ali e morriam ali.
    4 linhas em 4 meses num destino que ninguem consultava.

    Query: ?severidade=CRITICAL&tipo=bot_travado&limite=50
    """
    import database as _db

    try:
        limite = max(1, min(int(request.args.get("limite", 100)), 500))
    except (TypeError, ValueError):
        limite = 100
    sev = request.args.get("severidade") or None
    tipo = request.args.get("tipo") or None
    try:
        eventos = _db.listar_bot_events(limite=limite, severidade=sev, tipo=tipo)
    except Exception as exc:
        return jsonify({"erro": str(exc)[:200], "eventos": []}), 503
    return jsonify({"total": len(eventos), "eventos": eventos})


@app.route("/api/trades")
def api_trades():
    with _lock:
        return jsonify(list(trades_recentes))


@app.route("/api/sinais")
def api_sinais():
    return jsonify(database.buscar_sinais(limit=30))


@app.route("/api/ml")
@app.route("/api/ml/<symbol>")
def api_ml(symbol="BTCUSDT"):
    symbol = symbol.upper()
    try:
        from ml_filtro import prever

        prob, msg = prever(symbol)
        return jsonify(
            {
                "symbol": symbol,
                "prob": prob,
                "msg": msg,
                "aprovado": prob >= 0.60 if prob else False,
            }
        )
    except Exception as e:
        return jsonify({"symbol": symbol, "prob": None, "msg": str(e), "aprovado": False})


@app.route("/api/risco")
def api_risco():
    import risco

    return jsonify(risco.status())


@app.route("/api/lucro")
def api_lucro():
    """Campo LUCRO — PnL de PAPER, leitura pura (doutrina E-8e: o dashboard
    nao escreve). Mesma regua do relatorio_gate: so a estrategia primaria
    (source NULL conta como primaria, historico pre-disciplina)."""
    conn = database.conectar()
    try:
        row = conn.execute("SELECT data FROM risk_state WHERE name='default'").fetchone()
        pnl_dia = 0.0
        if row:
            try:
                pnl_dia = float(json.loads(row["data"]).get("pnl_dia", 0.0) or 0.0)
            except (ValueError, TypeError):
                pnl_dia = 0.0
        agg = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(pnl_usdt),0) AS total,"
            " COALESCE(SUM(pnl_usdt > 0),0) AS ganhos"
            " FROM sinais WHERE pnl_usdt IS NOT NULL"
            " AND (source IS NULL OR source = 'estrategia_otimizada')"
        ).fetchone()
        ultimo = conn.execute(
            "SELECT timestamp, symbol, pnl_usdt, barreira_tocada FROM sinais"
            " WHERE pnl_usdt IS NOT NULL"
            " AND (source IS NULL OR source = 'estrategia_otimizada')"
            " ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        n = int(agg["n"] or 0)
        return jsonify(
            {
                "modo": "PAPER",
                "pnl_dia": pnl_dia,
                "pnl_total": float(agg["total"] or 0.0),
                "trades_fechados": n,
                "win_rate": (float(agg["ganhos"] or 0) / n * 100) if n else None,
                "ultimo": (
                    {
                        "quando": ultimo["timestamp"],
                        "symbol": ultimo["symbol"],
                        "pnl_usdt": ultimo["pnl_usdt"],
                        "barreira": ultimo["barreira_tocada"],
                    }
                    if ultimo
                    else None
                ),
            }
        )
    finally:
        conn.close()


@app.route("/api/atividade")
def api_atividade():
    """BOT em acao — as ultimas avaliacoes de log_avaliacoes, o 'pensamento'
    do worker por ciclo. Leitura pura."""
    conn = database.conectar()
    try:
        try:
            rows = conn.execute(
                "SELECT timestamp, symbol, preco, score, decisao, regime,"
                " ml_ensemble, bloqueios FROM log_avaliacoes ORDER BY id DESC LIMIT 14"
            ).fetchall()
        except Exception:
            rows = []  # banco novo sem a tabela do logger: feed vazio, nao 500
        return jsonify(
            [
                {
                    "quando": r["timestamp"],
                    "symbol": r["symbol"],
                    "preco": r["preco"],
                    "score": r["score"],
                    "decisao": r["decisao"],
                    "regime": r["regime"],
                    "ml": r["ml_ensemble"],
                    "bloqueios": r["bloqueios"] if isinstance(r["bloqueios"], str) else "",
                }
                for r in rows
            ]
        )
    finally:
        conn.close()


@app.route("/api/equity")
def api_equity():
    """Curva de equity do PAPER — PnL acumulado trade a trade (E-8e: leitura
    pura). Mesma regua do relatorio_gate e do /api/lucro: so a estrategia
    primaria (source NULL conta como primaria, historico pre-disciplina)."""
    try:
        limite = max(10, min(int(request.args.get("limite", 500)), 2000))
    except (TypeError, ValueError):
        limite = 500
    conn = database.conectar()
    try:
        try:
            rows = conn.execute(
                "SELECT timestamp, symbol, pnl_usdt, barreira_tocada FROM sinais"
                " WHERE pnl_usdt IS NOT NULL"
                " AND (source IS NULL OR source = 'estrategia_otimizada')"
                " ORDER BY timestamp ASC"
            ).fetchall()
        except Exception:
            rows = []  # banco novo sem as colunas de meta-labeling: curva vazia
        pontos = []
        cum = 0.0
        for r in rows:
            pnl = float(r["pnl_usdt"] or 0.0)
            cum += pnl
            pontos.append(
                {
                    "t": r["timestamp"],
                    "symbol": r["symbol"],
                    "pnl": round(pnl, 4),
                    "cum": round(cum, 4),
                    "barreira": r["barreira_tocada"],
                }
            )
        return jsonify(
            {
                "modo": "PAPER",
                "pontos": pontos[-limite:],
                "total": round(cum, 4),
                "n": len(pontos),
            }
        )
    finally:
        conn.close()


@app.route("/api/analytics")
def api_analytics():
    """Análises do PAPER sobre trades fechados da estratégia primária —
    mesma régua do gate (E-8e: leitura pura). `?dias=7|14|30` filtra o
    período; ausente ou 0 = tudo. Sem dados, devolve estruturas VAZIAS —
    nunca um número inventado (doutrina RAZÃO; o anti-exemplo está
    documentado na análise do cryptoxbot.zip de 2026-08-16)."""
    from datetime import timedelta

    try:
        dias = max(0, min(int(request.args.get("dias", 0)), 3650))
    except (TypeError, ValueError):
        dias = 0

    conn = database.conectar()
    try:
        try:
            rows = conn.execute(
                "SELECT timestamp, symbol, pnl_usdt FROM sinais"
                " WHERE pnl_usdt IS NOT NULL"
                " AND (source IS NULL OR source = 'estrategia_otimizada')"
                " ORDER BY timestamp ASC"
            ).fetchall()
        except Exception:
            rows = []
    finally:
        conn.close()

    trades = [
        {"t": str(r["timestamp"]), "symbol": r["symbol"], "pnl": float(r["pnl_usdt"] or 0.0)}
        for r in rows
    ]
    if dias:
        corte = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
        trades = [t for t in trades if t["t"][:10] >= corte]

    # diário: barras de PnL + capital acumulado (a curva composta da IDEIA #1)
    por_dia: dict[str, dict] = {}
    for t in trades:
        d = por_dia.setdefault(t["t"][:10], {"pnl": 0.0, "n": 0, "wins": 0})
        d["pnl"] += t["pnl"]
        d["n"] += 1
        d["wins"] += 1 if t["pnl"] > 0 else 0
    diario = []
    cum = 0.0
    for dia in sorted(por_dia):
        d = por_dia[dia]
        cum += d["pnl"]
        diario.append(
            {
                "dia": dia,
                "pnl": round(d["pnl"], 4),
                "cum": round(cum, 4),
                "n": d["n"],
                "wins": d["wins"],
            }
        )

    # win rate móvel trade a trade (IDEIA #2 — referência: pisos pré-registrados)
    win_movel = []
    wins = 0
    for i, t in enumerate(trades):
        wins += 1 if t["pnl"] > 0 else 0
        win_movel.append(
            {
                "t": t["t"],
                "symbol": t["symbol"],
                "pnl": round(t["pnl"], 4),
                "wr": round(wins / (i + 1) * 100, 1),
            }
        )

    # PnL por janela horária (IDEIA #3) e por par (IDEIA #4)
    por_hora = [{"hora": h, "pnl": 0.0, "n": 0} for h in range(24)]
    for t in trades:
        try:
            h = int(t["t"][11:13])
        except (ValueError, IndexError):
            continue
        if 0 <= h <= 23:
            por_hora[h]["pnl"] = round(por_hora[h]["pnl"] + t["pnl"], 4)
            por_hora[h]["n"] += 1

    pares: dict[str, dict] = {}
    for t in trades:
        p = pares.setdefault(t["symbol"], {"pnl": 0.0, "n": 0, "wins": 0})
        p["pnl"] = round(p["pnl"] + t["pnl"], 4)
        p["n"] += 1
        p["wins"] += 1 if t["pnl"] > 0 else 0
    por_par = [{"symbol": s, **v} for s, v in sorted(pares.items())]

    # ribbon do período (IDEIA #5) — profit factor NULO sem perdas, jamais sentinela
    ganho_bruto = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    perda_bruta = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    dias_pos = sum(1 for d in diario if d["pnl"] > 0)
    ribbon = {
        "n_trades": len(trades),
        "win_rate": round(wins / len(trades) * 100, 1) if trades else None,
        "pnl_total": round(sum(t["pnl"] for t in trades), 4),
        "dias_com_trade": len(diario),
        "media_por_dia": round(sum(t["pnl"] for t in trades) / len(diario), 4) if diario else None,
        "pct_dias_positivos": round(dias_pos / len(diario) * 100, 1) if diario else None,
        "melhor_dia": max(diario, key=lambda d: d["pnl"]) if diario else None,
        "pior_dia": min(diario, key=lambda d: d["pnl"]) if diario else None,
        "profit_factor": round(ganho_bruto / perda_bruta, 2) if perda_bruta > 0 else None,
    }
    return jsonify(
        {
            "modo": "PAPER",
            "dias_filtro": dias,
            "diario": diario,
            "win_movel": win_movel,
            "por_hora": por_hora,
            "por_par": por_par,
            "ribbon": ribbon,
            # referências HONESTAS: pisos pré-registrados, não benchmark inventado
            "referencias": {
                "pf_piso_etapa2": 1.3,  # GATE_GO_LIVE.md, Etapa 2
                "wr_equilibrio_barreira": 33.3,  # barreira 2:1 (alvo 2×stop)
            },
        }
    )


# O caminho ao real e PRE-REGISTRADO (docs/GATE_GO_LIVE.md, research/METODOLOGIA*.md):
# FAIL nunca e revogado e o hold-out e de uso unico. Este endpoint so EXIBE o
# estado — nao ha rota que mude gate nenhum, por construcao.
_HOLDOUT_ABRE = "2026-12-01"


@app.route("/api/gates")
def api_gates():
    """A regua do caminho ao trading real, sem maquiagem. Leitura pura:
    constantes pre-registradas + flags vivas do ambiente + arquivos de
    veredito. O dashboard mostra a verdade; quem gira chave e o operador."""
    from datetime import date

    from config.runtime_settings import ALLOW_REAL_TRADING, DRY_RUN

    raiz = os.path.dirname(os.path.abspath(__file__))
    hoje = date.today()
    holdout = date(*[int(x) for x in _HOLDOUT_ABRE.split("-")])

    medicao_micro = os.path.exists(
        os.path.join(raiz, "research", "vereditos", "microestrutura_pesquisa_BTCUSDT.json")
    )
    barras = None
    try:
        # ultima contagem do vigia diario (barato de ler; a contagem exata
        # exigiria carregar os bancos de book/tape a cada hit da rota)
        with open(
            os.path.join(raiz, "logs", "micro_vigia.log"), encoding="utf-8", errors="replace"
        ) as f:
            for linha in f:
                if "barras completas na pesquisa:" in linha:
                    barras = linha.rsplit(":", 1)[1].strip()
    except OSError:
        pass

    ignicao_flags = {
        "dry_run_false": not DRY_RUN,
        "allow_real_true": bool(ALLOW_REAL_TRADING),
        "env_production": APP_ENV == "production",
    }
    etapas = [
        {
            "id": "pesquisa_micro",
            "nome": "Pesquisa microestrutura (E-11)",
            "status": "MEDIDA" if medicao_micro else "EM_COLETA",
            "detalhe": (
                "primeira medicao feita — ver research/vereditos/"
                if medicao_micro
                else f"coletando book 24/7 — {barras or 'aguardando contagem do vigia'}"
            ),
            "data": "~2026-08-25",
        },
        {
            "id": "holdout",
            "nome": "Hold-out (uso unico)",
            "status": "TRANCADO",
            "detalhe": f"abre em {_HOLDOUT_ABRE} — faltam {max((holdout - hoje).days, 0)} dias",
            "data": _HOLDOUT_ABRE,
        },
        {
            "id": "etapa1",
            "nome": "Gate Etapa 1 — edge com expectativa positiva",
            "status": "REPROVADA",
            "detalhe": "walk-forward honesto negativo; FAIL pre-registrado e final",
            "data": None,
        },
        {
            "id": "etapa2",
            "nome": "Etapa 2 — paper 90d com profit factor",
            "status": "AGUARDA_ETAPA1",
            "detalhe": "so abre com Etapa 1 aprovada por uma estrategia nova",
            "data": None,
        },
        {
            "id": "etapa3",
            "nome": "Etapa 3 — micro-capital 30d",
            "status": "AGUARDA_ETAPA2",
            "detalhe": None,
            "data": None,
        },
        {
            "id": "ignicao",
            "nome": "IGNICAO REAL",
            "status": "BLOQUEADA" if not all(ignicao_flags.values()) else "ARMADA",
            "detalhe": "DRY_RUN=false + ALLOW_REAL_TRADING=true + ENV=production + --real",
            "data": None,
            "flags": ignicao_flags,
        },
    ]
    # Funil de hipóteses — a Etapa 1 só reabre por uma família APROVADA.
    # Históricas: FAIL pré-registrado é final (constantes honestas).
    # Novas (MOMO/VOLT): lidas dos vereditos que os próprios labs gravam.
    frentes = [
        {
            "nome": "Edge multi-sinal 1h (edge_lab)",
            "status": "FAIL",
            "quando": "2026-07",
            "detalhe": "IC indistinguível de aleatório",
        },
        {
            "nome": "Trend Donchian diário",
            "status": "FAIL",
            "quando": "2026-08",
            "detalhe": "hold-out +5,7% a.a. vs piso 8%",
        },
        {
            "nome": "Carry funding v1",
            "status": "FAIL",
            "quando": "2026-08",
            "detalhe": "hold-out consumido",
        },
        {
            "nome": "Microestrutura OFI/CVD (E-11)",
            "status": "MEDIDA" if medicao_micro else "EM_COLETA",
            "quando": "~2026-08-25",
            "detalhe": barras,
        },
    ]
    for lab, nome in (
        ("momo", "MOMO — rotação BTC/ETH/SOL"),
        ("volt", "VOLT — vol-targeting spot"),
    ):
        caminho = os.path.join(raiz, "research", "vereditos", f"{lab}_pesquisa.json")
        try:
            with open(caminho, encoding="utf-8") as f:
                v = json.load(f)
            frentes.append(
                {
                    "nome": nome,
                    "status": v.get("veredito_pesquisa", "?"),
                    "quando": str(v.get("quando", ""))[:10],
                    "detalhe": (
                        f"{v.get('sobreviventes', 0)} sobrevivente(s) em "
                        f"{len(v.get('combos', []))} trials"
                    ),
                }
            )
        except (OSError, ValueError):
            frentes.append(
                {
                    "nome": nome,
                    "status": "NAO_MEDIDA",
                    "quando": None,
                    "detalhe": "pré-registrada, ainda sem medição",
                }
            )
    frentes.append(
        {
            "nome": "CARRY v2 — funding em janela nova",
            "status": "AGENDADA",
            "quando": "2026-11-16",
            "detalhe": "janela 15/08→15/11; medir antes da data anula o contrato",
        }
    )
    return jsonify({"etapas": etapas, "frentes": frentes, "hoje": hoje.isoformat()})


_RELATORIO_PRODUCAO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "RELATORIO_PRODUCAO.md"
)


@app.route("/api/diario")
def api_diario():
    """Última entrada de diário do RELATORIO_PRODUCAO.md — leitura pura.

    O razão imprime o próprio editorial: a fonte é o arquivo versionado no
    repositório, sem cópia que possa divergir. Sem arquivo ou sem seção de
    diário, devolve vazio (nunca 500)."""
    try:
        with open(_RELATORIO_PRODUCAO, encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return jsonify({"titulo": None, "corpo": None})
    marcador = "\n## Diário —"
    pos = texto.rfind(marcador)
    if pos == -1:
        return jsonify({"titulo": None, "corpo": None})
    trecho = texto[pos + 1 :]
    fim = trecho.find("\n## ", 1)
    if fim != -1:
        trecho = trecho[:fim]
    linhas = trecho.splitlines()
    return jsonify(
        {
            "titulo": linhas[0].lstrip("# ").strip(),
            "corpo": "\n".join(linhas[1:]).strip(),
            "fonte": "docs/RELATORIO_PRODUCAO.md",
        }
    )


@app.route("/api/sistema")
def api_sistema():
    """Servicos NSSM + relogios agendados — o 'bot esta vivo' verificavel.
    `sc query` e consulta (nao muda estado); em ambiente sem os servicos a
    resposta degrada para AUSENTE/INDISPONIVEL em vez de 500."""
    import subprocess
    from datetime import timedelta

    servicos = []
    for nome in ("BXBotWorker", "BXBotDashboard", "BXBotBook"):
        estado = "DESCONHECIDO"
        try:
            r = subprocess.run(["sc", "query", nome], capture_output=True, text=True, timeout=4)
            if "RUNNING" in r.stdout:
                estado = "RODANDO"
            elif "STOPPED" in r.stdout:
                estado = "PARADO"
            elif r.returncode != 0:
                estado = "AUSENTE"
        except Exception:
            estado = "INDISPONIVEL"
        servicos.append({"nome": nome, "estado": estado})

    agora = datetime.now()

    def _proximo(hh: int, mm: int, dow: int | None = None) -> str:
        c = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dow is None:
            if c <= agora:
                c += timedelta(days=1)
        else:
            while c.weekday() != dow or c <= agora:
                c = (c + timedelta(days=1)).replace(hour=hh, minute=mm)
        return c.isoformat(timespec="minutes")

    relogios = [
        {"nome": "Backup local", "agenda": "03:30 diario", "proximo": _proximo(3, 30)},
        {"nome": "Vigia micro_lab", "agenda": "08:00 diario", "proximo": _proximo(8, 0)},
        {"nome": "Relatorio Telegram", "agenda": "18:00 diario", "proximo": _proximo(18, 0)},
        {"nome": "Retreino ML", "agenda": "domingo 02:00", "proximo": _proximo(2, 0, dow=6)},
    ]
    return jsonify(
        {
            "servicos": servicos,
            "relogios": relogios,
            "agora": agora.isoformat(timespec="seconds"),
        }
    )


@app.route("/api/conexao")
def api_conexao():
    """Status de conectividade com a Binance: REST spot, REST futures, auth e modo."""
    from config.runtime_settings import ALLOW_REAL_TRADING, DRY_RUN

    def _ping(url: str) -> dict:
        t0 = time.time()
        try:
            r = requests.get(url, timeout=5)
            return {"ok": r.status_code == 200, "latencia_ms": round((time.time() - t0) * 1000, 1)}
        except Exception as exc:
            return {"ok": False, "latencia_ms": None, "erro": str(exc)[:80]}

    rest_spot = _ping(f"{BASE_URL}/api/v3/ping")
    rest_fut = _ping(f"{BASE_FAPI}/fapi/v1/ping")

    # I-8 (2026-08-07): havia uma chave de API de 64 caracteres LITERAL nesta
    # lista de placeholders — versionada desde 3c0fc70. Removida daqui; a
    # deteccao passa a ser a de binance_conta.chave_configurada(), que ja
    # reconhece placeholder por padrao de substring e nao precisa enumerar
    # segredo nenhum no fonte.
    # ATENCAO: a chave removida continua no HISTORICO do git. Revogar na Binance.
    import binance_conta

    key_ok = binance_conta.chave_configurada()

    auth_ok = False
    saldo_usdt = None
    auth_err = None

    if key_ok and rest_spot["ok"]:
        # Consolidado em binance_conta (auditoria 2026-07-26): esta rota tinha
        # uma copia da leitura de conta, e era a copia BOA -- separava
        # autenticado/erro/saldo, enquanto risco.py devolvia 0.0 para tudo.
        # Agora as duas usam a mesma implementacao (a informada), com o offset
        # de relogio que faltava no lado do risco.
        import binance_conta

        conta = binance_conta.ler_conta()
        auth_ok = conta["autenticado"]
        auth_err = conta["erro"]
        if conta["ok"]:
            saldo_usdt = conta["saldos"].get("USDT", 0.0)

    # Frescor dos dados por par (última atualização)
    with _lock:
        pares_info = [
            {
                "symbol": s,
                "ultima_atualizacao": estados[s].get("ultima_atualizacao", "—"),
                "preco": estados[s].get("preco", 0),
            }
            for s in PARES_ATIVOS
        ]

    return jsonify(
        {
            "rest_spot": rest_spot,
            "rest_fut": rest_fut,
            "auth": {
                "chave_configurada": key_ok,
                "autenticado": auth_ok,
                "saldo_usdt": saldo_usdt,
                "erro": auth_err,
            },
            "modo": {
                "dry_run": DRY_RUN,
                "allow_real": ALLOW_REAL_TRADING,
                "label": "SIMULAÇÃO" if (DRY_RUN or not ALLOW_REAL_TRADING) else "REAL",
            },
            "pares": pares_info,
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/backtest/<symbol>")
def api_backtest(symbol="BTCUSDT"):
    """Backtest sob demanda — DESLIGADO por padrão (I-12).

    Esta rota era o único artefato do repositório que servia um número
    falsamente POSITIVO por HTTP, 24/7, para qualquer um que abrisse o
    dashboard. Medido em 2026-08-09, antes das correções:

        retorno +2,54% | Sharpe 1,04 | profit factor 1,01 | DSR 0,839
        e o frontend traduzia isso em "ESTRATEGIA PROMISSORA"

    Dois defeitos produziam esse número:

      1. LOOK-AHEAD no mapeamento 1h->4h (`idx4 = i // 4`), que lia um candle
         4h ainda ABERTO em 17.563 de 17.563 barras — 100%.
      2. TAXA de FUTUROS (0,0004) num motor que modela execução SPOT (0,001).

    Com os dois corrigidos, o mesmo motor sobre os mesmos dados devolve:

        retorno -45,83% | Sharpe -0,30 | profit factor 0,75 | DD 48,93%

    48,4 pontos percentuais de diferença. O número honesto é uma perda de quase
    metade do capital.

    A rota continua desligada depois de todo o I-12, mas o MOTIVO mudou. O
    original era `_score_backtest` — uma função de score diferente da de
    produção. Essa função foi ELIMINADA: o motor agora chama
    `regua.score_unificado`, que delega a `score.calcular`. Com a régua
    unificada o número passou de −45,83% para −38,98%.

    Segue desligada por três razões que não são de régua:

      1. −38,98% é o número honesto. Servir por HTTP uma curva que perde 39%
         do capital, num painel cujo público é o operador, não ajuda ninguém.
      2. `data/fng_historico.json` não existe: a medição roda com Fear & Greed
         neutro fixo, sem o veto de sentimento que a produção aplica (I-12g).
      3. O motor modela UMA saída (stop ou alvo). O bot faz parcial de 50%,
         breakeven e trailing — `walk_forward.py` já modela isso (I-12h),
         este motor não.

    Enquanto 2 e 3 não forem fechados aqui, a medição válida é
    `python backtesting/walk_forward.py`.

    Para rodar mesmo assim (uso local, consciente):
        BACKTEST_HTTP=1 python dashboard.py
    """
    import os as _os

    if _os.getenv("BACKTEST_HTTP", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return (
            jsonify(
                {
                    "erro": "MEDICAO INVALIDA — rota desligada (I-12)",
                    "motivo": (
                        "A regua ja foi unificada (score.calcular via regua.py) e o "
                        "numero honesto e -38,98%. A rota segue desligada porque a "
                        "medicao roda sem historico de Fear & Greed (sem o veto de "
                        "sentimento da producao) e modela UMA saida, enquanto o bot "
                        "faz parcial de 50% + breakeven + trailing."
                    ),
                    "historico": {
                        "antes_das_correcoes": {
                            "retorno_total_%": 2.54,
                            "sharpe_ratio": 1.04,
                            "veredito_exibido": "ESTRATEGIA PROMISSORA",
                        },
                        "depois_de_corrigir_lookahead_e_taxa": {
                            "retorno_total_%": -45.83,
                            "sharpe_ratio": -0.30,
                            "profit_factor": 0.75,
                        },
                        "depois_de_unificar_a_regua": {"retorno_total_%": -38.98},
                        "nota": (
                            "48,4 pontos percentuais vinham de duas linhas: idx4 = i//4 "
                            "(look-ahead em 100% das barras) e TAXA=0.0004 (futuros num "
                            "bot spot)."
                        ),
                    },
                    "medicao_valida": "python backtesting/walk_forward.py",
                    "para_rodar_local": "BACKTEST_HTTP=1 python dashboard.py",
                }
            ),
            409,
        )

    from backtesting.motor_ensemble import rodar

    r = rodar(symbol.upper(), "1h", "4h", 1000.0, usar_ml=False)
    if not r or "erro" in r:
        return jsonify({"erro": r.get("erro", "Sem dados") if r else "Sem dados"})
    ops = r.pop("operacoes", [])
    r["operacoes"] = ops[-30:]
    r["aviso"] = (
        "Score do backtest != score de producao (I-12). Numero nao comparavel "
        "com o desempenho ao vivo."
    )
    return jsonify(r)


@app.route("/backtest")
def pagina_backtest():
    return render_template("backtest.html")


@socketio.on("connect")
def on_connect():
    for symbol in PARES_ATIVOS:
        with _lock:
            payload = dict(estados[symbol])
        payload["preco_historico"] = list(preco_historico.get(symbol, []))
        socketio.emit("update", payload)


def run_dashboard():
    database.inicializar()

    threading.Thread(target=loop_snapshots, daemon=True).start()
    threading.Thread(target=iniciar_ws, daemon=True).start()

    print("\n" + "=" * 56)
    print("  DASHBOARD BOTBINANCE v2")
    print(f"  Pares: {', '.join(PARES_ATIVOS)}")
    print(f"  Acesse: http://localhost:{PORT}")
    print()
    print("  APIs:")
    print("    /api/pares          - todos os pares")
    print("    /api/estado/BTCUSDT - estado BTC")
    print("    /api/estado/ETHUSDT - estado ETH")
    print("    /api/score/BTCUSDT  - score detalhado")
    print("    /api/trades         - feed de trades")
    print("    /api/sinais         - historico sinais")
    print("    /api/ml/BTCUSDT     - probabilidade ML")
    print("    /api/risco          - gestao de risco")
    print("=" * 56 + "\n")

    # P0-5: bind local por padrao — dashboard expoe saldo/estrategia e NAO tem
    # auth propria. Para expor na rede: DASHBOARD_BIND=0.0.0.0 no .env (de
    # preferencia atras do Caddy/VPN) e DASHBOARD_TOKEN definido.
    bind = os.getenv("DASHBOARD_BIND", "127.0.0.1")
    _checar_exposicao_rede(bind, os.getenv("DASHBOARD_TOKEN", ""), APP_ENV)
    socketio.run(app, host=bind, port=PORT, debug=False, allow_unsafe_werkzeug=True)


def _checar_exposicao_rede(bind: str, token: str, app_env: str) -> None:
    """Avisa (ou aborta em producao) quando o dashboard expoe a rede sem token.

    Auditoria 2026-07-17: producao + DASHBOARD_BIND != 127.0.0.1 + sem
    DASHBOARD_TOKEN e a combinacao realmente perigosa (qualquer dispositivo na
    rede le saldo/estrategia) — mesmo padrao fail-fast que main.py ja usa para
    `--real` sem credenciais.
    """
    if bind == "127.0.0.1" or token:
        return
    if app_env == "production":
        print(
            "[SEGURANCA] Abortando: DASHBOARD_BIND != 127.0.0.1 sem "
            "DASHBOARD_TOKEN em producao. Defina DASHBOARD_TOKEN no .env ou "
            "volte DASHBOARD_BIND para 127.0.0.1."
        )
        raise SystemExit(1)
    print(
        "[SEGURANCA] AVISO: dashboard exposto na rede SEM DASHBOARD_TOKEN — "
        "qualquer dispositivo na rede ve saldo e estrategia."
    )


# ── Inicialização ──────────────────────────────────────────────

if __name__ == "__main__":
    run_dashboard()
