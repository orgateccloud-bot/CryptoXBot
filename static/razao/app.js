/* Terminal RAZÃO — app.js (D-1: JS fora do inline, mesma origem, zero CDN)
 * Doutrina: dado ausente vira '—', nunca número inventado; esc() antes de
 * qualquer innerHTML com string vinda de API; dashboard é READ-ONLY (E-8e).
 */
'use strict';

// ── helpers ──────────────────────────────────────────────────────
const el  = id => document.getElementById(id);
const fmt = (n, d=2) => n == null ? '—' : Number(n).toLocaleString('pt-BR', {minimumFractionDigits:d, maximumFractionDigits:d});
const fmtK = n => n >= 1e9 ? `$${(n/1e9).toFixed(2)}B` : n >= 1e6 ? `$${(n/1e6).toFixed(2)}M` : `$${(n/1e3).toFixed(1)}K`;
const sinalNum = (n, d=2, pre='$') => (n >= 0 ? '+' : '−') + pre + fmt(Math.abs(n), d);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// Com DASHBOARD_TOKEN setado no servidor, TODO /api/* exige Bearer — o token
// colado na mesa (sessionStorage, morre com a aba) autentica a página inteira.
const _fetchNativo = window.fetch.bind(window);
window.fetch = (url, opts = {}) => {
  const t = sessionStorage.getItem('bxbot-mesa-token');
  if (t && typeof url === 'string' && url.startsWith('/api/')) {
    opts = { ...opts, headers: { ...(opts.headers || {}), 'Authorization': 'Bearer ' + t } };
  }
  return _fetchNativo(url, opts);
};

let parAtivo = localStorage.getItem('bxbot-par') || 'BTCUSDT';
let dadosPares = {};

// ── ABAS (D-1): hash routing + atalhos 1-6 + init preguiçoso ────
const ABAS = ['cockpit', 'analise', 'real', 'quant', 'telemetria', 'expediente', 'mesa'];
let _abaAtiva = 'cockpit';

const INIT_ABA = {
  cockpit: () => { if (precoChart) precoChart.resize(); Object.values(sparks).forEach(s => s.resize()); },
  analise: () => { initEquity(); carregarEquity(); if (_anData) renderAnalytics(_anData); },
  real: () => { carregarGates(); carregarDiario(); },
  quant: () => { carregarQuant(); },
  telemetria: () => {},
  expediente: () => { carregarSistema(); },
  mesa: () => { carregarMesa(); },
};

function ativarAba(aba) {
  if (!ABAS.includes(aba)) aba = 'cockpit';
  _abaAtiva = aba;
  document.querySelectorAll('.aba-secao').forEach(s => {
    // 'block' explícito: '' devolveria a vez à regra CSS .aba-secao{display:none}
    // e escondia a aba ATIVA (bug pego por screenshot em 18/08 — as checagens
    // por textContent/style inline eram cegas ao display COMPUTADO).
    s.style.display = s.id === 'aba-' + aba ? 'block' : 'none';
  });
  document.querySelectorAll('.aba-btn').forEach(b =>
    b.classList.toggle('ativa', b.dataset.aba === aba));
  if (location.hash !== '#/' + aba) history.replaceState(null, '', '#/' + aba);
  (INIT_ABA[aba] || (() => {}))();
}
window.addEventListener('hashchange', () => ativarAba(location.hash.replace('#/', '')));
document.addEventListener('keydown', e => {
  if (e.target.matches('input,textarea,select') || e.ctrlKey || e.altKey || e.metaKey) return;
  const i = parseInt(e.key, 10);
  if (i >= 1 && i <= 7) ativarAba(ABAS[i - 1]);
});
document.querySelectorAll('.aba-btn').forEach(b =>
  b.addEventListener('click', () => ativarAba(b.dataset.aba)));

// ── TEMA ────────────────────────────────────────────────────────
function alternarTema() {
  const claro = document.documentElement.getAttribute('data-theme') === 'light';
  if (claro) {
    document.documentElement.removeAttribute('data-theme');
    localStorage.setItem('bxbot-tema', 'dark');
  } else {
    document.documentElement.setAttribute('data-theme', 'light');
    localStorage.setItem('bxbot-tema', 'light');
  }
  el('exp-tema').textContent = claro ? 'impressão noturna' : 'papel-jornal';
  reentintarCharts();
}

// ── CHARTS base ─────────────────────────────────────────────────
Chart.defaults.font.family = "Consolas, 'Cascadia Mono', monospace";
Chart.defaults.font.size = 10;

function hachura(ctx, cor) {
  const c = document.createElement('canvas'); c.width = 7; c.height = 7;
  const x = c.getContext('2d');
  x.strokeStyle = cor; x.globalAlpha = parseFloat(cssVar('--hatch-a')) || .3; x.lineWidth = 1;
  x.beginPath(); x.moveTo(-2, 9); x.lineTo(9, -2); x.stroke();
  x.beginPath(); x.moveTo(-2, 2); x.lineTo(2, -2); x.stroke();
  x.beginPath(); x.moveTo(2, 9); x.lineTo(9, 2); x.stroke();
  return ctx.createPattern(c, 'repeat');
}
const eixos = () => ({
  x: { ticks: { color: cssVar('--ink-3'), maxTicksLimit: 9 }, grid: { color: cssVar('--rule'), drawTicks: false }, border: { color: cssVar('--rule-2') } },
  y: { ticks: { color: cssVar('--ink-3'), callback: v => '$' + Number(v).toLocaleString('pt-BR') }, grid: { color: cssVar('--rule'), drawTicks: false }, border: { color: cssVar('--rule-2') }, position: 'right' },
});

// preço da sessão (Cockpit, criado no boot)
const pCtx = el('precoChart').getContext('2d');
const precoChart = new Chart(pCtx, {
  type: 'line',
  data: { labels: [], datasets: [{ data: [], borderColor: cssVar('--ink'), borderWidth: 1.5, pointRadius: 0, tension: .25 }]},
  options: { responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false } }, scales: eixos() },
});

// equity (Análise, criado sob demanda — canvas de aba oculta nasce sem tamanho)
let equityChart = null;
function initEquity() {
  if (equityChart) { equityChart.resize(); return; }
  const eqCtx = el('equityChart').getContext('2d');
  equityChart = new Chart(eqCtx, {
    type: 'line',
    data: { labels: [], datasets: [{
      data: [], borderWidth: 2, pointRadius: 0, tension: 0,
      segment: { borderColor: s => (s.p1.parsed.y >= 0 ? cssVar('--gain') : cssVar('--loss')) },
      borderColor: cssVar('--ink'),
      fill: { target: 'origin', above: hachura(eqCtx, cssVar('--gain')), below: hachura(eqCtx, cssVar('--loss')) },
    }]},
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 600 },
      plugins: { legend: { display: false }, tooltip: { displayColors: false,
        callbacks: { label: c => 'acumulado ' + sinalNum(c.parsed.y) } } },
      scales: eixos() },
  });
}

const sparks = {};
function spark(id) {
  if (sparks[id]) return sparks[id];
  const cv = document.getElementById(id);
  if (!cv) return null;
  sparks[id] = new Chart(cv.getContext('2d'), {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: cssVar('--ink-3'), borderWidth: 1, pointRadius: 0, tension: .3 }]},
    options: { responsive: false, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } } }
  });
  return sparks[id];
}

function reentintarCharts() {
  precoChart.options.scales = eixos();
  precoChart.data.datasets[0].borderColor = cssVar('--ink');
  precoChart.update('none');
  if (equityChart) {
    equityChart.options.scales = eixos();
    equityChart.data.datasets[0].fill = {
      target: 'origin',
      above: hachura(equityChart.ctx, cssVar('--gain')),
      below: hachura(equityChart.ctx, cssVar('--loss')),
    };
    equityChart.update('none');
  }
  Object.values(sparks).forEach(s => { s.data.datasets[0].borderColor = cssVar('--ink-3'); s.update('none'); });
  if (_anData) renderAnalytics(_anData);
}

