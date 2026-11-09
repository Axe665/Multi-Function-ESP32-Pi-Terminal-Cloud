/* 云前端：原生 ES 模块，无构建步骤。 */

import { lines, barList, fmtNum, fmtDay, SERIES_COLORS } from "./charts.js";

const main = document.getElementById("main");
let META = null;
let currentReport = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json", ...(opts.headers || {}) }, ...opts });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = JSON.stringify((await r.json()).detail).slice(0, 180); } catch { /* 非 JSON */ }
    throw new Error(`${r.status} ${detail}`);
  }
  return r.json();
}

function toast(msg) {
  document.querySelector(".toast")?.remove();
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 2600);
}

function panel(title, meta, body, cls = "") {
  return `<section class="panel ${cls}">
    <div class="panel-head"><h2>${esc(title)}</h2>${meta ? `<span class="meta">${meta}</span>` : ""}</div>
    ${body}
  </section>`;
}

function errBox(msg) {
  return `<div class="error-box"><span>${esc(msg)}</span><button class="btn btn-sm" data-retry>重试</button></div>`;
}

function skeleton(n = 3) {
  return `<div class="list">${Array.from({ length: n }, (_, i) =>
    `<div class="list-row"><div class="grow"><span class="skeleton" style="width:${40 + i * 14}%"></span></div></div>`).join("")}</div>`;
}

const RISK = { info: "提示", watch: "关注", alert: "告警" };

/* k 闸门：5 格 + 实际 k */
function gate(k) {
  if (!k) return `<span class="dash">—</span>`;
  const filled = Math.min(5, k);
  return `<span class="gate" title="k=${k}，门槛 5">${Array.from({ length: 5 },
    (_, i) => `<i class="${i < filled ? "on" : ""}"></i>`).join("")}<span>${k}</span></span>`;
}

function metricMeta(key) {
  return (META?.metrics || []).find((m) => m.key === key) || { key, label: key, unit: "" };
}

async function ensureMeta() {
  if (!META) META = await api("/api/v1/meta");
  document.getElementById("scope").textContent = META.cloud_name;
  return META;
}

/* ---------------------------------------------------------------- 总览 */

