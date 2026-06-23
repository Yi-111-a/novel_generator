import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import type { GraphData, GraphNode, GraphEdgeViz } from '../types';

/* ── 配色 ── */
const TYPE_COLOR: Record<string, string> = {
  character: '#60a5fa', faction: '#f59e0b', location: '#34d399', item: '#c084fc', object: '#c084fc',
};
const TYPE_LABEL: Record<string, string> = {
  character: '人物', faction: '势力', location: '地点', item: '道具', object: '道具',
};
const REL_COLOR: Record<string, string> = {
  hostile: '#ef4444', allied: '#22c55e', controls: '#f59e0b', infiltrates: '#e879f9',
  member_of: '#64748b', has_member: '#64748b', located_in: '#475569',
  knows: '#94a3b8', related_to: '#94a3b8',
};
const REL_LABEL: Record<string, string> = {
  hostile: '敌对', allied: '结盟', controls: '控制', infiltrates: '渗透',
  member_of: '从属', has_member: '统属', located_in: '位于',
  knows: '相识', related_to: '关联',
};

const TIER: Record<string, 'primary' | 'structural' | 'weak'> = {
  hostile: 'primary', allied: 'primary', controls: 'primary', infiltrates: 'primary',
  member_of: 'structural', has_member: 'structural', located_in: 'structural',
  knows: 'weak', related_to: 'weak',
};
const PRIMARY_RELS = ['hostile', 'allied', 'controls', 'infiltrates'];
const STRUCTURAL_RELS = ['member_of', 'has_member', 'located_in'];
const WEAK_RELS = ['knows', 'related_to'];

const LINK_DIST: Record<string, number> = {
  member_of: 72, has_member: 72, located_in: 58, controls: 130,
  hostile: 260, allied: 210, infiltrates: 220, knows: 170, related_to: 110,
};
const LINK_STR: Record<string, number> = {
  member_of: 0.25, has_member: 0.25, located_in: 0.7, controls: 0.12,
  hostile: 0.015, allied: 0.03, infiltrates: 0.015, knows: 0.005, related_to: 0.03,
};

/* ── 模拟节点扩展 ── */
interface SimNode extends GraphNode {
  x: number; y: number; vx: number; vy: number;
  fx: number | null; fy: number | null;
  degree: number;
  factionId?: string;
  parentLoc?: string;
  clusterTarget: SimNode | null;
  anchor: [number, number];
  anchorStrength: number;
}

/* ── 极简力导向模拟（零依赖替代 d3-force） ── */
function chargeStrength(n: SimNode): number {
  if (n.type === 'faction') return -800;
  if (n.type === 'location') return -250;
  if (n.type === 'item' || n.type === 'object') return -150;
  return -300;
}

function runSimTick(nodes: SimNode[], edges: GraphEdgeViz[], alpha: number,
                    byId: Record<string, SimNode>) {
  // 斥力 — 使用 1/d（非 1/d²），匹配 d3-force 的 forceManyBody
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1) { dx = (Math.random() - 0.5) * 2; dy = (Math.random() - 0.5) * 2; d = Math.sqrt(dx * dx + dy * dy); }
      const str = (chargeStrength(a) + chargeStrength(b)) * 0.5;
      const f = str / d * alpha;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx -= fx; a.vy -= fy;
      b.vx += fx; b.vy += fy;
    }
  }

  // 弹簧力（link）
  for (const e of edges) {
    const s = byId[e.src], t = byId[e.dst];
    if (!s || !t) continue;
    const dx = t.x - s.x, dy = t.y - s.y;
    const d = Math.sqrt(dx * dx + dy * dy) + 0.1;
    const targetDist = LINK_DIST[e.rel] ?? 130;
    const strength = LINK_STR[e.rel] ?? 0.04;
    const f = (d - targetDist) * strength * alpha;
    const fx = (dx / d) * f, fy = (dy / d) * f;
    s.vx += fx; s.vy += fy;
    t.vx -= fx; t.vy -= fy;
  }

  // cluster force — 成员拉向势力质心（不乘 alpha）
  for (const n of nodes) {
    const ct = n.clusterTarget;
    if (!ct) continue;
    n.vx += (ct.x - n.x) * 0.06;
    n.vy += (ct.y - n.y) * 0.06;
  }

  // 锚点力（不乘 alpha，保证锚点持续生效）
  for (const n of nodes) {
    n.vx += (n.anchor[0] - n.x) * n.anchorStrength;
    n.vy += (n.anchor[1] - n.y) * n.anchorStrength;
  }

  // 碰撞
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const minD = nodeRadius(a) + nodeRadius(b) + 12;
      if (d < minD) {
        const push = (minD - d) * 0.4;
        const px = (dx / d) * push, py = (dy / d) * push;
        a.vx -= px; a.vy -= py;
        b.vx += px; b.vy += py;
      }
    }
  }

  // 积分：阻尼 + 速度钳制（一次性，不分轴）
  const vMax = 30;
  const damping = 0.55;
  for (const n of nodes) {
    if (n.fx !== null && n.fy !== null) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; continue; }
    n.vx *= damping;
    n.vy *= damping;
    const v = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
    if (v > vMax) { const s = vMax / v; n.vx *= s; n.vy *= s; }
    if (n.fx !== null) { n.x = n.fx; n.vx = 0; } else { n.x += n.vx; }
    if (n.fy !== null) { n.y = n.fy; n.vy = 0; } else { n.y += n.vy; }
  }
}