// ── 1 · RESULTADO ───────────────────────────────────────────────
function marcarNumero(elId, texto, positivo) {
  const e = el(elId);
  if (!e) return;
  const mudou = e.textContent !== texto;
  e.textContent = texto;
  if (mudou) {
    e.classList.remove('seca-verde', 'seca-vermelho');
    void e.offsetWidth;
    e.classList.add(positivo ? 'seca-verde' : 'seca-vermelho');
  }
}

async function carregarLucro() {
  try {
    const d = await fetch('/api/lucro').then(r => r.json());
    const tot = d.pnl_total || 0;
    marcarNumero('pnl-hero', sinalNum(tot), tot >= 0);
    el('pnl-hero').style.color = tot > 0 ? 'var(--gain)' : tot < 0 ? 'var(--loss)' : 'var(--ink)';
    const dia = d.pnl_dia || 0;
    el('lucro-dia').textContent = sinalNum(dia);
    el('lucro-dia').className = 'val ' + (dia > 0 ? 'up' : dia < 0 ? 'down' : '');
    el('lucro-n').textContent = d.trades_fechados ?? 0;
    el('lucro-wr').textContent = d.win_rate == null ? '—' : d.win_rate.toFixed(0) + '%';
    if (d.ultimo) {
      const u = d.ultimo;
      el('lucro-ultimo').textContent =
        u.symbol + ' ' + sinalNum(u.pnl_usdt) + ' · ' + (u.barreira || '?') + ' · ' + String(u.quando).slice(5, 16);
      el('lucro-ultimo').className = 'val ' + (u.pnl_usdt >= 0 ? 'up' : 'down');
      el('lucro-ultimo').style.fontSize = '11px';
    }
  } catch(e) {}
}

async function carregarEquity() {
  if (!equityChart) return;
  try {
    const d = await fetch('/api/equity').then(r => r.json());
    const pts = d.pontos || [];
    el('equity-vazio').style.display = pts.length ? 'none' : 'flex';
    equityChart.data.labels = pts.map(p => String(p.t).slice(5, 10));
    equityChart.data.datasets[0].data = pts.map(p => p.cum);
    equityChart.update();
  } catch(e) {}
}

// ── ANÁLISE DO PERÍODO ──────────────────────────────────────────
let _anDias = 0, _anData = null;
const _anCharts = {};

function _anDestroi() {
  for (const k of Object.keys(_anCharts)) { _anCharts[k].destroy(); delete _anCharts[k]; }
}

function renderAnalytics(d) {
  _anData = d;
  if (_abaAtiva !== 'analise') return; // canvas oculto não tem tamanho; renderiza ao ativar
  const vazio = !d || !d.ribbon || d.ribbon.n_trades === 0;
  el('analise-vazia').style.display = vazio ? '' : 'none';
  el('analise-corpo').style.display = vazio ? 'none' : '';
  if (vazio) { _anDestroi(); return; }

  const r = d.ribbon, ref = d.referencias || {};
  const pfPiso = ref.pf_piso_etapa2 || 1.3;
  const pfOk = r.profit_factor != null && r.profit_factor > pfPiso;
  el('analise-ribbon').innerHTML =
    `<span>TRADES <b>${r.n_trades}</b></span>` +
    `<span>PNL <b class="${r.pnl_total >= 0 ? 'up' : 'down'}">${sinalNum(r.pnl_total)}</b></span>` +
    `<span>WIN RATE <b>${r.win_rate == null ? '—' : r.win_rate.toFixed(0) + '%'}</b></span>` +
    `<span>PF <b class="${r.profit_factor == null ? '' : (pfOk ? 'up' : 'down')}">${r.profit_factor == null ? '—' : r.profit_factor.toFixed(2)}</b> <span style="opacity:.7">piso E2 ${pfPiso.toFixed(1)}</span></span>` +
    `<span>DIAS C/ TRADE <b>${r.dias_com_trade}</b></span>` +
    `<span>DIAS POSITIVOS <b>${r.pct_dias_positivos == null ? '—' : r.pct_dias_positivos.toFixed(0) + '%'}</b></span>` +
    (r.melhor_dia ? `<span>MELHOR <b class="up">${sinalNum(r.melhor_dia.pnl)}</b> <span style="opacity:.7">${esc(r.melhor_dia.dia.slice(5))}</span></span>` : '') +
    (r.pior_dia ? `<span>PIOR <b class="${r.pior_dia.pnl >= 0 ? 'up' : 'down'}">${sinalNum(r.pior_dia.pnl)}</b> <span style="opacity:.7">${esc(r.pior_dia.dia.slice(5))}</span></span>` : '');

  _anDestroi();
  const GAIN = cssVar('--gain'), LOSS = cssVar('--loss'), INK = cssVar('--ink'), INK3 = cssVar('--ink-3');
  const ex = { ticks: { color: INK3, maxTicksLimit: 8 }, grid: { display: false }, border: { color: cssVar('--rule-2') } };
  const ey = { ticks: { color: INK3 }, grid: { color: cssVar('--rule'), drawTicks: false }, border: { display: false } };

  _anCharts.diario = new Chart(el('chartDiario').getContext('2d'), {
    data: { labels: d.diario.map(x => x.dia.slice(5)), datasets: [
      { type: 'bar', data: d.diario.map(x => x.pnl), backgroundColor: d.diario.map(x => x.pnl >= 0 ? GAIN : LOSS), yAxisID: 'y', order: 2 },
      { type: 'line', data: d.diario.map(x => x.cum), borderColor: INK, borderWidth: 2, pointRadius: 0, tension: 0, yAxisID: 'y2', order: 1 },
    ]},
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: {
        title: c => d.diario[c[0].dataIndex].dia,
        label: c => { const x = d.diario[c.dataIndex]; return [
          'dia ' + sinalNum(x.pnl), x.n + ' trade(s) · ' + x.wins + 'W/' + (x.n - x.wins) + 'L',
          'capital acumulado ' + sinalNum(x.cum)]; },
      }}},
      scales: { x: ex, y: ey, y2: { position: 'right', ticks: { color: INK3 }, grid: { display: false }, border: { display: false } } } },
  });

  const wrRef = ref.wr_equilibrio_barreira || 33.3;
  _anCharts.wr = new Chart(el('chartWinRate').getContext('2d'), {
    type: 'line',
    data: { labels: d.win_movel.map((x, i) => i + 1), datasets: [
      { data: d.win_movel.map(x => x.wr), borderColor: INK, borderWidth: 2, pointRadius: 3,
        pointBackgroundColor: d.win_movel.map(x => x.pnl >= 0 ? GAIN : LOSS), tension: 0 },
      { data: d.win_movel.map(() => wrRef), borderColor: LOSS, borderWidth: 1, borderDash: [4, 4], pointRadius: 0 },
    ]},
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: {
        title: c => 'trade #' + (c[0].dataIndex + 1),
        label: c => { const x = d.win_movel[c.dataIndex]; return [
          x.symbol + ' ' + sinalNum(x.pnl), 'win rate móvel ' + x.wr.toFixed(1) + '%', String(x.t).slice(0, 16)]; },
      }}},
      scales: { x: ex, y: { ...ey, min: 0, max: 100, ticks: { color: INK3, callback: v => v + '%' } } } },
  });

  _anCharts.hora = new Chart(el('chartHora').getContext('2d'), {
    type: 'bar',
    data: { labels: d.por_hora.map(x => String(x.hora).padStart(2, '0')),
      datasets: [{ data: d.por_hora.map(x => x.pnl), backgroundColor: d.por_hora.map(x => x.pnl >= 0 ? GAIN : LOSS) }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: {
        label: c => { const x = d.por_hora[c.dataIndex]; return sinalNum(x.pnl) + ' em ' + x.n + ' trade(s)'; } } } },
      scales: { x: ex, y: ey } },
  });

  _anCharts.par = new Chart(el('chartPar').getContext('2d'), {
    type: 'bar',
    data: { labels: d.por_par.map(x => x.symbol.replace('USDT', '')),
      datasets: [{ data: d.por_par.map(x => x.pnl), backgroundColor: d.por_par.map(x => x.pnl >= 0 ? GAIN : LOSS) }] },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { displayColors: false, callbacks: {
        label: c => { const x = d.por_par[c.dataIndex]; return sinalNum(x.pnl) + ' · ' + x.n + ' trade(s) · WR ' + (x.n ? Math.round(x.wins / x.n * 100) : 0) + '%'; } } } },
      scales: { x: ey, y: ex } },
  });
}