async function viewOverview() {
  main.innerHTML = `<div class="title-row"><div><h1>总览</h1><p class="sub">读取中…</p></div></div>`;
  await ensureMeta().catch(() => {});

  let o;
  try {
    o = await api("/api/v1/overview");
  } catch (e) {
    main.innerHTML = `<div class="title-row"><div><h1>总览</h1></div></div><div class="row">${errBox(`读不到云端状态：${e.message}`)}</div>`;
    return;
  }

  document.getElementById("side-foot").innerHTML = `<dl>
    <dt>云 ID</dt><dd>${esc(o.cloud_id)}</dd>
    <dt>AI</dt><dd>${o.ai_mode === "local" ? "本地模型" : "公有 API"}</dd>
    <dt>最小 k</dt><dd>${META?.k_min ?? 5}</dd>
  </dl>`;

  const ai = o.latest_ai;
  let aiDetail = null;
  if (ai) {
    try { aiDetail = await api(`/api/v1/ai/reports/${ai.id}`); } catch { /* 报告可能已被清理 */ }
  }
  const c = aiDetail?.content;
  const aiBlock = c ? `
    <section class="ai">
      <div class="ai-head">
        <span class="ai-kicker">本周 AI 摘要 · ${esc(ai.model)} · ${fmtDay(c.period.from)} – ${fmtDay(c.period.to)}</span>
        <a class="btn btn-sm" href="#/reports">打开报告</a>
      </div>
      <h2>${esc(c.headline)}</h2>
      <p>${esc(c.narrative)}</p>
      ${c.actions?.length ? `<div class="ai-actions">${c.actions.map((a) => `<div>${esc(a)}</div>`).join("")}</div>` : ""}
      <div class="ai-foot">
        <span>模型 ${esc(ai.model)}</span>
        <span>prompt ${esc(ai.prompt_hash)}</span>
        <span>${ai.model === "rules-v1" ? "规则模板，不消耗 token" : `token ${fmtNum(ai.tokens_in)} / ${fmtNum(ai.tokens_out)}`}</span>
        <span>置信度 ${c.confidence === "high" ? "高" : "低（规则模板）"}</span>
      </div>
    </section>`
    : `<section class="ai"><div class="ai-head"><span class="ai-kicker">本周 AI 摘要</span></div>
       <h2>还没有生成报告</h2>
       <p>点右上角生成一份，云端会用本地模型解读这周的日聚合；模型不可用时退回规则模板，不会留空。</p></section>`;

  const edgeRows = o.edges.map((e) => `
    <tr data-href="#/edges/${esc(e.edge_id)}">
      <td><strong>${esc(e.name)}</strong><div class="mono">${esc(e.edge_id)}</div></td>
      <td class="num">${e.devices}</td>
      <td class="num">${e.online_devices}</td>
      <td class="num">${e.last_seen ? Math.round((o.generated_at - e.last_seen) / 60000) + " 分钟前" : "—"}</td>
    </tr>`).join("");

  main.innerHTML = `
    <div class="title-row">
      <div><h1>总览</h1>
        <p class="sub">${o.edges_total} 个边 · ${o.devices_total} 台设备 · 最近 7 天上报完整率
        ${o.completeness_7d.toFixed(1)}% · 只接收 k≥5 的日聚合</p></div>
      <div class="title-actions">
        <span class="stamp">更新于 ${new Date(o.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
        <button class="btn btn-primary" id="gen-ai">生成 AI 报告</button>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="kpi-label">在线边节点</div>
        <div class="kpi-value">${o.edges_online}<small>/ ${o.edges_total}</small></div>
        <div class="kpi-note">2 小时内有上报</div></div>
      <div class="kpi"><div class="kpi-label">在线设备</div>
        <div class="kpi-value">${o.devices_online}<small>/ ${o.devices_total}</small></div>
        <div class="kpi-note">与会话状态一致</div></div>
      <div class="kpi"><div class="kpi-label">7 天完整率</div>
        <div class="kpi-value">${o.completeness_7d.toFixed(1)}<small>%</small></div>
        <div class="kpi-note">${fmtNum(o.rows_7d)} 行日聚合</div></div>
      <div class="kpi"><div class="kpi-label">待处理告警</div>
        <div class="kpi-value">${fmtNum(o.alerts_total)}</div>
        <div class="kpi-note">最小 k 见 ${o.k_min_seen || "—"}</div></div>
    </div>

    <div class="row">${aiBlock}</div>

    <div class="row split" style="align-items:stretch">
      <section class="panel fill">
        <div class="panel-head"><h2>告警 Top 5</h2><span class="meta">按建议文本聚合 · 最近 7 天</span></div>
        <div id="alerts">${skeleton(3)}</div>
        <p class="hint grow-block" style="margin:14px 0 0;font-size:12px;color:var(--faint)">
          同一句话出现多次说明它在多台设备上重复触发，通常对应一个共同的环境因素。</p>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>边节点</h2><span class="meta">点击进入下钻</span></div>
        <table class="table">
          <thead><tr><th>边</th><th class="num">设备</th><th class="num">在线</th><th class="num">最近上报</th></tr></thead>
          <tbody>${edgeRows}</tbody>
        </table>
      </section>
    </div>`;

  barList(document.getElementById("alerts"),
    o.alerts_top.map((a) => ({ label: a.text, value: a.count, note: a.edge })),
    { suffix: "次" });

  document.getElementById("gen-ai").onclick = async (ev) => {
    ev.currentTarget.disabled = true;
    ev.currentTarget.textContent = "正在生成…";
    try {
      await api("/api/v1/ai/reports", { method: "POST", body: JSON.stringify({ scope: "fleet" }) });
      toast("报告已生成");
      route(true);
    } catch (e) {
      toast(`生成失败：${e.message}`);
      ev.currentTarget.disabled = false;
      ev.currentTarget.textContent = "生成 AI 报告";
    }
  };

  main.querySelectorAll("[data-href]").forEach((tr) =>
    tr.addEventListener("click", () => { location.hash = tr.dataset.href; }));
}

/* ---------------------------------------------------------------- 边与设备 */

