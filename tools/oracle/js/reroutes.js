// Page-side measurement of native reroutes (extra.reroutes, extra.linkExtensions). Evaluated after lib.js;
// defines window.__cwcReroutes.probe. Every result is plain JSON.
(() => {
  const clone = (v) => {
    if (v === undefined) return null;
    try {
      const s = JSON.stringify(v);
      return s === undefined ? null : JSON.parse(s);
    } catch (e) {
      return null;
    }
  };

  const frames = (n) =>
    new Promise((resolve) => {
      const step = (left) => (left <= 0 ? resolve() : requestAnimationFrame(() => step(left - 1)));
      step(n);
    });

  const load = async (workflow) => {
    const errors = [];
    const origError = console.error;
    console.error = (...a) => {
      errors.push(a.map(String).join(' ').slice(0, 300));
      return origError.apply(console, a);
    };
    let load_error = null;
    try {
      await window.app.loadGraphData(clone(workflow), true, false, null, {});
    } catch (e) {
      load_error = String(e && e.stack ? e.stack : e);
    }
    console.error = origError;
    return { load_error, console_errors: errors };
  };

  // Fit nodes and reroutes in the viewport at scale <= 1, so every link is inside the culling margin.
  const fit = () => {
    const g = window.app.graph;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const add = (b) => {
      x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]);
      x1 = Math.max(x1, b[0] + b[2]); y1 = Math.max(y1, b[1] + b[3]);
    };
    for (const n of g._nodes) add(n.boundingRect);
    for (const r of g.reroutes.values()) add([r.pos[0] - 10, r.pos[1] - 10, 20, 20]);
    if (!isFinite(x0)) return;
    const el = window.app.canvas.canvas;
    const pad = 40;
    const scale = Math.min(1, (el.clientWidth - 2 * pad) / Math.max(1, x1 - x0), (el.clientHeight - 2 * pad) / Math.max(1, y1 - y0));
    const ds = window.app.canvas.ds;
    ds.scale = scale;
    ds.offset[0] = pad / scale - x0;
    ds.offset[1] = pad / scale - y0;
    window.app.canvas.setDirty(true, true);
  };

  const pt = (p) => [p[0], p[1]];
  const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);

  const rerouteRow = (r) => ({
    id: r.id,
    parent_id: r.parentId ?? null,
    pos: pt(r.pos),
    floating: clone(r.floating),
    link_ids: [...r.linkIds].sort((a, b) => a - b),
    floating_link_ids: [...r.floatingLinkIds].sort((a, b) => a - b),
    cos: r.cos,
    sin: r.sin,
    control_point: pt(r.controlPoint),
  });

  const liveState = (g) => ({
    reroutes: [...g.reroutes.values()].map(rerouteRow),
    links: [...g.links.values()].map((l) => ({ id: l.id, parent_id: l.parentId ?? null, origin: [l.origin_id, l.origin_slot], target: [l.target_id, l.target_slot] })),
    floating_links: [...g.floatingLinks.values()].map((l) => ({ id: l.id, parent_id: l.parentId ?? null, origin: [l.origin_id, l.origin_slot], target: [l.target_id, l.target_slot] })),
    last_node_id: g.state ? g.state.lastNodeId : null,
    last_reroute_id: g.state ? g.state.lastRerouteId : null,
  });

  const bezier = (p0, c1, c2, p3, t) => {
    const u = 1 - t;
    const a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return [a * p0[0] + b * c1[0] + c * c2[0] + d * p3[0], a * p0[1] + b * c1[1] + c * c2[1] + d * p3[1]];
  };

  // The segments the rule predicts for one link: from the output anchor through each reroute to the input anchor.
  // Reroute direction (cos, sin) is the mean of the angles to the next point of every link through the reroute.
  const predictLink = (g, link, p0, p3) => {
    const maxOffset = window.LiteGraph.Reroute.maxSplineOffset;
    const first = g.getReroute(link.parentId);
    const chain = first ? first.getReroutes() : [];
    if (!chain || !chain.length) return { chain: [], segments: [] };
    const dirOf = (r, linkStart) => {
      const angles = [];
      for (const id of [...r.linkIds, ...r.floatingLinkIds]) {
        const l = g.links.get(id) ?? g.floatingLinks.get(id);
        if (!l) continue;
        const rs = g.getReroute(l.parentId) ? g.getReroute(l.parentId).getReroutes() : [];
        const at = rs.findIndex((x) => x.id === r.id);
        let next = null;
        if (at >= 0 && at + 1 < rs.length) next = rs[at + 1].pos;
        else {
          const tn = g.getNodeById(l.target_id);
          if (tn) next = Array.from(tn.getInputPos(l.target_slot));
        }
        if (next) angles.push(Math.atan2(next[1] - r.pos[1], next[0] - r.pos[0]));
      }
      if (!angles.length) return { cos: 0, sin: 0, control: [0, 0] };
      const mean = angles.reduce((s, a) => s + a, 0) / angles.length;
      const o = Math.atan2(r.pos[1] - linkStart[1], r.pos[0] - linkStart[0]);
      let diff = (o - mean) * 0.5;
      if (Math.abs(diff) > Math.PI * 0.5) diff += Math.PI;
      const d = Math.min(maxOffset, dist(linkStart, r.pos) * 0.25);
      const od = o - diff;
      return { cos: Math.cos(od), sin: Math.sin(od), control: [d * -Math.cos(od), d * -Math.sin(od)] };
    };
    const dirs = chain.map((r, j) => dirOf(r, j > 0 ? chain[j - 1].pos : p0));
    const segments = [];
    let startOff = null;
    chain.forEach((r, j) => {
      const a = j > 0 ? pt(chain[j - 1].pos) : pt(p0);
      const b = pt(r.pos);
      const c1 = startOff ? [a[0] + startOff[0], a[1] + startOff[1]] : [a[0] + 0.25 * dist(a, b), a[1]];
      const c2 = [b[0] + dirs[j].control[0], b[1] + dirs[j].control[1]];
      segments.push({ key: 'reroute:' + r.id, link: link.id, a, b, c1, c2 });
      const nextPos = chain[j + 1] ? chain[j + 1].pos : p3;
      const d = Math.min(maxOffset, dist(r.pos, nextPos) * 0.25);
      startOff = [d * dirs[j].cos, d * dirs[j].sin];
    });
    const last = pt(chain[chain.length - 1].pos);
    segments.push({
      key: 'link:' + link.id, link: link.id, a: last, b: pt(p3),
      c1: [last[0] + startOff[0], last[1] + startOff[1]],
      c2: [p3[0] - 0.25 * dist(last, p3), p3[1]],
    });
    return { chain: chain.map((r) => r.id), segments, dirs: dirs.map((d) => ({ cos: d.cos, sin: d.sin, control: d.control })) };
  };

  // Draw once with the path renderer and the arc call spied on; return what the frontend actually drew.
  const drawSpied = () => {
    const canvas = window.app.canvas;
    const renderer = canvas.linkRenderer;
    const pr = renderer.pathRenderer;
    const calls = [];
    const origDraw = pr.drawLink;
    pr.drawLink = function (ctx, linkData, pathContext) {
      calls.push({
        id: linkData.id,
        start: [linkData.startPoint.x, linkData.startPoint.y],
        end: [linkData.endPoint.x, linkData.endPoint.y],
        start_direction: linkData.startDirection,
        end_direction: linkData.endDirection,
        control_points: (linkData.controlPoints || []).map((c) => [c.x, c.y]),
        disabled: !!linkData.disabled,
      });
      return origDraw.call(this, ctx, linkData, pathContext);
    };
    const arcs = [];
    const proto = CanvasRenderingContext2D.prototype;
    const origArc = proto.arc;
    proto.arc = function (x, y, r, a0, a1, ccw) {
      arcs.push([x, y, r, a0, a1]);
      return origArc.call(this, x, y, r, a0, a1, ccw);
    };
    try {
      canvas.setDirty(true, true);
      canvas.draw(true, true);
    } finally {
      pr.drawLink = origDraw;
      proto.arc = origArc;
    }
    return { calls, arcs };
  };

  const near = (p, q, tol = 1e-6) => Math.abs(p[0] - q[0]) <= tol && Math.abs(p[1] - q[1]) <= tol;

  const measureDraw = async (g) => {
    fit();
    window.app.canvas.draw(true, true);
    await frames(6);
    window.app.canvas.draw(true, true);
    await frames(2);
    const { calls, arcs } = drawSpied();
    const ctx = document.createElement('canvas').getContext('2d');
    ctx.lineWidth = 3;
    const rows = [];
    const rerouteIds = new Set([...g.reroutes.values()].map((r) => r.id));
    for (const link of g.links.values()) {
      const a = g.getNodeById(link.origin_id), b = g.getNodeById(link.target_id);
      if (!a || !b) continue;
      const p0 = Array.from(a.getOutputPos(link.origin_slot));
      const p3 = Array.from(b.getInputPos(link.target_slot));
      const pred = predictLink(g, link, p0, p3);
      if (!pred.segments.length) continue;
      const segs = pred.segments.map((s) => {
        const owner = s.key.startsWith('reroute:') ? g.getReroute(Number(s.key.slice(8))) : link;
        const path = owner && owner.path;
        let on = 0;
        const n = 21;
        if (path) {
          for (let k = 0; k < n; k++) {
            const [x, y] = bezier(s.a, s.c1, s.c2, s.b, k / (n - 1));
            if (ctx.isPointInStroke(path, x, y)) on++;
          }
        }
        const hit = calls.find((c) => near(c.start, s.a) && near(c.end, s.b) && c.control_points.length === 2 && near(c.control_points[0], s.c1, 1e-3) && near(c.control_points[1], s.c2, 1e-3));
        return { ...s, has_path: !!path, samples: n, on_path: on, matched_call: !!hit };
      });
      rows.push({ link: link.id, anchors: [p0, p3], chain: pred.chain, segments: segs, rerouted_dirs: pred.dirs });
    }
    // Reroute circles: arcs centred on a reroute.
    const circles = [];
    for (const r of g.reroutes.values()) {
      const radii = arcs.filter((a) => near([a[0], a[1]], pt(r.pos), 1e-6)).map((a) => a[2]);
      circles.push({ id: r.id, radii });
    }
    return {
      max_spline_offset: window.LiteGraph.Reroute.maxSplineOffset,
      reroute_radius: window.LiteGraph.Reroute.radius,
      calls,
      call_count: calls.length,
      predicted: rows,
      circles,
      reroutes_after_draw: [...g.reroutes.values()].map(rerouteRow),
    };
  };

  // Group membership of reroutes, then a drag of each group as the canvas does it (nested items once, skipChildren).
  const settled = async (workflow) => {
    const r = await load(workflow);
    fit();
    window.app.canvas.draw(true, true);
    await frames(8);
    return r;
  };

  const groupsAndMove = async (workflow) => {
    await settled(workflow);
    const g = window.app.graph;
    const out = { groups: [], moves: [] };
    const nested = (items) => {
      const all = new Set();
      const add = (it) => {
        if (all.has(it) || it.pinned) return;
        all.add(it);
        if (it.children) for (const c of it.children) add(c);
      };
      for (const it of items) add(it);
      return all;
    };
    for (const gr of [...g.groups]) {
      gr.recomputeInsideNodes();
      out.groups.push({
        id: gr.id,
        title: gr.title,
        bounding: Array.from(gr._bounding),
        reroute_ids: [...gr.children].filter((c) => c.id !== undefined && g.reroutes.get(c.id) === c).map((c) => c.id).sort((a, b) => a - b),
        node_ids: gr._nodes.map((n) => n.id).sort((a, b) => a - b),
      });
    }
    for (const info of out.groups) {
      await settled(workflow);
      const g2 = window.app.graph;
      const gr = g2.groups.find((x) => x.id === info.id);
      gr.recomputeInsideNodes();
      const before = {
        reroutes: Object.fromEntries([...g2.reroutes.values()].map((r) => [r.id, pt(r.pos)])),
        nodes: Object.fromEntries(g2._nodes.map((n) => [n.id, pt(n.pos)])),
      };
      for (const item of nested(new Set([gr]))) item.move(37, 53, true);
      const after = {
        reroutes: Object.fromEntries([...g2.reroutes.values()].map((r) => [r.id, pt(r.pos)])),
        nodes: Object.fromEntries(g2._nodes.map((n) => [n.id, pt(n.pos)])),
      };
      const moved = (a, b) => Object.keys(a).filter((k) => !near(a[k], b[k])).map(Number).sort((x, y) => x - y);
      const shifted = (a, b) => Object.keys(a).filter((k) => near([b[k][0] - a[k][0], b[k][1] - a[k][1]], [37, 53])).map(Number).sort((x, y) => x - y);
      out.moves.push({
        group: info.id,
        delta: [37, 53],
        moved_reroutes: moved(before.reroutes, after.reroutes),
        shifted_by_delta_reroutes: shifted(before.reroutes, after.reroutes),
        moved_nodes: moved(before.nodes, after.nodes),
        shifted_by_delta_nodes: shifted(before.nodes, after.nodes),
      });
    }
    return out;
  };

  const probe = async (workflow, opts) => {
    const res = { ...(await load(workflow)) };
    const g = window.app.graph;
    res.serialized = clone(g.serialize());
    res.live = liveState(g);
    res.draw = opts.draw === false ? null : await measureDraw(g);
    if (res.draw) res.serialized_after_draw = clone(g.serialize());
    // Round trip: load what serialize() returned, serialize again.
    const again = await load(res.serialized);
    res.reload = again;
    res.serialized_again = clone(window.app.graph.serialize());
    res.live_again = liveState(window.app.graph);
    if (opts.groups) res.group_tests = await groupsAndMove(workflow);
    return res;
  };

  window.__cwcReroutes = { probe };
})();