async function carregarAnalytics() {
  try {
    const d = await fetch('/api/analytics' + (_anDias ? '?dias=' + _anDias : '')).then(r => r.json());
    renderAnalytics(d);
  } catch (e) {}
}

document.querySelectorAll('#analise-presets .preset').forEach(b => {
  b.addEventListener('click', () => {
    _anDias = parseInt(b.dataset.dias, 10) || 0;
    document.querySelectorAll('#analise-presets .preset').forEach(x => x.classList.toggle('ativo', x === b));
    carregarAnalytics();
  });
});

// ── 2 · O BOT AGORA ─────────────────────────────────────────────
let _cicloTs = null;
let _feedTs = Date.now();

function varrerPipeline() {
  if (_abaAtiva !== 'cockpit') return;
  const estacoes = [...document.querySelectorAll('.estacao')];
  estacoes.forEach(e => e.classList.remove('ativa'));
  estacoes.forEach((e, i) => { setTimeout(() => e.classList.add('ativa'), i * 130); });
  setTimeout(() => estacoes.forEach(e => e.classList.remove('ativa')), estacoes.length * 130 + 900);
}

let _atvTopo = null;
async function carregarAtividade() {
  try {
    const rows = await fetch('/api/atividade').then(r => r.json());
    if (!rows.length) return;
    const corpo = el('diario-corpo');
    const novoTopo = rows[0].quando + rows[0].symbol;
    const primeira = _atvTopo && novoTopo !== _atvTopo;
    corpo.innerHTML = rows.map((r, i) => {
      const dec = esc((r.decisao || 'AGUARDAR').toUpperCase());
      const ml = r.ml == null ? '—' : (r.ml * 100).toFixed(0) + '%';
      const extra = esc(r.bloqueios ? r.bloqueios : (r.regime || ''));
      const cls = (i === 0 && primeira) ? ' class="assenta"' : '';
      return `<tr${cls}>` +
        `<td>${esc(String(r.quando).slice(11, 19))}</td>` +
        `<td class="forte">${esc(r.symbol)}</td>` +
        `<td class="dir-right">$${fmt(r.preco)}</td>` +
        `<td class="dir-right forte">${r.score == null ? '—' : esc(r.score)}</td>` +
        `<td>${extra}</td>` +
        `<td class="dir-right">${ml}</td>` +
        `<td class="dir-right dec-${dec}">${dec}</td>` +
      `</tr>`;
    }).join('');
    _atvTopo = novoTopo;
    _cicloTs = rows[0].quando;
    el('bot-ciclo-nota').textContent = 'ÚLTIMO CICLO ' + String(_cicloTs).slice(11, 19);
  } catch(e) {}
}

setInterval(() => {
  const seg = Math.round((Date.now() - _feedTs) / 1000);
  const bat = el('batimento');
  if (seg > 90) {
    bat.classList.add('parado');
    el('batimento-txt').textContent = `FEED PARADO HÁ ${seg}s`;
  } else {
    bat.classList.remove('parado');
    el('batimento-txt').textContent = _cicloTs
      ? 'ESCREVENDO — CICLO ' + String(_cicloTs).slice(11, 16)
      : 'ESCREVENDO…';
  }
}, 5000);

// ── 3 · MERCADO ─────────────────────────────────────────────────
function renderParesMini(pares) {
  const row = el('mercado-pares');
  if (!row.children.length) {
    row.innerHTML = pares.map(p => {
      const sym = esc(String(p.symbol).replace('USDT', ''));
      return `<div class="par-col" data-symbol="${esc(p.symbol)}">` +
        `<div class="sym">${sym} / USDT</div>` +
        `<div class="preco num" id="pp-${esc(p.symbol)}">—</div>` +
        `<div class="linha2"><span id="pv-${esc(p.symbol)}">—</span>` +
        `<span style="color:var(--ink-3)" id="pt-${esc(p.symbol)}">—</span>` +
        `<span style="color:var(--ink-3)" id="psc-${esc(p.symbol)}">—</span></div>` +
        `<canvas id="spark-${esc(p.symbol)}" width="380" height="34"></canvas>` +
      `</div>`;
    }).join('');
    row.querySelectorAll('.par-col').forEach(d =>
      d.addEventListener('click', () => trocarPar(d.dataset.symbol)));
  }
  pares.forEach(p => {
    const preco = el('pp-' + p.symbol), varr = el('pv-' + p.symbol);
    if (!preco) return;
    preco.textContent = '$' + fmt(p.preco);
    const vp = p.variacao_24h || 0;
    varr.innerHTML = (vp >= 0 ? '&#9650; ' : '&#9660; ') + fmt(Math.abs(vp)) + '%';
    varr.className = vp > 0 ? 'up' : vp < 0 ? 'down' : '';
    el('pt-' + p.symbol).textContent = (p.tend_4h || '—') + ' · RSI ' + fmt(p.rsi, 0);
    el('psc-' + p.symbol).textContent = 'SCORE ' + (p.score || 0) + ' · ' + (p.sinal || '—');
  });
  document.querySelectorAll('.par-col').forEach(d =>
    d.classList.toggle('ativo', d.dataset.symbol === parAtivo));
}

function trocarPar(symbol) {
  parAtivo = symbol;
  localStorage.setItem('bxbot-par', symbol); // D-6: par global persistente
  document.querySelectorAll('.par-col').forEach(d =>
    d.classList.toggle('ativo', d.dataset.symbol === symbol));
  const d = dadosPares[symbol];
  if (d) aplicarDados(d);
  else fetch('/api/estado/' + symbol).then(r => r.json()).then(x => { dadosPares[x.symbol] = x; aplicarDados(x); });
}

const PESOS = { regime:25, mtf:20, ml:15, ema:10, fear_greed:10, rsi:8, vwap:5, volume:4, atr:3 };
const NOMES = { regime:'REGIME', mtf:'MTF 4H', ml:'ML', ema:'EMA', fear_greed:'F&G', rsi:'RSI', vwap:'VWAP', volume:'VOL', atr:'ATR' };
function renderBreakdown(detail) {
  const wrap = el('score-breakdown');
  wrap.innerHTML = Object.entries(PESOS).map(([k, peso]) => {
    const val = detail && detail[k] != null ? detail[k] : 0;
    const contrib = (val * peso / 100).toFixed(1);
    const cor = val >= 70 ? 'var(--gain)' : val >= 40 ? 'var(--warn)' : 'var(--loss)';
    return `<div class="breakdown-linha">` +
      `<span class="k">${NOMES[k]}</span>` +
      `<div class="breakdown-barra"><div class="breakdown-fill" style="width:${Math.min(val,100)}%;color:${cor}"></div></div>` +
      `<span class="v" style="color:${cor}">${contrib}</span>` +
    `</div>`;
  }).join('');
}