async function viewEdges() {
  main.innerHTML = `<div class="title-row"><div><h1>边与设备</h1><p class="sub">读取中…</p></div></div>`;
  await ensureMeta().catch(() => {});

  let edges;
  try {
    edges = await api("/api/v1/edges");
  } catch (e) {
    main.innerHTML = `<div class="title-row"><div><h1>边与设备</h1></div></div><div class="row">${errBox(`读不到边列表：${e.message}`)}</div>`;
    return;
  }

  main.innerHTML = `
    <div class="title-row">
      <div><h1>边与设备</h1>
        <p class="sub">每个边走一台管理软件，管 3 台终端。点边看设备，点设备看趋势。</p></div>
      <div class="title-actions"><span class="stamp">${edges.length} 个边 · ${edges.reduce((n, e) => n + e.devices, 0)} 台设备</span></div>
    </div>
    <div class="row">
      <section class="panel">
        <div class="panel-head"><h2>边节点</h2><span class="meta">最近 7 天</span></div>
        <table class="table responsive">
          <thead><tr><th>边</th><th class="num">设备</th><th class="num">在线</th><th class="num">有数据天数</th><th class="num">最小 k</th><th class="num">指标数</th></tr></thead>
          <tbody>${edges.map((e) => `<tr data-href="#/edges/${esc(e.edge_id)}">
            <td><strong>${esc(e.name)}</strong><div class="mono">${esc(e.edge_id)}</div></td>
            <td class="num">${e.devices}</td>
            <td class="num">${e.online_devices}</td>
            <td class="num">${e.days}<span class="faint"> / 7</span></td>
            <td class="num">${gate(e.k_min)}</td>
            <td class="num">${e.metrics.length}</td>
          </tr>`).join("")}</tbody>
        </table>
      </section>
    </div>`;

  main.querySelectorAll("[data-href]").forEach((tr) =>
    tr.addEventListener("click", () => { location.hash = tr.dataset.href; }));
}

async function viewEdge(id) {
  main.innerHTML = `<div class="title-row"><div><h1>边节点</h1><p class="sub">读取中…</p></div></div>`;

  let edges;
  try {
    edges = await api("/api/v1/edges");
  } catch (e) {
    main.innerHTML = `<div class="title-row"><h1>边节点</h1></div><div class="row">${errBox(e.message)}</div>`;
    return;
  }
  const edge = edges.find((e) => e.edge_id === id);
  if (!edge) {
    main.innerHTML = `<div class="title-row"><h1>找不到这个边</h1></div>
      <div class="row">${errBox(`没有 ${id}。`)}</div>`;
    return;
  }

  const adv = await api(`/api/v1/advice?edge=${encodeURIComponent(id)}&limit=6`).catch(() => []);
  const alerts = adv.filter((a) => a.risk === "alert").length;

  main.innerHTML = `
    <div class="title-row">
      <div>
        <p class="faint" style="margin:0 0 6px;font-size:12px"><a href="#/edges">← 边与设备</a></p>
        <h1>${esc(edge.name)}</h1>
        <p class="sub"><span class="mono">${esc(edge.edge_id)}</span> · ${edge.devices} 台设备 ·
          证书指纹 <span class="mono">${esc(edge.cert_fp || "—")}</span></p>
      </div>
      <div class="title-actions"><span class="stamp">最近上报 ${edge.last_seen ? new Date(edge.last_seen).toLocaleString("zh-CN") : "—"}</span></div>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="kpi-label">设备</div><div class="kpi-value">${edge.devices}</div>
        <div class="kpi-note">${edge.online_devices} 台在线</div></div>
      <div class="kpi"><div class="kpi-label">有数据天数</div><div class="kpi-value">${edge.days}<small>/ 7</small></div>
        <div class="kpi-note">按日聚合统计</div></div>
      <div class="kpi"><div class="kpi-label">最小 k</div><div class="kpi-value">${edge.k_min || "—"}</div>
        <div class="kpi-note">门槛 ${META?.k_min ?? 5}</div></div>
      <div class="kpi"><div class="kpi-label">告警</div><div class="kpi-value">${alerts}</div>
        <div class="kpi-note">最近 6 条建议中</div></div>
    </div>

    <div class="row split" style="align-items:stretch">
      <section class="panel fill">
        <div class="panel-head"><h2>设备</h2><span class="meta">${edge.devices} 台</span></div>
        <table class="table responsive">
          <thead><tr><th>设备</th><th>场景</th><th class="num">天数</th><th class="num">最小 k</th><th class="num">指标</th><th></th></tr></thead>
          <tbody>${edge.devices_list.map((d) => `<tr data-href="#/devices/${esc(d.device_id)}">
            <td><strong>${esc(d.device_id)}</strong>
              <div class="row-sub">${d.online ? "在线" : "离线"} · 模块 ${JSON.parse(d.modules_json || "[]").join(", ") || "—"}</div></td>
            <td>${esc(d.scene)}</td>
            <td class="num">${edge.days}</td>
            <td class="num">${gate(edge.k_min)}</td>
            <td class="num">${edge.metrics.length}</td>
            <td class="num"><span class="btn btn-sm">查看</span></td>
          </tr>`).join("")}</tbody>
        </table>
        <p class="grow-block" style="margin:14px 0 0;font-size:12px;color:var(--faint)">
          云端只存日聚合：每天的 mean / slope / z / n / k，不存逐条读数。</p>
      </section>

      ${panel("最近建议", `${adv.length} 条`, `<div class="list">
        ${adv.map((a) => `<div class="list-row">
          <span class="pill pill-${a.risk}">${RISK[a.risk]}</span>
          <div class="grow"><div class="row-text">${esc(a.text)}</div>
            <div class="row-sub">${esc(a.device_id)} · ${esc(a.source)}</div></div>
          <div class="row-time">${fmtDay(new Date(a.created_at).toISOString().slice(0, 10))}</div>
        </div>`).join("") || `<div class="empty">这个边最近没有建议。</div>`}
      </div>`)}
    </div>`;

  main.querySelectorAll("[data-href]").forEach((tr) =>
    tr.addEventListener("click", () => { location.hash = tr.dataset.href; }));
}

