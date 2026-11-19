export const FRAME_WIDTH = 480;
export const FRAME_HEIGHT = 270;
export const FRAME_INTERVAL = 1 / 24;

export const SCENE_META = {
  translation: {
    label: "平移",
    title: "已知平移",
    subtitle: "dx +14, dy −5 px",
    description: "整幅场景以恒定位移平移，真值在每个块中心精确已知。",
  },
  rotation: {
    label: "旋转",
    title: "已知旋转",
    subtitle: "2.6° · 绕画面中心",
    description: "目标帧由参考帧绕画面中心旋转 2.6° 生成，真值随位置线性变化。",
  },
  occlusion: {
    label: "遮挡",
    title: "独立前景遮挡",
    subtitle: "背景 +10,−3 · 前景 −16,−6",
    description: "前景与背景具有不同运动，部分背景在目标帧中被遮挡并标记为无效真值。",
  },
  textureless: {
    label: "低纹理",
    title: "低纹理区域",
    subtitle: "dx +9, dy +2 px",
    description: "大面积平滑墙面产生孔径问题，估计器应以低置信度而非精确向量响应。",
  },
};

const TAU = Math.PI * 2;

function canvas(width, height) {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  return c;
}

function roundedRect(ctx, x, y, w, h, radius) {
  const r = Math.min(radius, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function seeded(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function drawCity(ctx) {
  const width = FRAME_WIDTH;
  const height = FRAME_HEIGHT;
  const random = seeded(2047);
  const sky = ctx.createLinearGradient(0, 0, 0, height * 0.78);
  sky.addColorStop(0, "#b8c9c4");
  sky.addColorStop(0.55, "#d8d1bd");
  sky.addColorStop(1, "#8b9a91");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, width, height);

  const glow = ctx.createRadialGradient(363, 54, 4, 363, 54, 95);
  glow.addColorStop(0, "rgba(255, 226, 158, .92)");
  glow.addColorStop(0.2, "rgba(255, 215, 138, .48)");
  glow.addColorStop(1, "rgba(255, 215, 138, 0)");
  ctx.fillStyle = glow;
  ctx.fillRect(245, 0, 235, 165);
  ctx.fillStyle = "#f6d38a";
  ctx.beginPath();
  ctx.arc(363, 54, 20, 0, TAU);
  ctx.fill();

  ctx.strokeStyle = "rgba(92, 110, 106, .26)";
  ctx.lineWidth = 2;
  for (let i = 0; i < 5; i += 1) {
    ctx.beginPath();
    ctx.moveTo(35 + i * 98, 48 + i * 6);
    ctx.bezierCurveTo(105 + i * 94, 39 + i * 5, 157 + i * 76, 54 + i * 4, 203 + i * 61, 49 + i * 6);
    ctx.stroke();
  }

  ctx.fillStyle = "#5e756e";
  ctx.beginPath();
  ctx.moveTo(0, 168);
  ctx.lineTo(54, 127);
  ctx.lineTo(98, 153);
  ctx.lineTo(162, 112);
  ctx.lineTo(232, 155);
  ctx.lineTo(298, 124);
  ctx.lineTo(368, 161);
  ctx.lineTo(430, 132);
  ctx.lineTo(480, 160);
  ctx.lineTo(480, 205);
  ctx.lineTo(0, 205);
  ctx.closePath();
  ctx.fill();

  drawSkyline(ctx, random);
  drawRoad(ctx);
}

function drawSkyline(ctx, random) {
  const buildings = [
    { x: -8, y: 94, w: 68, h: 112, c: "#33454a", rows: 5, cols: 3 },
    { x: 52, y: 128, w: 48, h: 78, c: "#405256", rows: 3, cols: 2 },
    { x: 92, y: 70, w: 58, h: 136, c: "#2b3d43", rows: 6, cols: 3 },
    { x: 142, y: 108, w: 43, h: 97, c: "#3d4f52", rows: 4, cols: 2 },
    { x: 179, y: 47, w: 67, h: 158, c: "#263a40", rows: 7, cols: 3 },
    { x: 239, y: 91, w: 54, h: 112, c: "#45565a", rows: 5, cols: 2 },
    { x: 287, y: 127, w: 51, h: 76, c: "#34484c", rows: 3, cols: 2 },
    { x: 330, y: 71, w: 66, h: 132, c: "#293d43", rows: 6, cols: 3 },
    { x: 391, y: 106, w: 49, h: 97, c: "#3b4d51", rows: 4, cols: 2 },
    { x: 433, y: 136, w: 57, h: 67, c: "#30454a", rows: 3, cols: 3 },
  ];
  for (const b of buildings) {
    ctx.fillStyle = b.c;
    ctx.fillRect(b.x, b.y, b.w, b.h);
    ctx.strokeStyle = "rgba(224, 216, 187, 0.1)";
    ctx.lineWidth = 1;
    ctx.strokeRect(b.x + 0.5, b.y + 0.5, b.w - 1, b.h - 1);
    const padX = 7;
    const padY = 10;
    const cellW = (b.w - padX * 2) / b.cols;
    const cellH = (b.h - padY * 2) / b.rows;
    for (let row = 0; row < b.rows; row += 1) {
      for (let col = 0; col < b.cols; col += 1) {
        const light = random() > 0.45;
        ctx.fillStyle = light ? "rgba(238, 190, 104, .8)" : "rgba(15, 29, 35, .52)";
        ctx.fillRect(b.x + padX + col * cellW + 1, b.y + padY + row * cellH + 1, Math.max(3, cellW - 4), Math.max(4, cellH - 5));
      }
    }
  }
  ctx.fillStyle = "#22363c";
  ctx.fillRect(204, 25, 3, 42);
  ctx.fillRect(204, 25, 31, 2);
  ctx.strokeStyle = "#22363c";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(234, 27);
  ctx.lineTo(234, 55);
  ctx.stroke();
}

function drawRoad(ctx) {
  const road = ctx.createLinearGradient(0, 192, 0, FRAME_HEIGHT);
  road.addColorStop(0, "#394346");
  road.addColorStop(1, "#202b2e");
  ctx.fillStyle = road;
  ctx.fillRect(0, 193, FRAME_WIDTH, FRAME_HEIGHT - 193);

  ctx.fillStyle = "#a9a38d";
  ctx.beginPath();
  ctx.moveTo(0, 233);
  ctx.lineTo(FRAME_WIDTH, 205);
  ctx.lineTo(FRAME_WIDTH, 210);
  ctx.lineTo(0, 238);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#6b756e";
  ctx.fillRect(0, 260, FRAME_WIDTH, 10);

  ctx.strokeStyle = "rgba(212, 203, 174, .16)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 13; i += 1) {
    const x = i * 44 + 8;
    ctx.beginPath();
    ctx.moveTo(x, 270);
    ctx.lineTo(x + 82, 198);
    ctx.stroke();
  }
  for (let i = 0; i < 4; i += 1) {
    ctx.fillStyle = "rgba(225, 213, 176, .28)";
    ctx.fillRect(18 + i * 128, 243 - i * 3, 52, 2);
  }
}

function drawObject(ctx, x, y) {
  const w = 96;
  const h = 61;
  ctx.save();
  ctx.shadowColor = "rgba(0, 0, 0, .34)";
  ctx.shadowBlur = 8;
  ctx.shadowOffsetY = 5;
  roundedRect(ctx, x - w / 2, y - h / 2, w, h, 7);
  ctx.fillStyle = "#bd6434";
  ctx.fill();
  ctx.restore();

  roundedRect(ctx, x - w / 2, y - h / 2, w, h, 7);
  const body = ctx.createLinearGradient(x, y - h / 2, x, y + h / 2);
  body.addColorStop(0, "#d78042");
  body.addColorStop(1, "#944724");
  ctx.fillStyle = body;
  ctx.fill();
  ctx.strokeStyle = "#562d21";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.fillStyle = "#202c2e";
  roundedRect(ctx, x - 34, y - 24, 32, 22, 3);
  ctx.fill();
  ctx.strokeStyle = "#a98450";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = "rgba(129, 181, 173, .38)";
  ctx.fillRect(x - 31, y - 21, 26, 12);
  ctx.fillStyle = "#e4b35e";
  ctx.fillRect(x + 3, y - 20, 32, 3);
  ctx.fillStyle = "#2b3839";
  ctx.fillRect(x - 38, y + 12, 78, 8);
  ctx.fillStyle = "#c6f2e3";
  ctx.fillRect(x - 25, y + 14, 25, 3);
  ctx.fillStyle = "#161e20";
  ctx.beginPath();
  ctx.arc(x - 28, y + 25, 9, 0, TAU);
  ctx.arc(x + 27, y + 25, 9, 0, TAU);
  ctx.fill();
  ctx.strokeStyle = "#8b917f";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(x - 28, y + 25, 4, 0, TAU);
  ctx.arc(x + 27, y + 25, 4, 0, TAU);
  ctx.stroke();

  ctx.strokeStyle = "rgba(255, 204, 138, .28)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    ctx.beginPath();
    ctx.moveTo(x - 39, y - 18 + i * 6);
    ctx.lineTo(x - 41, y - 15 + i * 6);
    ctx.stroke();
  }
}