function aplicarDados(d) {
  if (!d) return;

  el('pl-dados').textContent = d.ultima_atualizacao || '—';
  el('pl-ind').textContent = fmt(d.rsi, 1);
  const mlP = d.ml_ensemble ?? d.ml_prob;
  el('pl-ml').textContent = mlP != null ? (mlP * 100).toFixed(0) + '%' : '—';
  el('pl-score').textContent = d.score || 0;
  const dec = d.score_decisao || d.sinal || 'AGUARDAR';
  el('pl-dec').textContent = dec === 'OPERAR_CHEIO' ? 'OPERAR' : dec === 'OPERAR_REDUZIDO' ? 'REDUZIDO' : dec;
  el('pl-dec').style.color = dec.startsWith('OPERAR') || dec === 'COMPRA' ? 'var(--gain)' : 'var(--ink-3)';
  el('pl-dec-sub').textContent = d.symbol || '';

  el('chart-title').textContent = 'HISTÓRICO DA SESSÃO :: ' + d.symbol;
  el('max24').textContent = '$' + fmt(d.maximo_24h);
  el('min24').textContent = '$' + fmt(d.minimo_24h);
  el('vol24').textContent = fmt(d.volume_24h, 0) + ' ' + d.symbol.replace('USDT', '');
  const fr = d.funding;
  el('funding').textContent = fr != null ? (fr >= 0 ? '+' : '−') + fmt(Math.abs(fr), 4) + '%' : '—';

  const sb = el('sinal-badge');
  sb.textContent = (d.sinal || 'aguardar').toLowerCase();
  sb.className = 'parecer-sinal ' + (d.sinal === 'COMPRA' ? 'compra' : d.sinal === 'VENDA' ? 'venda' : '');
  el('stop-val').textContent   = d.stop_loss   ? '$' + fmt(d.stop_loss)   : '—';
  el('stop-val').className    = 'val' + (d.stop_loss ? ' down' : '');
  el('target-val').textContent = d.take_profit ? '$' + fmt(d.take_profit) : '—';
  el('target-val').className  = 'val' + (d.take_profit ? ' up' : '');
  el('tend-val').textContent = (d.tendencia || '—') + ' / ' + (d.tend_4h || '—');
  el('regime-badge').textContent = (d.regime || '—').replace('TENDENCIA_', '').replace('_', ' ');
  el('fg-badge').textContent = (d.fear_greed || 0) + ' — ' + (d.fear_greed_pt || '—');
  // D-6: F&G real também no masthead (vem do estado do worker/estratégia)
  if (d.symbol === 'BTCUSDT' && d.fear_greed) {
    el('hdr-fg').textContent = 'F&G ' + d.fear_greed;
  }
  if (d.params && Object.keys(d.params).length) {
    el('params-strip').textContent =
      'stop ' + (d.params.stop_pct * 100).toFixed(1) + '% · alvo ' + (d.params.target_pct * 100).toFixed(1) +
      '% · RSI ' + d.params.rsi_min + '–' + d.params.rsi_max;
  }

  const score = d.score || 0;
  el('gauge-num').textContent = score;
  el('gauge-num').style.color = score >= 70 ? 'var(--gain)' : score >= 60 ? 'var(--warn)' : 'var(--ink)';
  const sd = el('score-decisao');
  sd.textContent = dec;
  sd.style.color = dec === 'OPERAR_CHEIO' ? 'var(--gain)' : dec === 'OPERAR_REDUZIDO' ? 'var(--warn)' : 'var(--ink-3)';
  renderBreakdown(d.score_detail);

  el('ema20').textContent = '$' + fmt(d.ema20);
  el('ema50').textContent = '$' + fmt(d.ema50);
  el('rsi').textContent = fmt(d.rsi, 1) + (d.rsi > 70 ? ' · sobrecomprado' : d.rsi < 30 ? ' · sobrevendido' : '');
  el('atr').textContent = fmt(d.atr);
  el('ml-prob').textContent = mlP != null ? (mlP * 100).toFixed(1) + '%' : '—';
  el('ml-prob').style.color = mlP >= 0.6 ? 'var(--gain)' : mlP >= 0.45 ? 'var(--warn)' : 'var(--loss)';
  el('ml-xgb').textContent  = d.ml_xgb  != null ? (d.ml_xgb * 100).toFixed(0) + '%' : '—';
  el('ml-lstm').textContent = d.ml_lstm != null ? (d.ml_lstm * 100).toFixed(0) + '%' : '—';
  el('ml-conf').textContent = d.ml_confianca || '—';

  const cvd = d.cvd || 0;
  el('cvd-valor').textContent = sinalNum(cvd, 3, '') + ' BTC';
  el('cvd-valor').style.color = cvd >= 0 ? 'var(--gain)' : 'var(--loss)';
  el('cvd-c').textContent = fmt(d.compras || 0, 2);
  el('cvd-v').textContent = fmt(d.vendas || 0, 2);
  const lc = d.liq_compra || 0, lv = d.liq_venda || 0, tot = lc + lv || 1;
  el('pressao').textContent = (d.pressao_ob || '—') + ' · ' + ((lc / tot) * 100).toFixed(0) + '/' + ((lv / tot) * 100).toFixed(0);
  el('pressao').style.color = d.pressao_ob === 'COMPRA' ? 'var(--gain)' : d.pressao_ob === 'VENDA' ? 'var(--loss)' : 'var(--ink-2)';

  if (d.preco_historico && d.preco_historico.length) {
    precoChart.data.labels = d.preco_historico.map(p => p.t);
    precoChart.data.datasets[0].data = d.preco_historico.map(p => p.v);
    precoChart.update('none');
    const s = spark('spark-' + d.symbol);
    if (s) {
      s.data.labels = d.preco_historico.map(p => p.t);
      s.data.datasets[0].data = d.preco_historico.map(p => p.v);
      s.update('none');
    }
  }

  el('update-lbl').textContent = 'ATUALIZADO ' + (d.ultima_atualizacao || '—');
  el('footer-update').textContent = 'edição contínua · ' + (d.ultima_atualizacao || '—');
}

async function carregarPares() {
  try {
    const pares = await fetch('/api/pares').then(r => r.json());
    renderParesMini(pares);
  } catch(e) {}
}

async function carregarSparks() {
  for (const s of ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']) {
    try {
      const d = await fetch('/api/estado/' + s).then(r => r.json());
      dadosPares[s] = d;
      const sp = spark('spark-' + s);
      if (sp && d.preco_historico) {
        sp.data.labels = d.preco_historico.map(p => p.t);
        sp.data.datasets[0].data = d.preco_historico.map(p => p.v);
        sp.update('none');
      }
    } catch(e) {}
  }
}

// ── TELEMETRIA: fita de pregão (D-2, honesta) ───────────────────
// Só o fluxo REAL: @aggTrade de BTC filtrado ≥0,5 (o que o backend emite).
// tx/min e vol/min são MEDIDOS numa janela móvel de 60s — sem piso, sem
// gerador sintético. A limitação está declarada no rótulo da própria UI.
let _fitaPausada = false;
const _fitaFila = [];
const _fitaJanela = []; // [{ts, valor}] p/ tx-min e vol-min medidos
function alternarPausaFita() {
  _fitaPausada = !_fitaPausada;
  const btn = el('btn-fita-pausa');
  if (_fitaPausada) {
    btn.textContent = '▶ RETOMAR';
  } else {
    while (_fitaFila.length) _fitaChip(_fitaFila.shift());
    btn.textContent = '⏸ PAUSAR';
  }
}
function _fitaChip(t) {
  const fita = el('fita-chips');
  if (!fita) return;
  const compra = t.direcao === 'COMPRA';
  const chip = document.createElement('button');
  chip.className = 'fita-chip' + (t.baleia ? ' baleia' : '');
  chip.setAttribute('aria-label', 'Focar BTCUSDT no cockpit');
  chip.innerHTML =
    `<span style="color:${compra ? 'var(--gain)' : 'var(--loss)'}">${compra ? '▲' : '▼'}</span> ` +
    `<b>$${fmt(t.preco)}</b> <span style="color:var(--ink-3)">${esc(t.volume)} · ${esc(t.hora)}</span>`;
  chip.addEventListener('click', () => { trocarPar('BTCUSDT'); ativarAba('cockpit'); });
  fita.insertBefore(chip, fita.firstChild);
  while (fita.children.length > 30) fita.removeChild(fita.lastChild);
}
function fitaRegistrar(t) {
  const agora = Date.now();
  _fitaJanela.push({ ts: agora, valor: t.valor_usdt || 0 });
  while (_fitaJanela.length && agora - _fitaJanela[0].ts > 60000) _fitaJanela.shift();
  el('fita-txmin').textContent = _fitaJanela.length;
  el('fita-volmin').textContent = fmtK(_fitaJanela.reduce((s, x) => s + x.valor, 0));
  if (_fitaPausada) {
    _fitaFila.push(t);
    el('btn-fita-pausa').textContent = '▶ RETOMAR (' + _fitaFila.length + ')';
    return;
  }
  _fitaChip(t);
}