async function viewDevice(id) {
  main.innerHTML = `<div class="title-row"><div><h1>设备</h1><p class="sub">读取中…</p></div></div>`;
  await ensureMeta().catch(() => {});

  let devs, rows;
  try {
    [devs, rows] = await Promise.all([
      api("/api/v1/devices"),
      api("/api/v1/daily?limit=60"),
    ]);
  } catch (e) {
    main.innerHTML = `<div class="title-row"><h1>设备</h1></div><div class="row">${errBox(e.message)}</div>`;
    return;
  }
  const dev = devs.find((d) => d.device_id === id);
  if (!dev) {
    main.innerHTML = `<div class="title-row"><h1>找不到设备</h1></div><div class="row">${errBox(`没有 ${id}。`)}</div>`;
    return;
  }

  const own = rows.filter((r) => r.device_id === id);
  const metrics = [...new Set(own.map((r) => r.metric))];
  let current = metrics[0];

  main.innerHTML = `
    <div class="title-row">
      <div>
        <p class="faint" style="margin:0 0 6px;font-size:12px">
          <a href="#/edges">边与设备</a> / <a href="#/edges/${esc(dev.edge_id)}">${esc(dev.edge_id)}</a></p>
        <h1>${esc(dev.device_id)}</h1>
        <p class="sub">${esc(dev.scene)} · ${dev.online ? "在线" : "离线"} · 最小 k ${dev.k_min || "—"}</p>
      </div>
      <div class="title-actions">
        <div class="seg" id="metric-tabs">
          ${metrics.map((m, i) => `<button data-metric="${esc(m)}" aria-pressed="${i === 0}">${esc(metricMeta(m).label)}</button>`).join("")}
        </div>
      </div>
    </div>

    <div class="row">
      <section class="panel">
        <div class="panel-head"><h2 id="trend-title">趋势</h2><span class="meta" id="trend-meta"></span></div>
        <div id="chart"></div>
        <div class="legend" style="margin-top:10px">
          <span><i style="background:var(--accent)"></i>日聚合均值</span>
          <span>缺报的日子断开，不连线</span>
        </div>
      </section>
    </div>

    <div class="row">
      <section class="panel">
        <div class="panel-head"><h2>日聚合明细</h2><span class="meta">只含聚合字段</span></div>
        <table class="table responsive">
          <thead><tr><th>日期</th><th>指标</th><th class="num">均值</th><th class="num">斜率</th><th class="num">z</th><th class="num">样本</th><th class="num">k</th></tr></thead>
          <tbody>${own.slice().sort((a, b) => b.day.localeCompare(a.day)).map((r) => `<tr>
            <td>${esc(r.day)}</td>
            <td>${esc(metricMeta(r.metric).label)}</td>
            <td class="num">${fmtNum(r.mean, 2)}<span class="faint"> ${esc(metricMeta(r.metric).unit)}</span></td>
            <td class="num">${fmtNum(r.slope, 3)}</td>
            <td class="num">${fmtNum(r.z, 2)}</td>
            <td class="num">${fmtNum(r.n_samples)}</td>
            <td class="num">${gate(r.k)}</td>
          </tr>`).join("") || `<tr><td colspan="7" class="dash">这台设备最近没有日聚合。</td></tr>`}</tbody>
        </table>
      </section>
    </div>`;

  const host = document.getElementById("chart");
  async function draw(metric) {
    const m = metricMeta(metric);
    document.getElementById("trend-title").textContent = `${m.label}（${m.unit}）`;
    document.getElementById("trend-meta").textContent = "最近 14 天";
    host.innerHTML = `<div class="empty">读取中…</div>`;
    try {
      const s = await api(`/api/v1/series?metric=${encodeURIComponent(metric)}&device=${encodeURIComponent(id)}`);
      const days = s.points.map((p) => p.day);
      lines(host, {
        series: [{ id, name: id, points: s.points.map((p) => ({ day: p.day, value: p.mean })) }],
        days, unit: m.unit, decimals: 1, label: `${id} ${m.label} 趋势`,
      });
    } catch (e) {
      host.innerHTML = errBox(`读不到趋势：${e.message}`);
    }
  }

  document.getElementById("metric-tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-metric]");
    if (!b) return;
    current = b.dataset.metric;
    document.querySelectorAll("#metric-tabs button").forEach((x) =>
      x.setAttribute("aria-pressed", String(x === b)));
    draw(current);
  });

  draw(current);
}