function drawTexturelessPlate(ctx) {
  const wall = ctx.createLinearGradient(0, 0, FRAME_WIDTH, FRAME_HEIGHT);
  wall.addColorStop(0, "#697c79");
  wall.addColorStop(0.42, "#95a29a");
  wall.addColorStop(0.7, "#9ba69a");
  wall.addColorStop(1, "#657471");
  ctx.fillStyle = wall;
  ctx.fillRect(0, 0, FRAME_WIDTH, FRAME_HEIGHT);

  const glow = ctx.createRadialGradient(353, 80, 2, 353, 80, 130);
  glow.addColorStop(0, "rgba(236, 221, 177, .23)");
  glow.addColorStop(1, "rgba(236, 221, 177, 0)");
  ctx.fillStyle = glow;
  ctx.fillRect(170, 0, 310, 220);

  ctx.fillStyle = "rgba(37, 55, 57, .3)";
  ctx.fillRect(0, 199, FRAME_WIDTH, 71);
  ctx.fillStyle = "rgba(220, 220, 193, .22)";
  ctx.fillRect(0, 201, FRAME_WIDTH, 2);
  ctx.strokeStyle = "rgba(51, 68, 68, .2)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 7; i += 1) {
    ctx.beginPath();
    ctx.moveTo(i * 80, 270);
    ctx.lineTo(i * 65 + 145, 199);
    ctx.stroke();
  }

  ctx.fillStyle = "#394d4e";
  roundedRect(ctx, 48, 51, 73, 65, 3);
  ctx.fill();
  ctx.strokeStyle = "rgba(239, 219, 173, .36)";
  ctx.strokeRect(56.5, 60.5, 56, 47);
  ctx.fillStyle = "#e1bb71";
  ctx.beginPath();
  ctx.arc(404, 149, 12, 0, TAU);
  ctx.fill();
  ctx.fillStyle = "#344849";
  ctx.fillRect(362, 183, 86, 4);
  ctx.fillStyle = "#d3d3b8";
  ctx.fillRect(410, 42, 33, 13);
  ctx.fillStyle = "#263a3c";
  ctx.fillRect(414, 46, 25, 3);
}