// ── pregão (tabela) + ocorrências + incidentes ──────────────────
let primeiroTrade = true;
function addTrade(t) {
  const list = el('trades-list');
  if (primeiroTrade) { list.innerHTML = ''; primeiroTrade = false; }
  const compra = t.direcao === 'COMPRA';
  const tr = document.createElement('tr');
  tr.innerHTML =
    `<td>${esc(t.hora)}</td>` +
    `<td class="${compra ? 'dec-COMPRA' : ''}" style="${compra ? '' : 'color:var(--loss);font-weight:700'}">${t.baleia ? '◉ ' : ''}${esc(t.direcao)}</td>` +
    `<td class="dir-right forte">$${fmt(t.preco)}</td>` +
    `<td class="dir-right">${esc(t.volume)}</td>` +
    `<td class="dir-right" style="color:var(--ink-3)">${fmtK(t.valor_usdt)}</td>`;
  list.insertBefore(tr, list.firstChild);
  while (list.children.length > 60) list.removeChild(list.lastChild);
}

let _evPausado = false;
const _evFila = [];
function alternarPausaEventos() {
  _evPausado = !_evPausado;
  const btn = el('btn-ev-pausa');
  if (_evPausado) {
    btn.textContent = '▶ RETOMAR';
  } else {
    while (_evFila.length) addEvento(_evFila.shift());
    btn.textContent = '⏸ PAUSAR';
  }
}

let primeiroEvento = true, totalEventos = 0;
function addEvento(ev) {
  if (_evPausado) {
    _evFila.push(ev);
    el('btn-ev-pausa').textContent = '▶ RETOMAR (' + _evFila.length + ')';
    return;
  }
  const list = el('eventos-list');
  if (primeiroEvento) { list.innerHTML = ''; primeiroEvento = false; }
  totalEventos++;
  el('ev-count').textContent = '· ' + totalEventos;
  const tr = document.createElement('tr');
  tr.innerHTML =
    `<td style="width:70px">${esc(ev.ts)}</td>` +
    `<td style="width:80px;color:var(--ink-3)">${esc(ev.tipo)}</td>` +
    `<td><span class="forte" style="color:var(--ink)">${esc(ev.titulo)}</span>` +
    (ev.mensagem ? ` <span style="color:var(--ink-3)">${esc(ev.mensagem)}</span>` : '') + `</td>`;
  list.insertBefore(tr, list.firstChild);
  while (list.children.length > 80) list.removeChild(list.lastChild);
}
async function carregarEventos() {
  try {
    const evs = await fetch('/api/eventos').then(r => r.json());
    if (evs.length) evs.reverse().forEach(addEvento);
  } catch(e) {}
}

async function carregarIncidentes() {
  try {
    const d = await fetch('/api/bot_events?limite=6').then(r => r.json());
    const evs = d.eventos || [];
    if (!evs.length) return;
    el('incidentes-list').innerHTML = evs.map((e, i) => {
      const sev = esc(e.severity || 'INFO');
      const cor = sev === 'CRITICAL' ? 'var(--loss)' : sev === 'WARNING' ? 'var(--warn)' : 'var(--ink-3)';
      const detalhe = esc(typeof e.data === 'string' ? e.data : JSON.stringify(e.data || '')) || '—';
      return `<tr style="cursor:pointer" data-inc="${i}">` +
        `<td style="width:110px">${esc(String(e.timestamp || '').slice(5, 16))}</td>` +
        `<td style="width:80px;color:${cor}">${sev}</td>` +
        `<td class="forte">${esc(e.event_type || '')}</td>` +
        `<td>${esc(e.message || '')}</td>` +
      `</tr>` +
      `<tr id="inc-det-${i}" style="display:none"><td colspan="4" style="font-size:10px;color:var(--ink-3);word-break:break-all">${detalhe}</td></tr>`;
    }).join('');
    el('incidentes-list').querySelectorAll('tr[data-inc]').forEach(tr =>
      tr.addEventListener('click', () => {
        const x = el('inc-det-' + tr.dataset.inc);
        x.style.display = x.style.display === 'none' ? '' : 'none';
      }));
  } catch(e) {}
}

// ── risco ───────────────────────────────────────────────────────
async function carregarRisco() {
  try {
    const d = await fetch('/api/risco').then(r => r.json());
    el('r-usdt').textContent = '$' + fmt(d.saldo_usdt);
    const dd = d['drawdown_dia_%'] || 0;
    el('r-dd').textContent = dd.toFixed(2) + '%';
    el('r-dd').className = 'v ' + (dd <= -3 ? 'down' : '');
    el('r-kelly').textContent = (d['kelly_%'] || 0) + '%';
    el('r-vol').textContent = (d['volatilidade_%'] || 0).toFixed(2) + '%';
    const bloq = d.bloqueado;
    el('r-status').textContent = bloq ? 'BLOQUEADO' : 'OPERANDO';
    el('r-status').className = 'v ' + (bloq ? 'down' : 'up');
    el('pl-risco').textContent = bloq ? 'BLOQ' : 'OK';
    el('pl-risco').style.color = bloq ? 'var(--loss)' : 'var(--ink)';
    el('pl-risco-sub').textContent = 'kelly ' + (d['kelly_%'] || 0) + '% · dd ' + dd.toFixed(1) + '%';
  } catch(e) {}
}

// ── 4 · CAMINHO AO REAL ─────────────────────────────────────────
const CARIMBO_GATE = {
  REPROVADA: ['vermelho', 'REPROVADA'],
  TRANCADO: ['', 'TRANCADO'],
  EM_COLETA: ['ambar', 'EM COLETA'],
  MEDIDA: ['verde', 'MEDIDA'],
  AGUARDA_ETAPA1: ['', 'AGUARDA E1'],
  AGUARDA_ETAPA2: ['', 'AGUARDA E2'],
  BLOQUEADA: ['', 'BLOQUEADA'],
  ARMADA: ['vermelho', '⚠ ARMADA'],
  FAIL: ['vermelho', 'FAIL'],
  SOBREVIVE: ['verde', 'SOBREVIVE'],
  PASS: ['verde', 'PASS'],
  AGENDADA: ['', 'AGENDADA'],
  NAO_MEDIDA: ['', 'PRÉ-REG.'],
};
async function carregarGates() {
  try {
    const d = await fetch('/api/gates').then(r => r.json());
    el('gates-row').innerHTML = (d.etapas || []).map(e => {
      const [cor, rot] = CARIMBO_GATE[e.status] || ['', esc(e.status)];
      let flags = '';
      if (e.flags) {
        flags = `<div class="flags-ignicao">` +
          `<span class="${e.flags.dry_run_false ? 'flag-on' : 'flag-off'}">${e.flags.dry_run_false ? '●' : '○'} DRY_RUN=false</span>` +
          `<span class="${e.flags.allow_real_true ? 'flag-on' : 'flag-off'}">${e.flags.allow_real_true ? '●' : '○'} ALLOW_REAL</span>` +
          `<span class="${e.flags.env_production ? 'flag-on' : 'flag-off'}">${e.flags.env_production ? '●' : '○'} ENV=prod</span>` +
        `</div>`;
      }
      return `<div class="gate">` +
        `<div class="quando">${esc(e.data || '·')}</div>` +
        `<div class="nome">${esc(e.nome)}</div>` +
        `<span class="carimbo reto ${cor}" style="font-size:9px;padding:2px 8px">${rot}</span>` +
        (e.detalhe ? `<div class="det">${esc(e.detalhe)}</div>` : '') + flags +
      `</div>`;
    }).join('');
    el('funil-row').innerHTML = (d.frentes || []).map(f => {
      const [cor, rot] = CARIMBO_GATE[f.status] || ['', esc(f.status)];
      return `<div>` +
        `<div class="funil-item">` +
          `<span class="fnome">${esc(f.nome)}</span>` +
          `<span class="fpontos"></span>` +
          `<span class="carimbo reto ${cor}" style="font-size:8px;padding:1px 7px">${rot}</span>` +
          `<span class="fdata">${esc(f.quando || '·')}</span>` +
        `</div>` +
        (f.detalhe ? `<div class="funil-det">${esc(f.detalhe)}</div>` : '') +
      `</div>`;
    }).join('');
  } catch(e) {}
}