/* ---------------------------------------------------------------- 对比 */

let compareMetric = "env.temp";

async function viewCompare() {
  main.innerHTML = `<div class="title-row"><div><h1>对比</h1><p class="sub">读取中…</p></div></div>`;
  await ensureMeta().catch(() => {});

  let data;
  try {
    data = await api(`/api/v1/compare?metric=${encodeURIComponent(compareMetric)}`);
  } catch (e) {
    main.innerHTML = `<div class="title-row"><h1>对比</h1></div><div class="row">${errBox(e.message)}</div>`;
    return;
  }
  const m = metricMeta(compareMetric);

  main.innerHTML = `
    <div class="title-row">
      <div><h1>对比</h1>
        <p class="sub">同指标跨边对比。左轴是指数：每条边以首日 = 100，抹平量级差异后才能看出走势快慢。</p></div>
      <div class="title-actions">
        <select class="control" id="cmp-metric" aria-label="对比指标">
          ${(META?.metrics || []).map((x) => `<option value="${esc(x.key)}" ${x.key === compareMetric ? "selected" : ""}>${esc(x.label)}</option>`).join("")}
        </select>
      </div>
    </div>

    <div class="row">
      <section class="panel">
        <div class="panel-head"><h2>${esc(m.label)}指数</h2><span class="meta">首日 = 100 · 原始单位 ${esc(m.unit)}</span></div>
        <div id="cmp-chart"></div>
        <div class="legend" style="margin-top:10px">
          ${data.edges.map((e, i) => `<span><i style="background:${SERIES_COLORS[i % SERIES_COLORS.length]}"></i>${esc(e.name)}</span>`).join("")}
        </div>
      </section>
    </div>

    <div class="row">
      <section class="panel">
        <div class="panel-head"><h2>末日指数与变化</h2><span class="meta">相对各边首日</span></div>
        <table class="table responsive">
          <thead><tr><th>边</th><th class="num">首日</th><th class="num">末日</th><th class="num">指数</th><th class="num">变化</th><th class="num">最小 k</th></tr></thead>
          <tbody>${data.edges.map((e) => {
            const first = e.points[0], last = e.points[e.points.length - 1];
            // 百分比按屏幕上显示的那两个数算，避免和四舍五入后的值对不上
            const fv = +first.value.toFixed(2), lv = +last.value.toFixed(2);
            return `<tr>
              <td><strong>${esc(e.name)}</strong><div class="mono">${esc(e.edge_id)}</div></td>
              <td class="num">${fmtNum(fv, 2)}</td>
              <td class="num">${fmtNum(lv, 2)}</td>
              <td class="num">${fmtNum(last.index, 1)}</td>
              <td class="num">${lv >= fv ? "+" : "−"}${fmtNum(Math.abs((lv / fv - 1) * 100), 1)}%</td>
              <td class="num">${gate(e.k_min)}</td>
            </tr>`;
          }).join("")}</tbody>
        </table>
      </section>
    </div>`;

  lines(document.getElementById("cmp-chart"), {
    series: data.edges.map((e, i) => ({
      id: e.edge_id, name: e.name, color: SERIES_COLORS[i % SERIES_COLORS.length],
      points: e.points.map((p) => ({ day: p.day, value: p.index })),
    })),
    days: data.days, unit: "（指数）", decimals: 1, height: 300, label: `${m.label} 跨边指数对比`,
  });

  document.getElementById("cmp-metric").onchange = (ev) => {
    compareMetric = ev.target.value;
    route(true);
  };
}

