/* 云的核心视觉：本机在中心，三个边节点围绕，设备挂在各自的边上。
   节点与连线的位置来自真实拓扑，转速与告警由数据驱动。 */

import { program, buffer, perspective, lookAt, multiply, rotationX, rotationY, animate } from "./glcore.js";

const VS = `#version 300 es
precision highp float;
in vec3 aPos;
in vec3 aColor;
in float aSize;
uniform mat4 uProj;
uniform mat4 uView;
uniform mat4 uModel;
uniform float uTime;
out vec3 vColor;
out float vAlpha;
void main() {
  vec4 world = uModel * vec4(aPos, 1.0);
  vec4 view = uView * world;
  gl_Position = uProj * view;
  float depth = clamp(1.0 - (-view.z - 2.2) / 5.0, 0.0, 1.0);
  gl_PointSize = aSize * (0.75 + 0.6 * depth) * (1.0 + 0.04 * sin(uTime * 2.0 + aPos.x * 4.0));
  vColor = aColor;
  vAlpha = 0.58 + 0.42 * depth;
}`;

const FS = `#version 300 es
precision highp float;
in vec3 vColor;
in float vAlpha;
out vec4 outColor;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  float r = length(d);
  if (r > 0.5) discard;
  float edge = smoothstep(0.5, 0.18, r);
  outColor = vec4(vColor, vAlpha * edge);
}`;

const LINE_VS = `#version 300 es
precision highp float;
in vec3 aPos;
in float aFade;
uniform mat4 uProj;
uniform mat4 uView;
uniform mat4 uModel;
uniform float uTime;
out float vAlpha;
void main() {
  vec4 view = uView * (uModel * vec4(aPos, 1.0));
  gl_Position = uProj * view;
  float depth = clamp(1.0 - (-view.z - 2.2) / 5.0, 0.0, 1.0);
  // 数据沿链路流动：靠近 0 的那端更亮
  float pulse = 0.55 + 0.45 * sin(uTime * 1.6 + aPos.x * 3.0 + aPos.z * 2.0);
  vAlpha = aFade * (0.62 + 0.38 * depth) * (0.75 + 0.25 * pulse);
}`;

const LINE_FS = `#version 300 es
precision highp float;
in float vAlpha;
out vec4 outColor;
void main() { outColor = vec4(0.58, 0.98, 0.72, vAlpha); }`;

const TEAL = [0.52, 0.86, 0.62];
const INK = [0.92, 0.96, 0.88];
const GREY = [0.62, 0.68, 0.63];