// ── QUANT LAB (D-3): artefatos reais da régua científica ────────
async function carregarQuant() {
  try {
    const d = await fetch('/api/quant').then(r => r.json());

    el('q-retreinos').innerHTML = (d.retreinos && d.retreinos.length)
      ? d.retreinos.map(r =>
          `<tr><td>${esc(String(r.quando).slice(0, 16))}</td>` +
          `<td class="forte">${esc(r.symbol)}</td>` +
          `<td>${esc(r.modelo)}</td>` +
          `<td class="dir-right">${r.auc == null ? '—' : Number(r.auc).toFixed(4)}</td>` +
          `<td class="dir-right">${r.cv_auc_mean == null ? '—' : Number(r.cv_auc_mean).toFixed(4)}</td>` +
          `<td class="dir-right" style="color:var(--ink-3)">${r.cv_auc_std == null ? '—' : '±' + Number(r.cv_auc_std).toFixed(4)}</td></tr>`
        ).join('')
      : '<tr><td colspan="6" style="font-style:italic;color:var(--ink-3)">Sem retreinos registrados neste banco.</td></tr>';

    el('q-drift').innerHTML = (d.drift && d.drift.length)
      ? d.drift.map(x =>
          `<tr><td style="width:130px">${esc(String(x.quando).slice(5, 16))}</td>` +
          `<td style="width:80px" class="forte">${esc(x.symbol || '')}</td>` +
          `<td style="color:var(--warn)">${esc(x.mensagem)}</td></tr>`
        ).join('')
      : '<tr><td colspan="3" style="font-style:italic;color:var(--ink-3)">Nenhum alerta de drift — os retreinos seguem dentro do histórico.</td></tr>';

    const labRot = { momo: 'MOMO — rotação BTC/ETH/SOL (8 trials)', volt: 'VOLT — vol-targeting spot (6 trials)' };
    el('q-labs').innerHTML = Object.entries(labRot).map(([k, rot]) => {
      const lab = d.labs && d.labs[k];
      if (!lab) return `<div class="q-lab"><div class="secao-nota">${esc(rot)}</div><div class="analise-vazia">— sem medição gravada —</div></div>`;
      const linhas = (lab.combos || []).map(c => {
        if (k === 'momo') {
          return `<tr><td>k=${esc(c.k)} ${c.filtro_tsmom ? 'TSMOM' : 'puro'}</td>` +
            `<td class="dir-right ${c.retorno_aa >= 0 ? 'up' : 'down'}">${(c.retorno_aa * 100).toFixed(1)}% a.a.</td>` +
            `<td class="dir-right">${c.sharpe.toFixed(2)}</td>` +
            `<td class="dir-right" style="color:var(--ink-3)">bh ${c.sharpe_bh_btc.toFixed(2)}</td>` +
            `<td class="dir-right">${(c.max_dd * 100).toFixed(0)}%</td>` +
            `<td class="dir-right">${c.sobrevive ? '<span style="color:var(--gain)">SOBREVIVE</span>' : '<span style="color:var(--loss)">reprova</span>'}</td></tr>`;
        }
        return `<tr><td>w=${esc(c.w)} σ=${(c.sigma_alvo * 100).toFixed(0)}%</td>` +
          `<td class="dir-right">${c.ativos_aprovados}/3 ativos</td>` +
          `<td class="dir-right">ΔSh ${c.delta_sharpe_medio >= 0 ? '+' : ''}${c.delta_sharpe_medio.toFixed(2)}</td>` +
          `<td class="dir-right" colspan="2"></td>` +
          `<td class="dir-right">${c.sobrevive ? '<span style="color:var(--gain)">SOBREVIVE</span>' : '<span style="color:var(--loss)">reprova</span>'}</td></tr>`;
      }).join('');
      return `<div class="q-lab">` +
        `<div class="secao-nota" style="margin:12px 0 4px">${esc(rot)} · medida ${esc(String(lab.quando || '').slice(0, 16))} · ` +
        `<span style="color:${lab.veredito === 'FAIL' ? 'var(--loss)' : 'var(--gain)'}">${esc(lab.veredito || '—')}</span></div>` +
        `<table class="diario" style="margin-top:0"><tbody>${linhas}</tbody></table></div>`;
    }).join('');

    const g = d.exec_gauges;
    el('q-exec').innerHTML = g
      ? Object.entries(g).map(([k, v]) =>
          `<div class="micro-linha"><span class="k">${esc(k)}</span><span class="v">${Number(v).toFixed(3)}</span></div>`
        ).join('')
      : '<div class="micro-linha"><span class="k">gauges exec_*</span><span class="v" style="color:var(--ink-3)">sem dado — worker fora do ar ou nenhuma execução medida; nada inventado</span></div>';

    el('q-arquivo').innerHTML = (d.arquivo && d.arquivo.length)
      ? d.arquivo.map(a =>
          `<div class="micro-linha"><span class="k">${esc(a.arquivo)}</span>` +
          `<span class="v" style="color:${String(a.veredito).includes('FAIL') ? 'var(--loss)' : String(a.veredito).includes('PASS') || String(a.veredito).includes('SOBREVIVE') ? 'var(--gain)' : 'var(--ink)'}">${esc(a.veredito)}</span></div>`
        ).join('')
      : '<div class="micro-linha"><span class="k">—</span><span class="v">sem vereditos gravados</span></div>';
  } catch(e) {}
}

// ── 7 · MESA DE OPERAÇÕES ───────────────────────────────────────
// A única ESCRITA do dashboard — e é comando auditado, papel somente.
// Confirmação dupla: 1º clique arma (5s), 2º envia. Token em sessionStorage
// (morre com a aba; nunca localStorage).
const _mesaTokenInput = el('mesa-token');
if (_mesaTokenInput && sessionStorage.getItem('bxbot-mesa-token')) {
  _mesaTokenInput.value = sessionStorage.getItem('bxbot-mesa-token');
}
if (_mesaTokenInput) {
  _mesaTokenInput.addEventListener('change', () =>
    sessionStorage.setItem('bxbot-mesa-token', _mesaTokenInput.value.trim()));
}

function _mesaAviso(texto, erro) {
  const av = el('mesa-aviso');
  av.style.display = texto ? '' : 'none';
  av.textContent = texto || '';
  av.style.borderColor = erro ? 'var(--loss)' : 'var(--warn)';
  av.style.color = erro ? 'var(--loss)' : 'var(--warn)';
}