function createBase(sceneId) {
  const base = canvas(FRAME_WIDTH, FRAME_HEIGHT);
  const ctx = base.getContext("2d", { willReadFrequently: true });
  if (sceneId === "textureless") drawTexturelessPlate(ctx);
  else drawCity(ctx);
  return base;
}

function copyFrame(source) {
  const target = canvas(FRAME_WIDTH, FRAME_HEIGHT);
  target.getContext("2d").drawImage(source, 0, 0);
  return target;
}

function shiftedFrame(source, dx, dy) {
  const target = canvas(FRAME_WIDTH, FRAME_HEIGHT);
  const ctx = target.getContext("2d");
  ctx.fillStyle = "#1a262a";
  ctx.fillRect(0, 0, FRAME_WIDTH, FRAME_HEIGHT);
  ctx.drawImage(source, dx, dy);
  return target;
}

function rotateFrame(source, degrees) {
  const target = canvas(FRAME_WIDTH, FRAME_HEIGHT);
  const ctx = target.getContext("2d");
  ctx.fillStyle = "#1a262a";
  ctx.fillRect(0, 0, FRAME_WIDTH, FRAME_HEIGHT);
  ctx.translate(FRAME_WIDTH / 2, FRAME_HEIGHT / 2);
  ctx.rotate((degrees * Math.PI) / 180);
  ctx.drawImage(source, -FRAME_WIDTH / 2, -FRAME_HEIGHT / 2);
  return target;
}

export function buildScenePair(sceneId) {
  const base = createBase(sceneId);
  if (sceneId === "rotation") {
    return {
      id: sceneId,
      frameA: copyFrame(base),
      frameB: rotateFrame(base, 2.6),
      gt: { kind: "rotation", angle: 2.6, cx: FRAME_WIDTH / 2, cy: FRAME_HEIGHT / 2 },
    };
  }

  if (sceneId === "textureless") {
    return {
      id: sceneId,
      frameA: copyFrame(base),
      frameB: shiftedFrame(base, 9, 2),
      gt: { kind: "translation", dx: 9, dy: 2 },
    };
  }

  if (sceneId === "occlusion") {
    const bgDx = 10;
    const bgDy = -3;
    const fgDx = -16;
    const fgDy = -6;
    const objectA = { x: 282, y: 155 };
    const objectB = { x: objectA.x + fgDx, y: objectA.y + fgDy };
    const frameA = copyFrame(base);
    drawObject(frameA.getContext("2d"), objectA.x, objectA.y);
    const frameB = shiftedFrame(base, bgDx, bgDy);
    drawObject(frameB.getContext("2d"), objectB.x, objectB.y);
    return {
      id: sceneId,
      frameA,
      frameB,
      gt: {
        kind: "occlusion",
        bg: { dx: bgDx, dy: bgDy },
        fg: { dx: fgDx, dy: fgDy },
        objectA: { x: objectA.x - 48, y: objectA.y - 30.5, w: 96, h: 61 },
        objectB: { x: objectB.x - 48, y: objectB.y - 30.5, w: 96, h: 61 },
      },
    };
  }

  return {
    id: "translation",
    frameA: copyFrame(base),
    frameB: shiftedFrame(base, 14, -5),
    gt: { kind: "translation", dx: 14, dy: -5 },
  };
}