export function createConstellation(canvas) {
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: true, premultipliedAlpha: false });
  if (!gl) return fallback(canvas);

  const nodeProg = program(gl, VS, FS);
  const lineProg = program(gl, LINE_VS, LINE_FS);

  const nodes = { pos: [], color: [], size: [] };
  const links = { pos: [], fade: [] };

  const nodeBufs = {
    pos: buffer(gl, new Float32Array(3), gl.DYNAMIC_DRAW),
    color: buffer(gl, new Float32Array(3), gl.DYNAMIC_DRAW),
    size: buffer(gl, new Float32Array(1), gl.DYNAMIC_DRAW),
  };
  const lineBufs = {
    pos: buffer(gl, new Float32Array(3), gl.DYNAMIC_DRAW),
    fade: buffer(gl, new Float32Array(1), gl.DYNAMIC_DRAW),
  };

  const nloc = {
    pos: gl.getAttribLocation(nodeProg, "aPos"),
    color: gl.getAttribLocation(nodeProg, "aColor"),
    size: gl.getAttribLocation(nodeProg, "aSize"),
    proj: gl.getUniformLocation(nodeProg, "uProj"),
    view: gl.getUniformLocation(nodeProg, "uView"),
    model: gl.getUniformLocation(nodeProg, "uModel"),
    time: gl.getUniformLocation(nodeProg, "uTime"),
  };
  const lloc = {
    pos: gl.getAttribLocation(lineProg, "aPos"),
    fade: gl.getAttribLocation(lineProg, "aFade"),
    proj: gl.getUniformLocation(lineProg, "uProj"),
    view: gl.getUniformLocation(lineProg, "uView"),
    model: gl.getUniformLocation(lineProg, "uModel"),
    time: gl.getUniformLocation(lineProg, "uTime"),
  };

  function build({ edges = [], alerts = 0 }) {
    nodes.pos = []; nodes.color = []; nodes.size = [];
    links.pos = []; links.fade = [];

    const push = (p, c, s) => {
      nodes.pos.push(p[0], p[1], p[2]);
      nodes.color.push(c[0], c[1], c[2]);
      nodes.size.push(s);
    };
    const link = (a, b, fade) => {
      // WebGL 的 lineWidth 不可靠：用三条轻微错位的线伪装成 2px 粗细
      for (const dy of [-0.014, 0, 0.014]) {
        links.pos.push(a[0], a[1] + dy, a[2], b[0], b[1] + dy, b[2]);
        links.fade.push(dy ? fade * 0.5 : fade, dy ? fade * 0.5 : fade);
      }
    };

    const center = [0, 0, 0];
    push(center, INK, 30);

    const n = Math.max(1, edges.length);
    edges.forEach((e, i) => {
      const ang = (i / n) * Math.PI * 2 - Math.PI / 2;
      const r = n === 1 ? 0 : 1.15;
      // 三个边放在不同高度，投影后才有纵深，不会压成一条横线
      const hub = [Math.cos(ang) * r, Math.sin(ang) * 0.62, Math.sin(ang) * r * 0.85];
      push(hub, e.online === false ? GREY : TEAL, 22);
      link(center, hub, 0.9);

      const k = Math.max(1, e.devices || 1);
      for (let j = 0; j < k; j++) {
        const a2 = ang + (j / k) * Math.PI * 2 * 0.55 + 0.4;
        const sat = [
          hub[0] + Math.cos(a2) * 0.44,
          hub[1] + Math.sin(a2 * 1.3) * 0.34,
          hub[2] + Math.sin(a2) * 0.40,
        ];
        push(sat, GREY, 14);
        link(hub, sat, 0.5);
      }
    });

    // 只有真实有告警时才让环更亮，避免"一直很兴奋"
    nodes.size[0] = 13 + Math.min(6, alerts * 0.3);

    gl.bindBuffer(gl.ARRAY_BUFFER, nodeBufs.pos);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(nodes.pos), gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, nodeBufs.color);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(nodes.color), gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, nodeBufs.size);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(nodes.size), gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineBufs.pos);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(links.pos), gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineBufs.fade);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(links.fade), gl.DYNAMIC_DRAW);
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    gl.viewport(0, 0, w, h);
    return w / h;
  }

  let aspect = resize();
  const ro = new ResizeObserver(() => { aspect = resize(); });
  ro.observe(canvas);

  const view = lookAt([0, 1.1, 4.1], [0, 0, 0], [0, 1, 0]);
  const fitProj = (a) => {
    const d = 4.1, W = 3.1, H = 1.9;
    const visH = Math.max(H, W / a);
    return perspective(2 * Math.atan(visH / 2 / d), a, 0.1, 20);
  };
  const tilt = rotationX(-0.22);

  gl.clearColor(0, 0, 0, 0);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

  const stop = animate((t) => {
    const a = resize();
    aspect = a;
    const proj = fitProj(a);
    const model = multiply(rotationY(t * 0.11), tilt);
    gl.clear(gl.COLOR_BUFFER_BIT);

    gl.useProgram(lineProg);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineBufs.pos);
    gl.enableVertexAttribArray(lloc.pos);
    gl.vertexAttribPointer(lloc.pos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineBufs.fade);
    gl.enableVertexAttribArray(lloc.fade);
    gl.vertexAttribPointer(lloc.fade, 1, gl.FLOAT, false, 0, 0);
    gl.uniformMatrix4fv(lloc.proj, false, proj);
    gl.uniformMatrix4fv(lloc.view, false, view);
    gl.uniformMatrix4fv(lloc.model, false, model);
    gl.uniform1f(lloc.time, t);
    gl.drawArrays(gl.LINES, 0, links.fade.length);

    gl.useProgram(nodeProg);
    gl.bindBuffer(gl.ARRAY_BUFFER, nodeBufs.pos);
    gl.enableVertexAttribArray(nloc.pos);
    gl.vertexAttribPointer(nloc.pos, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, nodeBufs.color);
    gl.enableVertexAttribArray(nloc.color);
    gl.vertexAttribPointer(nloc.color, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, nodeBufs.size);
    gl.enableVertexAttribArray(nloc.size);
    gl.vertexAttribPointer(nloc.size, 1, gl.FLOAT, false, 0, 0);
    gl.uniformMatrix4fv(nloc.proj, false, proj);
    gl.uniformMatrix4fv(nloc.view, false, view);
    gl.uniformMatrix4fv(nloc.model, false, model);
    gl.uniform1f(nloc.time, t);
    gl.drawArrays(gl.POINTS, 0, nodes.size.length);
  });

  build({});
  return {
    setData: build,
    destroy() { stop(); ro.disconnect(); },
  };
}

function fallback(canvas) {
  const ctx = canvas.getContext("2d");
  let data = { edges: [] };
  const draw = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#12161a";
    ctx.beginPath();
    ctx.arc(w / 2, h / 2, 5, 0, Math.PI * 2);
    ctx.fill();
    const n = Math.max(1, data.edges.length);
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 - Math.PI / 2;
      const x = w / 2 + Math.cos(a) * w * 0.28;
      const y = h / 2 + Math.sin(a) * h * 0.3;
      ctx.strokeStyle = "rgba(20,105,90,0.5)";
      ctx.beginPath();
      ctx.moveTo(w / 2, h / 2);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.fillStyle = "#14695a";
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  };
  const ro = new ResizeObserver(draw);
  ro.observe(canvas);
  draw();
  return {
    setData(d) { data = d || data; draw(); },
    destroy() { ro.disconnect(); },
  };
}