let _mesaArmado = null, _mesaDesarma = null;
async function _mesaEnviar(comando) {
  const token = (_mesaTokenInput.value || '').trim();
  if (!token) { _mesaAviso('Cole o DASHBOARD_TOKEN para armar a mesa — sem token ela não existe (fail-closed).', true); return; }
  const params = comando === 'fechar_posicao_paper' ? { symbol: el('mesa-par').value } : {};
  try {
    const r = await fetch('/api/mesa/comando', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
      body: JSON.stringify({ comando, params }),
    });
    const d = await r.json();
    if (!r.ok) { _mesaAviso('Recusado (' + r.status + '): ' + (d.erro || '?'), true); return; }
    _mesaAviso('Comando #' + d.id + ' (' + comando + ') registrado — o worker responde em até 10s.', false);
    setTimeout(carregarMesa, 1500);
    setTimeout(carregarMesa, 11000);
  } catch (e) {
    _mesaAviso('Falha de rede ao enviar o comando.', true);
  }
}

document.querySelectorAll('.mesa-btn').forEach(b => {
  const rotulo = b.textContent;
  b.addEventListener('click', () => {
    if (_mesaArmado === b) {
      clearTimeout(_mesaDesarma);
      _mesaArmado = null;
      b.classList.remove('armado');
      b.textContent = rotulo;
      _mesaEnviar(b.dataset.comando);
      return;
    }
    if (_mesaArmado) { _mesaArmado.classList.remove('armado'); _mesaArmado.textContent = _mesaArmado.dataset.rotulo || _mesaArmado.textContent; }
    _mesaArmado = b;
    b.dataset.rotulo = rotulo;
    b.classList.add('armado');
    b.textContent = 'CONFIRMAR?';
    _mesaDesarma = setTimeout(() => {
      b.classList.remove('armado');
      b.textContent = rotulo;
      _mesaArmado = null;
    }, 5000);
  });
});

async function carregarMesa() {
  try {
    const r = await fetch('/api/mesa/comandos');
    if (r.status === 401 || r.status === 403) {
      // Estado honesto: sem token o histórico é ILEGÍVEL, não "vazio".
      el('mesa-historico').innerHTML =
        '<tr><td colspan="5" style="font-style:italic;color:var(--ink-3)">' +
        'Mesa desarmada — cole o token acima para ler o histórico.</td></tr>';
      return;
    }
    const rows = await r.json();
    if (!Array.isArray(rows) || !rows.length) return;
    el('mesa-historico').innerHTML = rows.map(c => {
      const st = esc(c.status || '?');
      return `<tr><td>#${esc(c.id)}</td>` +
        `<td>${esc(String(c.criado_em || '').slice(5, 16).replace('T', ' '))}</td>` +
        `<td class="forte">${esc(c.comando)}${c.params && c.params !== '{}' ? ' <span style="color:var(--ink-3)">' + esc(c.params) + '</span>' : ''}</td>` +
        `<td class="st-${st}">${st}</td>` +
        `<td style="color:var(--ink-3)">${esc(c.resultado || '—')}</td></tr>`;
    }).join('');
  } catch (e) {}
}
setInterval(() => { if (_abaAtiva === 'mesa') carregarMesa(); }, 10000);

// ── 5 · SISTEMA ─────────────────────────────────────────────────
async function carregarSistema() {
  try {
    const d = await fetch('/api/sistema').then(r => r.json());
    el('exp-servicos').innerHTML = (d.servicos || []).map(s => {
      const cor = s.estado === 'RODANDO' ? 'var(--gain)' : s.estado === 'PARADO' ? 'var(--loss)' : 'var(--ink-3)';
      return `<div class="micro-linha"><span class="k">${esc(s.nome)}</span>` +
        `<span class="v" style="color:${cor}">${esc(s.estado)}</span></div>`;
    }).join('');
    el('exp-relogios').innerHTML = (d.relogios || []).map(r =>
      `<div class="micro-linha"><span class="k">${esc(r.nome)}</span>` +
      `<span class="v" title="${esc(r.agenda)}">${esc(String(r.proximo).slice(5, 16).replace('T', ' '))}</span></div>`
    ).join('');
    el('sistema-agora').textContent = 'IMPRESSO ÀS ' + String(d.agora).slice(11, 19);
  } catch(e) {}
}

async function carregarSaude() {
  try {
    const h = await fetch('/health').then(r => r.json());
    const db = h.database || {};
    el('exp-banco').textContent = (db.backend || '—') + (db.db_path ? ' · local' : '');
  } catch(e) {}
}