/* ---------------------------------------------------------------- AI 报告 */

async function viewReports() {
  main.innerHTML = `<div class="title-row"><div><h1>AI 报告</h1><p class="sub">读取中…</p></div></div>`;
  await ensureMeta().catch(() => {});

  let list;
  try {
    list = await api("/api/v1/ai/reports?limit=30");
  } catch (e) {
    main.innerHTML = `<div class="title-row"><h1>AI 报告</h1></div><div class="row">${errBox(e.message)}</div>`;
    return;
  }

  const active = currentReport && list.find((r) => r.id === currentReport) ? currentReport : list[0]?.id;
  let detail = null;
  if (active) {
    detail = await api(`/api/v1/ai/reports/${active}`).catch(() => null);
  }

  const scopeLabel = { fleet: "全网", edge: "单边", device: "单设备" };

  main.innerHTML = `
    <div class="title-row">
      <div><h1>AI 报告</h1>
        <p class="sub">默认走本地 Ollama；模型不可用时退回规则模板并标为低置信度，不会留空报告。</p></div>
      <div class="title-actions">
        <select class="control" id="gen-scope" aria-label="报告范围">
          <option value="fleet">全网</option>
          <option value="edge">单边</option>
        </select>
        <button class="btn btn-primary" id="gen">生成报告</button>
      </div>
    </div>

    <div class="row split">
      <section class="panel">
        <div class="panel-head"><h2>报告列表</h2><span class="meta">${list.length} 份</span></div>
        <table class="table responsive">
          <thead><tr><th>范围</th><th>周期</th><th>模型</th><th class="num">token</th></tr></thead>
          <tbody>${list.map((r) => `<tr data-report="${r.id}" ${r.id === active ? 'style="background:var(--raised)"' : ""}>
            <td><strong>${scopeLabel[r.scope] || r.scope}</strong>
              <div class="mono">${esc(r.scope_id || "全部")}</div></td>
            <td>${fmtDay(r.period_start)} – ${fmtDay(r.period_end)}</td>
            <td class="muted">${esc(r.model)}</td>
            <td class="num">${r.model === "rules-v1" ? "—" : `${fmtNum(r.tokens_in)} / ${fmtNum(r.tokens_out)}`}</td>
          </tr>`).join("") || `<tr><td colspan="4" class="dash">还没有报告。</td></tr>`}</tbody>
        </table>
      </section>

      <section class="panel">
        ${detail ? `
          <div class="ai-head">
            <span class="ai-kicker">${scopeLabel[detail.scope] || detail.scope} · ${esc(detail.model)} ·
              ${fmtDay(detail.content.period.from)} – ${fmtDay(detail.content.period.to)}</span>
            <span class="pill ${detail.content.confidence === "high" ? "pill-ok" : "pill-watch"}">
              ${detail.content.confidence === "high" ? "高置信" : "规则模板"}</span>
          </div>
          <h2 style="font-size:19px;font-weight:500;line-height:1.4;margin:0 0 10px">${esc(detail.content.headline)}</h2>
          <p style="margin:0;font-size:14px;line-height:1.65">${esc(detail.content.narrative)}</p>
          ${detail.content.findings?.length ? `<div class="list" style="margin-top:16px">
            ${detail.content.findings.map((f) => `<div class="list-row">
              <span class="pill pill-${f.risk}">${RISK[f.risk]}</span>
              <div class="grow"><div class="row-text">${esc(f.label)}</div>
                <div class="row-sub" style="white-space:normal">${esc(f.detail)}</div></div>
            </div>`).join("")}</div>` : ""}
          ${detail.content.actions?.length ? `<div class="ai-actions">
            ${detail.content.actions.map((a) => `<div>${esc(a)}</div>`).join("")}</div>` : ""}
          <div class="ai-foot">
            <span>prompt ${esc(detail.prompt_hash)}</span>
            <span>${detail.model === "rules-v1" ? "规则模板" : `token ${fmtNum(detail.tokens_in)} / ${fmtNum(detail.tokens_out)}`}</span>
            <span>${new Date(detail.created_at).toLocaleString("zh-CN")}</span>
          </div>`
        : `<div class="empty">左侧选一份报告查看内容。</div>`}
      </section>
    </div>`;

  main.querySelectorAll("[data-report]").forEach((tr) =>
    tr.addEventListener("click", () => {
      currentReport = Number(tr.dataset.report);
      route(true);
    }));

  document.getElementById("gen").onclick = async (ev) => {
    const scope = document.getElementById("gen-scope").value;
    const body = { scope };
    if (scope === "edge") {
      const edges = await api("/api/v1/edges").catch(() => []);
      if (!edges.length) { toast("还没有边节点"); return; }
      body.scope_id = edges[0].edge_id;
    }
    ev.currentTarget.disabled = true;
    ev.currentTarget.textContent = "生成中…";
    try {
      const r = await api("/api/v1/ai/reports", { method: "POST", body: JSON.stringify(body) });
      currentReport = r.id;
      toast(`已生成（${r.model}）`);
      route(true);
    } catch (e) {
      toast(`生成失败：${e.message}`);
    } finally {
      ev.currentTarget.disabled = false;
      ev.currentTarget.textContent = "生成报告";
    }
  };
}