function nodeRadius(n: SimNode | GraphNode): number {
  const d = (n as SimNode).degree ?? 0;
  if (n.type === 'faction') return 16;
  if (n.type === 'location') return (n as SimNode).parentLoc ? 6.5 : 9;
  if (n.type === 'item' || n.type === 'object') return 5;
  return Math.min(11, 6 + d * 0.35);
}

/* ── 凸包（Andrew's monotone chain） ── */
function convexHull(points: [number, number][]): [number, number][] {
  if (points.length < 3) return points.slice();
  const pts = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o: [number, number], a: [number, number], b: [number, number]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0)
      lower.pop();
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], pts[i]) <= 0)
      upper.pop();
    upper.push(pts[i]);
  }
  lower.pop(); upper.pop();
  return lower.concat(upper);
}

/* ── Canvas 绘制辅助 ── */
function withAlpha(hex: string, a: number): string {
  const v = Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0');
  return hex + v;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/* ── 主组件 ── */
export function KnowledgeGraph({ data }: { data: GraphData }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<SimNode[]>([]);
  const byIdRef = useRef<Record<string, SimNode>>({});
  const alphaRef = useRef(1);
  const alphaTargetRef = useRef(0);
  const tRef = useRef({ x: 0, y: 0, k: 0.72 });
  const hoverRef = useRef<string | null>(null);
  const pointerRef = useRef({ down: false, moved: 0, dragging: false, dragNode: null as SimNode | null, sx: 0, sy: 0 });
  const uiRef = useRef<any>({});

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ id: string; depth: 1 | 2 } | null>(null);
  const [rels, setRels] = useState<Set<string>>(new Set(PRIMARY_RELS));
  const [types, setTypes] = useState<Set<string>>(new Set(['character', 'faction', 'location', 'item', 'object']));
  const [minIntensity, setMinIntensity] = useState(0);
  const [graphMode, setGraphMode] = useState<'current' | 'all'>('current');

  const maxChapter = useMemo(() => {
    return data.edges.reduce((m, e) => Math.max(m, e.lastActiveChapter ?? e.sinceChapter ?? 0), 0);
  }, [data.edges]);
  const [chapterRange, setChapterRange] = useState(0);

  const adjacency = useMemo(() => {
    const adj = new Map<string, string[]>();
    for (const n of data.nodes) adj.set(n.id, []);
    for (const e of data.edges) {
      adj.get(e.src)?.push(e.dst);
      adj.get(e.dst)?.push(e.src);
    }
    return adj;
  }, [data]);

  const focusSet = useMemo(() => {
    if (!focus) return null;
    const set = new Set([focus.id]);
    let frontier = [focus.id];
    for (let d = 0; d < focus.depth; d++) {
      const next: string[] = [];
      for (const id of frontier) for (const nb of adjacency.get(id) || []) {
        if (!set.has(nb)) { set.add(nb); next.push(nb); }
      }
      frontier = next;
    }
    return set;
  }, [focus, adjacency]);

  uiRef.current = { selectedId, focus, focusSet, rels, types, minIntensity, graphMode, chapterRange };

  useEffect(() => {
    setChapterRange(maxChapter);
  }, [maxChapter]);

  /* ── 初始化力模拟 ── */
  useEffect(() => {
    const byId: Record<string, SimNode> = {};
    const nodes: SimNode[] = data.nodes.map(n => ({
      ...n, x: 0, y: 0, vx: 0, vy: 0, fx: null, fy: null,
      degree: 0, clusterTarget: null, anchor: [0, 0] as [number, number], anchorStrength: 0.012,
    }));
    for (const n of nodes) byId[n.id] = n;

    // 从边推导关系：member_of → factionId，located_in → parentLoc
    const factionIds = new Set(nodes.filter(n => n.type === 'faction').map(n => n.id));
    const locationIds = new Set(nodes.filter(n => n.type === 'location').map(n => n.id));
    for (const e of data.edges) {
      if (e.rel === 'member_of' && byId[e.src] && factionIds.has(e.dst)) {
        byId[e.src].factionId = e.dst;
      }
      if (e.rel === 'has_member' && byId[e.dst] && factionIds.has(e.src)) {
        byId[e.dst].factionId = e.src;
      }
      if (e.rel === 'located_in' && byId[e.src] && locationIds.has(e.dst)) {
        byId[e.src].parentLoc = e.dst;
      }
    }

    // 度数（只算核心+弱边）
    for (const e of data.edges) {
      if ((TIER[e.rel] ?? 'weak') === 'structural') continue;
      if (byId[e.src]) byId[e.src].degree++;
      if (byId[e.dst]) byId[e.dst].degree++;
    }

    // 势力锚点：按势力数量均分象限
    const factions = nodes.filter(n => n.type === 'faction');
    const facAnchors: [number, number][] = [[-300, -190], [300, -190], [-340, 120], [340, 120]];
    factions.forEach((f, i) => {
      f.anchor = facAnchors[i % facAnchors.length];
      f.anchorStrength = 0.18;
    });

    // 顶层地点（无 parentLoc 的 location）锚在底部一字排开
    const rootLocs = nodes.filter(n => n.type === 'location' && !n.parentLoc);
    rootLocs.forEach((n, i) => {
      n.anchor = [-330 + (660 / Math.max(1, rootLocs.length - 1)) * i, 360];
      n.anchorStrength = 0.09;
    });

    // cluster 目标：成员→势力，道具/object→关联 character 的势力
    for (const n of nodes) {
      if (n.factionId && byId[n.factionId]) n.clusterTarget = byId[n.factionId];
    }
    // object/item: 找到关联的 character，继承其势力
    for (const n of nodes) {
      if ((n.type === 'item' || n.type === 'object') && !n.clusterTarget) {
        for (const e of data.edges) {
          if (e.src !== n.id && e.dst !== n.id) continue;
          const otherId = e.src === n.id ? e.dst : e.src;
          const other = byId[otherId];
          if (other?.type === 'character') {
            n.clusterTarget = other.clusterTarget || other;
            break;
          }
        }
      }
    }

    // 孤儿节点（无边）：散布在画布周边，不堆在中心
    const connectedIds = new Set<string>();
    for (const e of data.edges) { connectedIds.add(e.src); connectedIds.add(e.dst); }
    let orphanIdx = 0;
    for (const n of nodes) {
      if (!connectedIds.has(n.id) && n.anchor[0] === 0 && n.anchor[1] === 0) {
        const angle = (orphanIdx / 8) * Math.PI * 2;
        n.anchor = [Math.cos(angle) * 420, Math.sin(angle) * 300] as [number, number];
        n.anchorStrength = 0.03;
        orphanIdx++;
      }
    }

    // 初始位置：用锚点/cluster target 的锚点确定起始区域
    for (const n of nodes) {
      let base: [number, number];
      if (n.clusterTarget) {
        base = n.clusterTarget.anchor;
      } else {
        base = n.anchor;
      }
      n.x = base[0] + (Math.random() - 0.5) * 200;
      n.y = base[1] + (Math.random() - 0.5) * 200;
    }

    nodesRef.current = nodes;
    byIdRef.current = byId;

    // 预热 200 帧：在首次渲染前让布局收敛，避免用户看到震荡
    let warmAlpha = 1;
    for (let i = 0; i < 200; i++) {
      runSimTick(nodes, data.edges, warmAlpha, byId);
      warmAlpha *= 0.97;
    }
    alphaRef.current = 0;
    alphaTargetRef.current = 0;
  }, [data]);

  /* ── 渲染循环 ── */
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext('2d')!;
    let localRaf = 0;
    let disposed = false;
    const resize = () => {
      if (container.clientWidth === 0) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = container.clientWidth * dpr;
      canvas.height = container.clientHeight * dpr;
      canvas.style.width = container.clientWidth + 'px';
      canvas.style.height = container.clientHeight + 'px';
      if (tRef.current.x === 0 && tRef.current.y === 0)
        tRef.current = { x: container.clientWidth / 2, y: container.clientHeight / 2 - 20, k: 0.72 };
    };
    const ro = new ResizeObserver(resize);
    ro.observe(container);
    resize();

    const isEdgeVisible = (e: GraphEdgeViz, ui: any, hoverId: string | null): boolean => {
      const tier = TIER[e.rel] ?? 'weak';
      if (ui.focusSet) return ui.focusSet.has(e.src) && ui.focusSet.has(e.dst);
      if (hoverId && (e.src === hoverId || e.dst === hoverId)) return true;
      if (ui.selectedId && (e.src === ui.selectedId || e.dst === ui.selectedId)) return true;
      if (ui.graphMode === 'current') {
        const since = e.sinceChapter ?? 0;
        const last = e.lastActiveChapter ?? since;
        if (since > ui.chapterRange || last > ui.chapterRange) return false;
      }
      if (tier !== 'structural' && e.intensity < ui.minIntensity) return false;
      if (!ui.rels.has(e.rel)) return false;
      const byId = byIdRef.current;
      return ui.types.has(byId[e.src]?.type) && ui.types.has(byId[e.dst]?.type);
    };

    const draw = () => {
      if (disposed) return;
      const alpha = alphaRef.current;
      const nodes = nodesRef.current;
      const byId = byIdRef.current;
      const ui = uiRef.current;
      const hoverId = hoverRef.current;
      const edges = data.edges;

      // 修复 canvas 尺寸（ResizeObserver 首次可能拿到 0）
      if (canvas.width === 0 && container.clientWidth > 0) resize();

      // 物理 tick — alphaTarget 模型（同 d3-force）
      const alphaDecay = 0.0228;
      const alphaMin = 0.001;
      let a = alpha + (alphaTargetRef.current - alpha) * alphaDecay;
      if (a < alphaMin && alphaTargetRef.current === 0) a = 0;
      alphaRef.current = a;
      if (a > alphaMin) {
        runSimTick(nodes, edges, a, byId);
      }

      const dpr = window.devicePixelRatio || 1;
      const W = canvas.width / dpr, H = canvas.height / dpr;
      const { x, y, k } = tRef.current;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = '#0a0a0a';
      ctx.fillRect(0, 0, W, H);
      ctx.translate(x, y);
      ctx.scale(k, k);

      // hover 邻居集
      let hoverNbs: Set<string> | null = null;
      if (hoverId && !ui.focusSet) {
        hoverNbs = new Set([hoverId, ...(adjacency.get(hoverId) || [])]);
      }
      const nodeAlpha = (n: SimNode): number => {
        if (!ui.types.has(n.type)) return 0;
        if (ui.focusSet) return ui.focusSet.has(n.id) ? 1 : 0.07;
        if (hoverNbs) return hoverNbs.has(n.id) ? 1 : 0.22;
        return 1;
      };

      /* 势力凸包 */
      for (const f of nodes) {
        if (f.type !== 'faction') continue;
        const pts: [number, number][] = nodes
          .filter(n => n.id === f.id || n.factionId === f.id)
          .map(n => [n.x, n.y]);
        if (pts.length < 2) continue;
        const hull = pts.length >= 3 ? convexHull(pts) : pts;
        if (!hull.length) continue;
        const dim = ui.focusSet && !ui.focusSet.has(f.id);
        ctx.beginPath();
        hull.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
        ctx.closePath();
        ctx.lineJoin = 'round';
        ctx.lineWidth = 58;
        ctx.strokeStyle = withAlpha(TYPE_COLOR.faction, dim ? 0.02 : 0.06);
        ctx.fillStyle = withAlpha(TYPE_COLOR.faction, dim ? 0.015 : 0.045);
        ctx.stroke();
        ctx.fill();
      }

      /* 边 */
      const order: Record<string, number> = { structural: 0, weak: 1, primary: 2 };
      const visEdges = edges
        .filter(e => isEdgeVisible(e, ui, hoverId))
        .sort((a, b) => (order[TIER[a.rel] ?? 'weak'] ?? 1) - (order[TIER[b.rel] ?? 'weak'] ?? 1));

      for (const e of visEdges) {
        const s = byId[e.src], t = byId[e.dst];
        if (!s || !t) continue;
        const tier = TIER[e.rel] ?? 'weak';
        let edgeAlpha = tier === 'primary' ? 0.28 + e.intensity * 0.45 : tier === 'structural' ? 0.18 : 0.16 + e.intensity * 0.2;
        const incident = hoverId === e.src || hoverId === e.dst || ui.selectedId === e.src || ui.selectedId === e.dst;
        if (incident) edgeAlpha = Math.min(0.95, edgeAlpha + 0.45);
        else if (hoverNbs) edgeAlpha *= 0.3;
        edgeAlpha *= Math.min(nodeAlpha(s) + 0.1, 1) * Math.min(nodeAlpha(t) + 0.1, 1);
        if (edgeAlpha < 0.01) continue;

        ctx.beginPath();
        const mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2;
        const dx = t.x - s.x, dy = t.y - s.y;
        const len = Math.hypot(dx, dy) || 1;
        ctx.moveTo(s.x, s.y);
        ctx.quadraticCurveTo(mx - dy / len * len * 0.08, my + dx / len * len * 0.08, t.x, t.y);
        ctx.strokeStyle = withAlpha(REL_COLOR[e.rel] || '#555', edgeAlpha);
        ctx.lineWidth = tier === 'primary' ? 0.6 + e.intensity * 2.2 : tier === 'weak' ? 0.7 : 0.9;
        ctx.setLineDash(e.rel === 'infiltrates' ? [7, 5] : tier === 'structural' ? [2, 4] : []);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      /* 节点 */
      for (const n of nodes) {
        const a = nodeAlpha(n);
        if (a <= 0) continue;
        const r = nodeRadius(n);
        const color = TYPE_COLOR[n.type] || '#888';
        ctx.globalAlpha = a;
        ctx.beginPath();
        if (n.type === 'location') {
          const s2 = r * 1.7;
          roundRect(ctx, n.x - s2 / 2, n.y - s2 / 2, s2, s2, 3);
        } else if (n.type === 'item' || n.type === 'object') {
          ctx.moveTo(n.x, n.y - r * 1.3); ctx.lineTo(n.x + r * 1.3, n.y);
          ctx.lineTo(n.x, n.y + r * 1.3); ctx.lineTo(n.x - r * 1.3, n.y); ctx.closePath();
        } else {
          ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        }
        ctx.fillStyle = color;
        ctx.fill();
        if (n.type === 'faction') {
          ctx.beginPath(); ctx.arc(n.x, n.y, r + 4.5, 0, Math.PI * 2);
          ctx.strokeStyle = withAlpha(color, 0.5); ctx.lineWidth = 1.5; ctx.stroke();
        }
        if (n.id === ui.selectedId || (ui.focusSet && n.id === ui.focus?.id)) {
          ctx.beginPath(); ctx.arc(n.x, n.y, r + 7, 0, Math.PI * 2);
          ctx.strokeStyle = '#e4e4e7'; ctx.lineWidth = 1.6; ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }

      /* 标签（语义缩放） */
      const fs = (base: number) => base / Math.min(Math.max(k, 0.7), 1.5);
      for (const n of nodes) {
        const a = nodeAlpha(n);
        if (a < 0.3) continue;
        const inFocus = ui.focusSet && ui.focusSet.has(n.id);
        const emphasized = n.id === hoverId || n.id === ui.selectedId || inFocus;
        let show = false;
        let size = fs(10.5);
        let weight = '400';
        if (n.type === 'faction') { show = true; size = fs(13.5); weight = '700'; }
        else if (n.type === 'location' && !n.parentLoc) show = k > 0.5 || !!emphasized;
        else if (n.degree >= 6) show = k > 0.6 || !!emphasized;
        else show = k > 0.9 || !!emphasized;
        if (!show) continue;
        ctx.font = `${weight} ${size}px ui-sans-serif, system-ui, "PingFang SC", sans-serif`;
        ctx.textAlign = 'center';
        ctx.globalAlpha = a;
        const ly = n.y + nodeRadius(n) + size + 2;
        ctx.lineWidth = 3; ctx.strokeStyle = '#0a0a0a';
        ctx.strokeText(n.name, n.x, ly);
        ctx.fillStyle = emphasized ? '#fafafa' : '#a1a1aa';
        ctx.fillText(n.name, n.x, ly);
        ctx.globalAlpha = 1;
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      /* hover 提示 */
      if (hoverId && byId[hoverId]) {
        const n = byId[hoverId];
        const sx = n.x * k + x, sy = n.y * k + y;
        const text = `${n.name} · ${TYPE_LABEL[n.type] || n.type}${n.degree ? ` · ${n.degree} 条关系` : ''}`;
        ctx.font = '11px ui-sans-serif, system-ui, "PingFang SC", sans-serif';
        const tw = ctx.measureText(text).width;
        ctx.beginPath();
        roundRect(ctx, sx - tw / 2 - 8, sy - nodeRadius(n) * k - 32, tw + 16, 22, 5);
        ctx.fillStyle = '#18181bee';
        ctx.fill();
        ctx.fillStyle = '#e4e4e7';
        ctx.textAlign = 'center';
        ctx.fillText(text, sx, sy - nodeRadius(n) * k - 17);
      }

      localRaf = requestAnimationFrame(draw);
    };
    localRaf = requestAnimationFrame(draw);
    return () => { disposed = true; cancelAnimationFrame(localRaf); ro.disconnect(); };
  }, [data, adjacency]);

  /* ── 交互 ── */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const toWorld = (mx: number, my: number): [number, number] => {
      const { x, y, k } = tRef.current;
      return [(mx - x) / k, (my - y) / k];
    };
    const hitTest = (mx: number, my: number): SimNode | null => {
      const [wx, wy] = toWorld(mx, my);
      const nodes = nodesRef.current;
      const ui = uiRef.current;
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if (!ui.types.has(n.type)) continue;
        const r = nodeRadius(n) + 4;
        if ((n.x - wx) ** 2 + (n.y - wy) ** 2 < r * r) return n;
      }
      return null;
    };

    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const t = tRef.current;
      const k2 = Math.max(0.25, Math.min(4, t.k * Math.exp(-ev.deltaY * 0.0016)));
      const [wx, wy] = toWorld(mx, my);
      tRef.current = { k: k2, x: mx - wx * k2, y: my - wy * k2 };
    };
    const DRAG_THRESHOLD = 5;
    const onDown = (ev: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const n = hitTest(mx, my);
      pointerRef.current = { down: true, moved: 0, dragging: false, dragNode: n, sx: mx, sy: my };
      canvas.setPointerCapture(ev.pointerId);
    };
    const onMove = (ev: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const p = pointerRef.current;
      if (p.down) {
        p.moved += Math.abs(mx - p.sx) + Math.abs(my - p.sy);
        if (p.dragNode) {
          if (!p.dragging && p.moved >= DRAG_THRESHOLD) {
            p.dragging = true;
            p.dragNode.fx = p.dragNode.x;
            p.dragNode.fy = p.dragNode.y;
            alphaTargetRef.current = 0.3;
            alphaRef.current = Math.max(alphaRef.current, 0.1);
          }
          if (p.dragging) {
            const [wx, wy] = toWorld(mx, my);
            p.dragNode.fx = wx; p.dragNode.fy = wy;
          }
        } else {
          tRef.current = { ...tRef.current, x: tRef.current.x + mx - p.sx, y: tRef.current.y + my - p.sy };
        }
        p.sx = mx; p.sy = my;
      } else {
        const n = hitTest(mx, my);
        hoverRef.current = n ? n.id : null;
        canvas.style.cursor = n ? 'pointer' : 'grab';
      }
    };
    const onUp = () => {
      const p = pointerRef.current;
      if (p.dragNode && p.dragging) {
        p.dragNode.fx = null; p.dragNode.fy = null;
        alphaTargetRef.current = 0;
      }
      if (p.moved < DRAG_THRESHOLD && p.dragNode) setSelectedId(p.dragNode.id);
      else if (p.moved < DRAG_THRESHOLD) { setSelectedId(null); setFocus(null); }
      pointerRef.current = { down: false, moved: 0, dragging: false, dragNode: null, sx: 0, sy: 0 };
    };
    const onDbl = (ev: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const n = hitTest(ev.clientX - rect.left, ev.clientY - rect.top);
      if (n) setFocus(f => (f && f.id === n.id ? null : { id: n.id, depth: (f?.depth || 1) as 1 | 2 }));
    };
    const onKey = (ev: KeyboardEvent) => { if (ev.key === 'Escape') { setFocus(null); setSelectedId(null); } };

    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('dblclick', onDbl);
    window.addEventListener('keydown', onKey);
    return () => {
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('dblclick', onDbl);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  /* ── 筛选操作 ── */
  const toggleRel = useCallback((r: string) => setRels(s => {
    const n = new Set(s); n.has(r) ? n.delete(r) : n.add(r); return n;
  }), []);
  const toggleGroup = useCallback((group: string[]) => setRels(s => {
    const n = new Set(s);
    const allOn = group.every(r => n.has(r));
    group.forEach(r => (allOn ? n.delete(r) : n.add(r)));
    return n;
  }), []);
  const toggleType = useCallback((t: string) => setTypes(s => {
    const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n;
  }), []);
  const resetView = useCallback(() => {
    const c = containerRef.current;
    if (c) tRef.current = { x: c.clientWidth / 2, y: c.clientHeight / 2 - 20, k: 0.72 };
    setFocus(null); setSelectedId(null);
  }, []);

  const selected = selectedId ? byIdRef.current[selectedId] : null;
  const selectedEdges = useMemo(() => {
    if (!selectedId) return [];
    return data.edges
      .filter(e => e.src === selectedId || e.dst === selectedId)
      .sort((a, b) => {
        const ta = TIER[a.rel] === 'primary' ? 0 : 1;
        const tb = TIER[b.rel] === 'primary' ? 0 : 1;
        return ta - tb || b.intensity - a.intensity;
      });
  }, [selectedId, data.edges]);

  return (
    <div ref={containerRef} className="relative w-full overflow-hidden rounded-lg border border-zinc-800"
      style={{ height: 'calc(100vh - 180px)', minHeight: 500, background: '#0a0a0a' }}>
      <canvas ref={canvasRef} style={{ display: 'block', cursor: 'grab' }} />

      {/* 筛选条 */}
      <div className="absolute top-3 left-3 right-3 flex flex-wrap items-center gap-1.5 rounded-xl border border-zinc-800 px-3 py-2"
        style={{ background: '#0f0f11d9', backdropFilter: 'blur(8px)', pointerEvents: 'auto' }}>
        <span className="text-[11px] text-zinc-600 mr-0.5">关系</span>
        {PRIMARY_RELS.map(r => (
          <Chip key={r} active={rels.has(r)} color={REL_COLOR[r]} onClick={() => toggleRel(r)}>{REL_LABEL[r]}</Chip>
        ))}
        <Chip active={STRUCTURAL_RELS.every(r => rels.has(r))} color="#64748b"
          onClick={() => toggleGroup(STRUCTURAL_RELS)}>结构</Chip>
        <Chip active={WEAK_RELS.every(r => rels.has(r))} color="#94a3b8"
          onClick={() => toggleGroup(WEAK_RELS)}>弱关系</Chip>
        <span className="mx-1 h-4 w-px bg-zinc-800" />
        <span className="text-[11px] text-zinc-600">类型</span>
        {Object.keys(TYPE_COLOR).map(t => (
          <Chip key={t} active={types.has(t)} color={TYPE_COLOR[t]} onClick={() => toggleType(t)}>{TYPE_LABEL[t]}</Chip>
        ))}
        <span className="mx-1 h-4 w-px bg-zinc-800" />
        <span className="text-[11px] text-zinc-600">视图</span>
        <Chip active={graphMode === 'current'} color="#60a5fa" onClick={() => setGraphMode('current')}>当前章节</Chip>
        <Chip active={graphMode === 'all'} color="#71717a" onClick={() => setGraphMode('all')}>全量结构</Chip>
        {graphMode === 'current' && (
          <>
            <span className="text-[11px] text-zinc-600 ml-1">章节 ≤ {chapterRange}</span>
            <input type="range" min="0" max={Math.max(0, maxChapter)} step="1" value={chapterRange}
              onChange={e => setChapterRange(+e.target.value)}
              className="w-24" style={{ accentColor: '#60a5fa' }} />
          </>
        )}
        <span className="mx-1 h-4 w-px bg-zinc-800" />
        <span className="text-[11px] text-zinc-600">强度 ≥ {minIntensity.toFixed(1)}</span>
        <input type="range" min="0" max="0.9" step="0.1" value={minIntensity}
          onChange={e => setMinIntensity(+e.target.value)}
          className="w-20" style={{ accentColor: '#71717a' }} />
        <button onClick={resetView}
          className="ml-auto rounded-md border border-zinc-800 px-2.5 py-1 text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors">
          重置视图
        </button>
      </div>

      {/* 聚焦控制 */}
      {focus && (
        <div className="absolute top-16 left-3 flex items-center gap-1.5 rounded-xl border border-zinc-800 px-3 py-1.5"
          style={{ background: '#0f0f11d9', fontSize: 11 }}>
          <span className="text-white">聚焦：{byIdRef.current[focus.id]?.name}</span>
          {([1, 2] as const).map(d => (
            <Chip key={d} active={focus.depth === d} color="#60a5fa"
              onClick={() => setFocus({ ...focus, depth: d })}>{d} 跳</Chip>
          ))}
          <Chip active={false} color="#71717a" onClick={() => setFocus(null)}>退出 (Esc)</Chip>
        </div>
      )}

      {/* 操作提示 */}
      <div className="absolute bottom-3 left-3 text-[11px] text-zinc-700">
        滚轮缩放 · 拖拽平移/移动节点 · 单击查看详情 · 双击聚焦 · hover 揭示弱关系
      </div>

      {/* 统计 */}
      <div className="absolute bottom-3 right-3 text-[11px] text-zinc-600">
        {data.nodes.length} 节点 · {data.edges.length} 条边
      </div>

      {/* 详情抽屉 */}
      {selected && (
        <div className="absolute top-16 right-3 bottom-3 w-[264px] overflow-y-auto rounded-xl border border-zinc-800 p-4"
          style={{ background: '#0f0f11f2', backdropFilter: 'blur(8px)' }}>
          <div className="flex items-center justify-between">
            <div className="text-base font-bold text-zinc-100">{selected.name}</div>
            <span className="cursor-pointer text-zinc-600 hover:text-zinc-300 text-lg leading-none" onClick={() => setSelectedId(null)}>×</span>
          </div>
          <div className="mt-1.5 flex items-center gap-1.5 text-[11px]" style={{ color: TYPE_COLOR[selected.type] }}>
            <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: TYPE_COLOR[selected.type] }} />
            {TYPE_LABEL[selected.type] || selected.type}
          </div>
          {selected.attributes && Object.keys(selected.attributes).length > 0 && (
            <div className="mt-3 text-xs leading-relaxed text-zinc-400">
              {Object.entries(selected.attributes).map(([k, v]) => (
                <div key={k}><span className="text-zinc-600">{k}</span>　{String(v)}</div>
              ))}
            </div>
          )}
          <button onClick={() => setFocus({ id: selected.id, depth: 1 })}
            className="mt-3 w-full rounded-lg border border-blue-800/60 py-1.5 text-xs text-blue-300 hover:bg-blue-900/20 transition-colors">
            聚焦此节点
          </button>
          <div className="mt-4 border-t border-zinc-800 pt-3 text-[11px] text-zinc-600">
            关系 ({selectedEdges.length})
          </div>
          {selectedEdges.map((e, i) => {
            const otherId = e.src === selectedId ? e.dst : e.src;
            const other = byIdRef.current[otherId];
            return (
              <div key={i} onClick={() => setSelectedId(otherId)}
                className="mt-1 flex items-center gap-2 rounded-lg px-1.5 py-1.5 cursor-pointer hover:bg-zinc-800/60">
                <span className="h-1.5 w-1.5 rounded-full flex-shrink-0" style={{ background: REL_COLOR[e.rel] || '#555' }} />
                <span className="text-[11px] flex-shrink-0 w-7" style={{ color: REL_COLOR[e.rel] || '#555' }}>
                  {REL_LABEL[e.rel] || e.rel}
                </span>
                <span className="text-xs text-zinc-300 flex-1 truncate">{other?.name || otherId}</span>
                <span className="relative h-[3px] w-8 flex-shrink-0 rounded-sm bg-zinc-800">
                  <span className="absolute left-0 top-0 h-full rounded-sm"
                    style={{ width: `${e.intensity * 100}%`, background: REL_COLOR[e.rel] || '#555' }} />
                </span>
              </div>
            );
          })}
          {selectedEdges.some(e => e.note) && (
            <div className="mt-3 text-[11px] text-zinc-500 leading-relaxed">
              {selectedEdges.filter(e => e.note).slice(0, 4).map((e, i) => (
                <div key={i} className="mt-1">· {e.note}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Chip 小组件 ── */
function Chip({ active, color, onClick, children }: {
  active: boolean; color: string; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <span onClick={onClick} className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] cursor-pointer select-none transition-all"
      style={{
        border: `1px solid ${active ? color + '88' : '#27272a'}`,
        background: active ? color + '1c' : 'transparent',
        color: active ? '#e4e4e7' : '#52525b',
      }}>
      <span className="inline-block h-[7px] w-[7px] rounded-full" style={{ background: color }} />
      {children}
    </span>
  );
}