// ── DIÁRIO editorial ────────────────────────────────────────────
function mdParaHtml(md) {
  const linhas = esc(md).split('\n');
  const saida = [];
  let ul = false, tabela = null, par = [];
  const inline = s => s
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  const fechaPar = () => { if (par.length) { saida.push('<p>' + inline(par.join(' ')) + '</p>'); par = []; } };
  const fechaUl = () => { if (ul) { saida.push('</ul>'); ul = false; } };
  const fechaTabela = () => {
    if (!tabela) return;
    const [cab, ...corpo] = tabela;
    saida.push('<table><thead><tr>' + cab.map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>' +
      corpo.map(l => '<tr>' + l.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') + '</tbody></table>');
    tabela = null;
  };
  for (const bruta of linhas) {
    const l = bruta.trim();
    if (l.startsWith('|')) {
      fechaPar(); fechaUl();
      const celulas = l.split('|').slice(1, -1).map(c => c.trim());
      if (celulas.every(c => /^:?-{2,}:?$/.test(c))) continue;
      (tabela = tabela || []).push(celulas);
      continue;
    }
    fechaTabela();
    if (l.startsWith('### ')) { fechaPar(); fechaUl(); saida.push('<h4>' + inline(l.slice(4)) + '</h4>'); }
    else if (l.startsWith('- ')) { fechaPar(); if (!ul) { saida.push('<ul>'); ul = true; } saida.push('<li>' + inline(l.slice(2)) + '</li>'); }
    else if (l.startsWith('*') && l.endsWith('*') && l.length > 2 && !l.startsWith('**')) { fechaPar(); fechaUl(); saida.push('<em class="rodape">' + inline(l.slice(1, -1)) + '</em>'); }
    else if (l === '---' || l === '') { fechaPar(); fechaUl(); }
    else { fechaUl(); par.push(l); }
  }
  fechaPar(); fechaUl(); fechaTabela();
  return saida.join('');
}

async function carregarDiario() {
  try {
    const d = await fetch('/api/diario').then(r => r.json());
    if (!d.titulo) return;
    el('sec-diario').style.display = '';
    el('diario-titulo').textContent = d.titulo;
    el('diario-corpo-doc').innerHTML = mdParaHtml(d.corpo || '');
  } catch(e) {}
}

// ── conexao Binance (modal + masthead com latência REAL) ────────
let _conexaoCache = null;
function abrirModalConexao() {
  el('modal-conexao').classList.add('open');
  if (!_conexaoCache) testarConexao(); else _renderConexao(_conexaoCache);
}
function fecharModalConexao() { el('modal-conexao').classList.remove('open'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') fecharModalConexao(); });

async function testarConexao() {
  const btn = el('btn-testar');
  btn.disabled = true;
  btn.innerHTML = '<span class="mc-spinner">↻</span> TESTANDO…';
  try {
    const d = await fetch('/api/conexao').then(r => r.json());
    _conexaoCache = d;
    _renderConexao(d);
  } catch(e) {
    btn.innerHTML = '✕ FALHA — sem resposta do servidor';
  } finally {
    btn.disabled = false;
    if (btn.innerHTML.includes('TESTANDO')) btn.innerHTML = '▶ TESTAR CONEXÃO';
  }
}

function _renderConexao(d) {
  const rs = d.rest_spot || {}, rf = d.rest_fut || {}, auth = d.auth || {}, modo = d.modo || {};
  el('mc-rest-spot').textContent = rs.ok ? `✓ ${rs.latencia_ms} ms` : `✕ ${rs.erro || 'timeout'}`;
  el('mc-rest-spot').className = 'mc-val ' + (rs.ok ? 'ok' : 'err');
  el('mc-rest-fut').textContent = rf.ok ? `✓ ${rf.latencia_ms} ms` : `✕ ${rf.erro || 'timeout'}`;
  el('mc-rest-fut').className = 'mc-val ' + (rf.ok ? 'ok' : 'err');

  // D-6: latência REAL no masthead (medida pelo ping do /api/conexao)
  el('hdr-latencia').textContent = rs.ok ? Math.round(rs.latencia_ms) + ' ms' : '—';
  el('hdr-latencia').style.color = rs.ok ? '' : 'var(--loss)';

  el('mc-pares-list').innerHTML = (d.pares || []).map(p => {
    const age = p.ultima_atualizacao && p.ultima_atualizacao !== '—' ? p.ultima_atualizacao : 'sem dados';
    return `<div class="mc-row"><span class="mc-lbl">${esc(p.symbol)}</span>` +
      `<span class="mc-val ${age === 'sem dados' ? 'muted' : 'ok'}">${age !== 'sem dados' ? '✓' : '○'} ${esc(age)}` +
      `${p.preco ? ' · $' + Number(p.preco).toLocaleString('pt-BR') : ''}</span></div>`;
  }).join('');

  el('mc-chave').textContent = auth.chave_configurada ? '✓ Configurada' : '✕ Não configurada';
  el('mc-chave').className = 'mc-val ' + (auth.chave_configurada ? 'ok' : 'err');
  el('mc-auth').textContent = auth.autenticado ? '✓ Autenticado' : auth.chave_configurada ? '✕ Falhou' : '— N/A';
  el('mc-auth').className = 'mc-val ' + (auth.autenticado ? 'ok' : auth.chave_configurada ? 'err' : 'muted');
  if (auth.saldo_usdt != null) {
    el('mc-saldo-row').style.display = '';
    el('mc-saldo').textContent = '$' + Number(auth.saldo_usdt).toFixed(2);
  } else el('mc-saldo-row').style.display = 'none';
  if (auth.erro) {
    el('mc-auth-err-row').style.display = '';
    el('mc-auth-err').textContent = auth.erro;
  } else el('mc-auth-err-row').style.display = 'none';

  el('mc-modo-badge').textContent = modo.label || '—';
  el('mc-modo-badge').className = 'mc-val ' + (modo.label === 'REAL' ? 'err' : 'warn');
  el('mc-dry-run').textContent = modo.dry_run ? '✓ Ativo' : '✕ Desativado';
  el('mc-dry-run').className = 'mc-val ' + (modo.dry_run ? 'warn' : 'ok');
  el('mc-allow-real').textContent = modo.allow_real ? '✓ Permitido' : '✕ Bloqueado';
  el('mc-allow-real').className = 'mc-val ' + (modo.allow_real ? 'err' : 'muted');
  if (d.timestamp) el('mc-ts').textContent = 'Atualizado: ' + new Date(d.timestamp).toLocaleTimeString('pt-BR');

  const hdr = el('header-modo');
  if (modo.label) {
    const real = modo.label === 'REAL';
    hdr.textContent = real ? '⚠ REAL' : 'SIMULAÇÃO';
    hdr.className = 'carimbo reto' + (real ? ' vermelho' : '');
    el('carimbo-modo').textContent = real ? '⚠ CAPITAL REAL' : 'SIMULAÇÃO';
    el('carimbo-modo').className = 'carimbo' + (real ? ' vermelho' : '');
    el('exp-modo').textContent = real ? 'CAPITAL REAL' : 'paper trading';
    el('exp-modo').style.color = real ? 'var(--loss)' : '';
  }

  const dot = el('conn-dot-binance');
  if (!rs.ok && !rf.ok) dot.className = 'conn-dot err';
  else if (!auth.chave_configurada || !auth.autenticado) dot.className = 'conn-dot warn';
  else dot.className = 'conn-dot ok';

  el('btn-testar').innerHTML = '↺ ATUALIZAR';

  el('exp-conexoes').innerHTML =
    `<div class="micro-linha"><span class="k">REST spot</span><span class="v" style="color:${rs.ok ? 'var(--gain)' : 'var(--loss)'}">${rs.ok ? rs.latencia_ms + ' ms' : 'falha'}</span></div>` +
    `<div class="micro-linha"><span class="k">REST futures</span><span class="v" style="color:${rf.ok ? 'var(--gain)' : 'var(--loss)'}">${rf.ok ? rf.latencia_ms + ' ms' : 'falha'}</span></div>` +
    `<div class="micro-linha"><span class="k">Autenticação</span><span class="v" style="color:${auth.autenticado ? 'var(--gain)' : 'var(--warn)'}">${auth.autenticado ? 'ok' : 'pendente'}</span></div>`;
}

async function _conexaoSilenciosa() {
  try {
    const d = await fetch('/api/conexao').then(r => r.json());
    _conexaoCache = d;
    _renderConexao(d);
  } catch(e) {}
}

// ── relógio + socket ────────────────────────────────────────────
setInterval(() => { el('header-time').textContent = new Date().toLocaleTimeString('pt-BR'); }, 1000);

const socket = io();
socket.on('connect', () => {
  el('conn-status').textContent = 'ONLINE';
  el('conn-status').className = 'online';
  carregarPares();
  fetch('/api/estado/' + parAtivo).then(r => r.json()).then(d => {
    dadosPares[d.symbol] = d;
    if (d.symbol === parAtivo) aplicarDados(d);
  });
  fetch('/api/trades').then(r => r.json()).then(ts => ts.slice(0, 30).reverse().forEach(t => { addTrade(t); _fitaChip(t); })).catch(() => {});
  carregarEventos();
  carregarRisco();
});
socket.on('disconnect', () => {
  el('conn-status').textContent = 'OFFLINE';
  el('conn-status').className = '';
});
socket.on('update', d => {
  _feedTs = Date.now();
  dadosPares[d.symbol] = d;
  if (d.symbol === parAtivo) { aplicarDados(d); varrerPipeline(); }
});
socket.on('trade', t => { _feedTs = Date.now(); addTrade(t); fitaRegistrar(t); });
socket.on('evento', ev => addEvento(ev));

// ── ciclos de leitura ───────────────────────────────────────────
setInterval(carregarPares,     15000);
setInterval(carregarRisco,     30000);
setInterval(carregarLucro,     20000); carregarLucro();
setInterval(carregarEquity,    60000);
setInterval(carregarAtividade, 15000); carregarAtividade();
setInterval(carregarGates,    300000);
setInterval(carregarDiario,   600000);
setInterval(carregarAnalytics, 60000); carregarAnalytics();
setInterval(carregarIncidentes, 60000); carregarIncidentes();
setInterval(carregarSistema,   60000); carregarSistema();
setInterval(carregarSparks,    60000); carregarSparks();
setInterval(_conexaoSilenciosa, 60000); setTimeout(_conexaoSilenciosa, 2500);
carregarSaude();
renderBreakdown({});
if (localStorage.getItem('bxbot-tema') === 'light') el('exp-tema').textContent = 'papel-jornal';
el('btn-tema').addEventListener('click', alternarTema);
el('btn-conexao').addEventListener('click', abrirModalConexao);
el('mc-fechar').addEventListener('click', fecharModalConexao);
el('modal-conexao').addEventListener('click', e => { if (e.target === el('modal-conexao')) fecharModalConexao(); });
el('btn-testar').addEventListener('click', testarConexao);
el('btn-ev-pausa').addEventListener('click', alternarPausaEventos);
el('btn-fita-pausa').addEventListener('click', alternarPausaFita);

// boot: aba do hash (deep-link) ou cockpit
ativarAba((location.hash || '').replace('#/', ''));