/* ---------------------------------------------------------------- 隐私审计 */

async function viewPrivacy() {
  main.innerHTML = `<div class="title-row"><div><h1>隐私审计</h1><p class="sub">读取中…</p></div></div>`;

  let a;
  try {
    a = await api("/api/v1/privacy/audit");
  } catch (e) {
    main.innerHTML = `<div class="title-row"><h1>隐私审计</h1></div><div class="row">${errBox(e.message)}</div>`;
    return;
  }

  const total = Object.values(a.k_distribution).reduce((n, v) => n + v, 0) || 1;
  const order = ["5", "6-7", "8-9", "10+"];

  main.innerHTML = `
    <div class="title-row">
      <div><h1>隐私审计</h1>
        <p class="sub">这张表回答一个问题：云端到底拿到了什么。答案是日聚合、建议文本和计数，没有别的。</p></div>
      <div class="title-actions"><span class="stamp">保留 ${a.retention_days} 天</span></div>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="kpi-label">观测到的最小 k</div>
        <div class="kpi-value">${a.k_min_seen || "—"}</div><div class="kpi-note">门槛 ${a.k_min}</div></div>
      <div class="kpi"><div class="kpi-label">被抑制的桶</div>
        <div class="kpi-value">${fmtNum(a.suppressed_buckets)}</div><div class="kpi-note">k＜${a.k_min}，从未离开端</div></div>
      <div class="kpi"><div class="kpi-label">原始字段拦截</div>
        <div class="kpi-value">${fmtNum(a.raw_rejected)}</div><div class="kpi-note">摄取时直接 422</div></div>
      <div class="kpi"><div class="kpi-label">已入库行</div>
        <div class="kpi-value">${fmtNum(a.rows_ingested)}</div><div class="kpi-note">全部是日聚合</div></div>
    </div>

    <div class="row split-even">
      <section class="panel">
        <div class="panel-head"><h2>k 值分布</h2><span class="meta">${fmtNum(a.rows)} 行日聚合</span></div>
        <div class="dist">
          ${order.map((b) => `<div class="dist-row">
            <span>k = ${b}</span>
            <div class="dist-track"><div class="dist-fill" style="width:${Math.round(((a.k_distribution[b] || 0) / total) * 100)}%"></div></div>
            <span style="text-align:right">${a.k_distribution[b] || 0}</span>
          </div>`).join("")}
        </div>
        <p style="margin:16px 0 0;font-size:12px;color:var(--faint);line-height:1.6">
          分布里没有低于 5 的桶，因为低于门槛的行在端侧就被丢弃了；边只上报抑制计数，不上报被抑制的内容。</p>
      </section>

      <section class="panel">
        <div class="panel-head"><h2>字段白名单</h2><span class="meta">摄取时强校验</span></div>
        <div class="list">
          <div class="list-row"><div class="grow"><div class="row-text">保留</div>
            <div class="row-sub" style="white-space:normal">${a.fields_kept.map(esc).join(" · ")}</div></div></div>
          <div class="list-row"><div class="grow"><div class="row-text">拒收</div>
            <div class="row-sub" style="white-space:normal">${a.fields_rejected.map(esc).join(" · ")}</div></div>
            <span class="pill pill-alert">422</span></div>
        </div>
        <p style="margin:16px 0 0;font-size:12px;color:var(--faint);line-height:1.6">
          载荷里出现任一个拒收字段，整份报表被拒收并记一次拦截，不做「过滤后放行」——静默清洗会掩盖上游的 bug。</p>
      </section>
    </div>

    <div class="row">
      <section class="panel">
        <div class="panel-head"><h2>审计日志</h2><span class="meta">最近的摄取与导出</span></div>
        <table class="table responsive">
          <thead><tr><th>时间</th><th>动作</th><th>对象</th><th>详情</th></tr></thead>
          <tbody>${a.audit.map((x) => `<tr>
            <td class="muted">${new Date(x.created_at).toLocaleString("zh-CN")}</td>
            <td>${esc(x.action)}</td>
            <td class="mono">${esc(x.target)}</td>
            <td class="faint" style="white-space:normal">${esc(x.detail)}</td>
          </tr>`).join("") || `<tr><td colspan="4" class="dash">还没有审计记录。</td></tr>`}</tbody>
        </table>
      </section>
    </div>`;
}

