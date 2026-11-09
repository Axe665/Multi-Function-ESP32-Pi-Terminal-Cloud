/* 云的图表：多边对比折线、条形清单。全部手写 SVG，规则与边端一致。 */

const NS = "http://www.w3.org/2000/svg";

export const SERIES_COLORS = ["#14695a", "#12161a", "#b08a4f", "#7f8f88"];

export function fmtNum(v, decimals = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("zh-CN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtDay(day) {
  const [y, m, d] = String(day).split("-");
  return `${Number(m)}/${Number(d)}`;
}

function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const mag = Math.pow(10, Math.floor(Math.log10(span / count)));
  let step = mag;
  for (const m of [1, 2, 2.5, 5, 10]) {
    step = m * mag;
    if (span / step <= count + 1) break;
  }
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function mount(host, draw) {
  let last = 0;
  const run = () => {
    const w = host.clientWidth;
    if (!w || Math.abs(w - last) < 2) return;
    last = w;
    host.replaceChildren(draw(w));
  };
  const ro = new ResizeObserver(run);
  ro.observe(host);
  run();
  return () => ro.disconnect();
}

/* series: [{ id, name, points:[{day, value}], color? }]  —— 共享横轴日期 */
export function lines(host, opts) {
  const { series = [], days = [], unit = "", decimals = 1, height = 280, label = "" } = opts;
  if (!series.length || !days.length) {
    host.replaceChildren(Object.assign(document.createElement("div"), {
      className: "empty", textContent: "这个周期没有可对比的数据。",
    }));
    return;
  }

  mount(host, (W) => {
    const padL = 44, padR = 16, padT = 14, padB = 26;
    const H = height;
    const iw = Math.max(40, W - padL - padR);
    const ih = H - padT - padB;

    const values = series.flatMap((s) => s.points.map((p) => p.value));
    const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
    const y0 = ticks[0], y1 = ticks[ticks.length - 1];
    const X = (i) => padL + (days.length === 1 ? iw / 2 : (i / (days.length - 1)) * iw);
    const Y = (v) => padT + ih - ((v - y0) / (y1 - y0)) * ih;

    const svg = el("svg", {
      class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H,
      role: "img", "aria-label": label || "多边对比折线",
    });

    for (const t of ticks) {
      svg.append(el("line", { class: "grid-line", x1: padL, x2: W - padR, y1: Y(t), y2: Y(t) }));
      svg.append(el("text", { class: "axis", x: padL - 10, y: Y(t) + 4, "text-anchor": "end" },
        fmtNum(t, y1 - y0 < 6 ? 1 : 0)));
    }

    const step = Math.max(1, Math.ceil(days.length / 6));
    const lastLabel = Math.floor((days.length - 1) / step) * step;
    days.forEach((d, i) => {
      const isLast = i === days.length - 1;
      if (i % step !== 0 && !(isLast && i - lastLabel >= step / 2)) return;
      const anchor = i === 0 ? "start" : i === days.length - 1 ? "end" : "middle";
      svg.append(el("text", { class: "axis", x: X(i), y: H - 8, "text-anchor": anchor }, fmtDay(d)));
    });

    series.forEach((s, si) => {
      const color = s.color || SERIES_COLORS[si % SERIES_COLORS.length];
      const byDay = new Map(s.points.map((p) => [p.day, p.value]));
      // 缺报的日子断开，不连成假线
      let path = "", pen = false;
      days.forEach((day, i) => {
        const v = byDay.get(day);
        if (v === undefined) { pen = false; return; }
        path += `${pen ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`;
        pen = true;
      });
      svg.append(el("path", { class: "series", d: path, stroke: color }));

      const hit = el("rect", {
        class: "hit", x: padL, y: padT, width: iw, height: ih,
      });
      svg.append(hit);

      const cursor = el("circle", { class: "dot", r: 3.5, fill: color, opacity: 0 });
      svg.append(cursor);

      let tip = document.getElementById("tip");
      if (!tip) {
        tip = document.createElement("div");
        tip.id = "tip";
        tip.className = "tip";
        tip.hidden = true;
        document.body.append(tip);
      }
      hit.addEventListener("mousemove", (ev) => {
        const box = svg.getBoundingClientRect();
        const rel = (ev.clientX - box.left - padL) / iw;
        const i = Math.max(0, Math.min(days.length - 1, Math.round(rel * (days.length - 1))));
        const v = byDay.get(days[i]);
        if (v === undefined) { cursor.setAttribute("opacity", 0); tip.hidden = true; return; }
        cursor.setAttribute("cx", X(i));
        cursor.setAttribute("cy", Y(v));
        cursor.setAttribute("opacity", 1);
        tip.hidden = false;
        tip.textContent = `${fmtDay(days[i])} · ${s.name} · ${fmtNum(v, decimals)} ${unit}`;
        tip.style.left = `${Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8)}px`;
        tip.style.top = `${ev.clientY - 34}px`;
      });
      hit.addEventListener("mouseleave", () => {
        cursor.setAttribute("opacity", 0);
        tip.hidden = true;
      });
    });

    return svg;
  });
}

export function barList(host, rows, opts = {}) {
  const { decimals = 0, suffix = "" } = opts;
  if (!rows.length) {
    host.replaceChildren(Object.assign(document.createElement("div"), {
      className: "empty", textContent: "这个周期没有需要单独说明的事件。",
    }));
    return;
  }
  const top = Math.max(...rows.map((r) => r.value)) || 1;
  const wrap = document.createElement("div");
  wrap.className = "list";
  rows.forEach((r) => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `<div class="grow">
        <div class="row-text">${r.label}</div>
        <div class="row-sub">${fmtNum(r.value, decimals)} ${suffix}${r.note ? ` · ${r.note}` : ""}</div>
      </div>
      <div style="width:88px;height:4px;background:#eef0ee;border-radius:2px;overflow:hidden;flex:none">
        <div style="width:${Math.round((r.value / top) * 100)}%;height:100%;background:var(--accent)"></div>
      </div>`;
    wrap.append(row);
  });
  host.replaceChildren(wrap);
}