/* ---------------------------------------------------------------- 路由 */

const routes = [
  [/^#\/$/, viewOverview, "overview"],
  [/^#\/overview$/, viewOverview, "overview"],
  [/^#\/edges$/, viewEdges, "edges"],
  [/^#\/edges\/(.+)$/, (m) => viewEdge(decodeURIComponent(m[1])), "edges"],
  [/^#\/devices\/(.+)$/, (m) => viewDevice(decodeURIComponent(m[1])), "edges"],
  [/^#\/compare$/, viewCompare, "compare"],
  [/^#\/reports$/, viewReports, "reports"],
  [/^#\/privacy$/, viewPrivacy, "privacy"],
];

let routing = false;

async function route(quiet = false) {
  if (routing) return;
  routing = true;
  const hash = location.hash || "#/";
  const hit = routes.find(([re]) => re.test(hash));
  document.querySelectorAll(".nav a").forEach((a) => a.removeAttribute("aria-current"));
  if (hit?.[2]) document.querySelector(`.nav a[data-nav="${hit[2]}"]`)?.setAttribute("aria-current", "page");
  if (!quiet) window.scrollTo(0, 0);
  try {
    if (hit) await hit[1](hash.match(hit[0]));
    else main.innerHTML = `<div class="title-row"><h1>页面不存在</h1></div>`;
  } catch (e) {
    main.innerHTML = `<div class="title-row"><h1>出错了</h1></div><div class="row">${errBox(e.message)}</div>`;
  } finally {
    routing = false;
    document.querySelectorAll("[data-retry]").forEach((b) =>
      b.addEventListener("click", () => route(true)));
  }
}

window.addEventListener("hashchange", () => route());
route();
