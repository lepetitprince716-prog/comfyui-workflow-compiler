// Page-side helpers for tools/oracle. Evaluated once per page; defines window.__cwc.
// Every function returns plain JSON so Playwright can hand it back unchanged.
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

  const widgetInfo = (w) => ({
    name: w.name ?? null,
    type: w.type ?? null,
    value: clone(w.value),
    serialize: w.serialize !== false,
    to_prompt: !(w.options && w.options.serialize === false),
  });

  const inputInfo = (s) => ({
    name: s.name,
    type: clone(s.type),
    link: s.link ?? null,
    widget: s.widget ? s.widget.name ?? null : null,
    shape: s.shape ?? null,
    label: s.label ?? null,
  });

  const outputInfo = (s) => ({
    name: s.name,
    type: clone(s.type),
    links: clone(s.links),
    shape: s.shape ?? null,
  });

  const nodeInfo = (n) => ({
    id: n.id,
    type: n.type,
    title: n.title,
    mode: n.mode,
    pos: [n.pos[0], n.pos[1]],
    size: [n.size[0], n.size[1]],
    bounding: Array.from(n.boundingRect),
    has_errors: !!n.has_errors,
    widgets: (n.widgets || []).map(widgetInfo),
    inputs: (n.inputs || []).map(inputInfo),
    outputs: (n.outputs || []).map(outputInfo),
  });

  // Fit every node into the viewport at scale <= 1 so DOM nodes are laid out on screen.
  const fitAll = () => {
    const nodes = window.app.graph._nodes;
    if (!nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      const b = n.boundingRect;
      x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]);
      x1 = Math.max(x1, b[0] + b[2]); y1 = Math.max(y1, b[1] + b[3]);
    }
    const el = window.app.canvas.canvas;
    const w = el.clientWidth, h = el.clientHeight, pad = 40;
    const scale = Math.min(1, (w - 2 * pad) / Math.max(1, x1 - x0), (h - 2 * pad) / Math.max(1, y1 - y0));
    const ds = window.app.canvas.ds;
    ds.scale = scale;
    ds.offset[0] = pad / scale - x0;
    ds.offset[1] = pad / scale - y0;
    window.app.canvas.setDirty(true, true);
  };

  // Vue renderer: measure each node's DOM box and convert it to graph coordinates.
  const measureVue = () => {
    const canvasRect = window.app.canvas.canvas.getBoundingClientRect();
    const ds = window.app.canvas.ds;
    const out = {};
    for (const el of document.querySelectorAll('[data-node-id]')) {
      const r = el.getBoundingClientRect();
      const x = (r.left - canvasRect.left) / ds.scale - ds.offset[0];
      const y = (r.top - canvasRect.top) / ds.scale - ds.offset[1];
      out[el.getAttribute('data-node-id')] = [x, y, r.width / ds.scale, r.height / ds.scale];
    }
    return out;
  };

  // Rectangle of a DOM element in graph units (layout size, undone from the canvas scale).
  const domRect = (el) => {
    const r = el.getBoundingClientRect();
    const k = window.app.canvas.ds.scale;
    return [r.width / k, r.height / k];
  };

  // Elements inside a Vue node worth sizing: header, text areas, previews, markdown body.
  const VUE_PARTS = 'textarea, img, canvas, video, [role=textarea], .comfy-markdown-content, [data-testid=node-header], .lg-node-header';
  const vueParts = (el) =>
    Array.from(el.querySelectorAll(VUE_PARTS)).map((p) => {
      const [w, h] = domRect(p);
      return {
        tag: p.tagName.toLowerCase(),
        cls: String(p.className || '').split(/\s+/).filter((c) => /^(comfy|lg-|widget)/.test(c)).join(' '),
        size: [w, h],
        scroll_height: p.scrollHeight,
        client_height: p.clientHeight,
        hidden: p.offsetParent === null,
      };
    });

  // Geometry of every node under the active renderer, after the frames have settled.
  // classic: node.size and boundingRect plus the DOM widgets' elements;
  // vue: the node element's box (title bar included) plus its inner parts.
  const measureLayout = () => {
    const vueBoxes = window.LiteGraph.vueNodesMode ? measureVue() : {};
    return window.app.graph._nodes.map((n) => {
      const row = {
        id: String(n.id),
        type: n.type,
        size: [n.size[0], n.size[1]],
        bounding: Array.from(n.boundingRect),
        min_size: (() => { try { return Array.from(n.computeSize()); } catch (e) { return null; } })(),
        vue_box: vueBoxes[String(n.id)] ?? null,
        imgs: (n.imgs || []).map((i) => [i.naturalWidth, i.naturalHeight, !!i.complete]),
        parts: null,
        widgets: (n.widgets || []).map((w) => ({
          name: w.name ?? null,
          type: w.type ?? null,
          y: w.y ?? null,
          computed_height: w.computedHeight ?? null,
          element: w.element ? { tag: w.element.tagName.toLowerCase(), size: domRect(w.element), scroll_height: w.element.scrollHeight } : null,
        })),
      };
      if (window.LiteGraph.vueNodesMode) {
        const el = document.querySelector('[data-node-id="' + CSS.escape(String(n.id)) + '"]');
        row.parts = el ? vueParts(el) : null;
      }
      return row;
    });
  };

  // Block-level breakdown of one rendered markdown body: per child tag, height, margins and
  // the number of text lines, to fit a line height per block kind.
  const markdownBlocks = (root) => {
    const k = window.app.canvas.ds.scale;
    const lineCount = (el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      const tops = new Set();
      for (const r of range.getClientRects()) if (r.height > 0) tops.add(Math.round(r.top));
      return tops.size;
    };
    const kids = Array.from(root.children).map((c) => {
      const cs = getComputedStyle(c);
      const row = {
        tag: c.tagName.toLowerCase(),
        height: c.getBoundingClientRect().height / k,
        margin_top: parseFloat(cs.marginTop),
        margin_bottom: parseFloat(cs.marginBottom),
        line_height: cs.lineHeight,
        font_size: cs.fontSize,
        lines: lineCount(c),
        cjk: /[\u3000-\u9fff\uff00-\uffef]/.test(c.textContent),
      };
      if (c.querySelector('tr')) {
        row.rows = Array.from(c.querySelectorAll('tr')).map((tr) => tr.getBoundingClientRect().height / k);
      }
      if (c.tagName === 'UL' || c.tagName === 'OL') {
        row.items = Array.from(c.querySelectorAll(':scope > li')).map((li) => li.getBoundingClientRect().height / k);
      }
      return row;
    });
    const cs = getComputedStyle(root);
    // Content extent from the first block's top edge to the last block's bottom margin: the
    // height the body needs, also when it is shorter than the box (where scrollHeight only
    // reports the box).
    const last = root.lastElementChild;
    const extent = last
      ? (last.getBoundingClientRect().bottom - root.getBoundingClientRect().top) / k + parseFloat(getComputedStyle(last).marginBottom)
      : 0;
    return {
      padding: [cs.paddingTop, cs.paddingBottom],
      scroll_height: root.scrollHeight,
      client_height: root.clientHeight,
      extent,
      font_family: cs.fontFamily,
      font_size_var: getComputedStyle(document.documentElement).getPropertyValue('--comfy-textarea-font-size').trim(),
      blocks: kids,
    };
  };

  // The markdown body element of each MarkdownNote (classic: the Tiptap editor; vue: the rendered html).
  const measureMarkdown = () => {
    const out = {};
    for (const n of window.app.graph._nodes) {
      if (n.type !== 'MarkdownNote') continue;
      let body = null;
      let host = null;
      if (window.LiteGraph.vueNodesMode) {
        host = document.querySelector('[data-node-id="' + CSS.escape(String(n.id)) + '"]');
        body = host && host.querySelector('.comfy-markdown-content');
      } else {
        const w = (n.widgets || []).find((w) => w.element);
        host = w && w.element;
        body = host && host.querySelector('.tiptap');
      }
      out[String(n.id)] = body ? { ...markdownBlocks(body), host_size: domRect(host), body_size: domRect(body) } : null;
    }
    return out;
  };

  // Fake a node's execution result so a preview node shows images (the /api/view reply is
  // answered by the harness).
  const showOutput = async (nodeId, images) => {
    window.app.api.dispatchCustomEvent('executed', {
      node: String(nodeId), display_node: String(nodeId), prompt_id: 'cwc', output: { images },
    });
    await frames(10);
  };

  // Wait until every image a node holds (classic renderer: node.imgs) has loaded, at most ms.
  const waitImages = async (ms) => {
    const t0 = performance.now();
    const pending = () => window.app.graph._nodes.some((n) => (n.imgs || []).some((i) => !i.complete));
    while (pending() && performance.now() - t0 < ms) await new Promise((r) => setTimeout(r, 50));
    await frames(6);
  };

  // Load a canvas and return the layout once settled. frames: animation frames to wait.
  const layout = async (workflow, opts) => {
    const res = { load_error: null };
    try {
      await window.app.loadGraphData(workflow, true, false, null, {});
    } catch (e) {
      res.load_error = String(e && e.stack ? e.stack : e);
    }
    fitAll();
    window.app.canvas.draw(true, true);
    await frames(opts.frames ?? 12);
    for (const [id, images] of Object.entries(opts.outputs || {})) await showOutput(id, images);
    await waitImages(3000);
    await frames(opts.frames ?? 12);
    res.scale = window.app.canvas.ds.scale;
    res.nodes = measureLayout();
    res.markdown = measureMarkdown();
    res.serialized_sizes = Object.fromEntries(window.app.graph.serialize().nodes.map((n) => [String(n.id), n.size]));
    return res;
  };

  const versions = async () => {
    let comfyui = null;
    try {
      const r = await fetch('/api/system_stats');
      comfyui = (await r.json()).system.comfyui_version ?? null;
    } catch (e) {
      comfyui = null;
    }
    return {
      frontend: window.__COMFYUI_FRONTEND_VERSION__ ?? null,
      comfyui,
      vue_nodes: !!window.LiteGraph.vueNodesMode,
      named_values_restore: !!window.LiteGraph.namedValuesRestore,
    };
  };

  const probe = async (workflow, opts) => {
    const res = { load_result: null, load_error: null };
    try {
      res.load_result = clone(await window.app.loadGraphData(workflow, true, false, null, {}));
    } catch (e) {
      res.load_error = String(e && e.stack ? e.stack : e);
    }
    fitAll();
    window.app.canvas.draw(true, true);
    await frames(opts.frames ?? 6);
    res.nodes = window.app.graph._nodes.map(nodeInfo);
    res.vue_boxes = window.LiteGraph.vueNodesMode ? measureVue() : null;
    try {
      const p = await window.app.graphToPrompt();
      res.prompt = clone(p.output);
      res.prompt_workflow = clone(p.workflow);
    } catch (e) {
      res.prompt_error = String(e && e.stack ? e.stack : e);
    }
    res.serialized = clone(window.app.graph.serialize());
    return res;
  };

  // Instantiate every registered node type once, without adding it to a graph.
  // core: the set of type names that /object_info reports.
  const truth = async (opts) => {
    const LG = window.LiteGraph;
    const types = Object.keys(LG.registered_node_types).sort();
    const nodes = {};
    const failed = {};
    const messages = {};
    let current = null;
    const origError = console.error, origWarn = console.warn;
    const capture = (level) => (...a) => {
      if (current) (messages[current] ||= []).push(level + ': ' + a.map(String).join(' ').slice(0, 300));
      return (level === 'error' ? origError : origWarn).apply(console, a);
    };
    console.error = capture('error');
    console.warn = capture('warning');
    const graph = window.app.graph;
    const t0 = performance.now();
    try {
      for (const type of types) {
        current = type;
        let node = null;
        try {
          node = LG.createNode(type);
          if (!node) throw new Error('createNode returned ' + node);
          if (opts.add) graph.add(node);
          const nd = node.constructor.nodeData;
          nodes[type] = {
            core: !!(nd && nd.python_module),
            inputs: (node.inputs || []).map((s) => ({
              name: s.name,
              type: clone(s.type),
              widget: s.widget ? s.widget.name ?? null : null,
              shape: s.shape ?? null,
            })),
            outputs: (node.outputs || []).map((s, i) => ({
              name: s.name,
              type: clone(s.type),
              is_list: !!(nd && nd.output_is_list && nd.output_is_list[i]),
            })),
            widgets: (node.widgets || [])
              .filter((w) => w.serialize !== false)
              .map((w) => ({
                name: w.name ?? null,
                type: w.type ?? null,
                value: clone(w.value),
                to_prompt: !(w.options && w.options.serialize === false),
              })),
            size: { classic: [node.size[0], node.size[1]], vue: null },
          };
        } catch (e) {
          failed[type] = String(e && e.message ? e.message : e).slice(0, 300);
        } finally {
          if (node && opts.add) {
            try { graph.remove(node); } catch (e) { /* recorded by the next check */ }
          }
        }
      }
    } finally {
      current = null;
      console.error = origError;
      console.warn = origWarn;
    }
    return { types: types.length, nodes, failed, messages, ms: performance.now() - t0 };
  };

  // Free-form evaluation used by tools/oracle/m0.py experiments.
  const createAndSerialize = async (specs) => {
    await window.app.loadGraphData({ nodes: [], links: [], groups: [], version: 0.4, last_node_id: 0, last_link_id: 0 }, true, false, null, {});
    const g = window.app.graph;
    const made = [];
    for (const s of specs) {
      const n = window.LiteGraph.createNode(s.type);
      g.add(n);
      n.pos = s.pos || [100 + made.length * 400, 100];
      for (const [name, value] of Object.entries(s.set || {})) {
        const w = n.widgets.find((w) => w.name === name);
        if (!w) throw new Error('no widget ' + name + ' on ' + s.type);
        w.value = value;
        if (w.callback) w.callback(value, window.app.canvas, n);
      }
      made.push(n);
    }
    for (const s of specs) {
      for (const [from, fromSlot, to, toSlot] of s.connect || []) {
        const a = made[from], b = made[to];
        const slot = typeof toSlot === 'string' ? b.inputs.findIndex((i) => i.name === toSlot) : toSlot;
        if (slot < 0) throw new Error('no input ' + toSlot + ' on ' + b.type);
        a.connect(fromSlot, b, slot);
      }
    }
    await frames(4);
    return { nodes: g._nodes.map(nodeInfo), serialized: clone(g.serialize()) };
  };

  // Cubic bezier point at t.
  const bezier = (p0, c1, c2, p3, t) => {
    const u = 1 - t;
    const a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return [a * p0[0] + b * c1[0] + c * c2[0] + d * p3[0], a * p0[1] + b * c1[1] + c * c2[1] + d * p3[1]];
  };

  // Load a canvas and report the geometry a layout checker needs: per node the visual box
  // (boundingRect, and the DOM box under the Vue renderer) and the slot anchors, per group
  // the bounding and the members recomputeInsideNodes finds, per link the anchors, the
  // spline control points predicted from them and how many sampled curve points lie on the
  // path the canvas actually stroked. serialized is taken before any recompute (recompute
  // reorders graph.groups). opts.prompt: also graphToPrompt().output as res.prompt;
  // opts.links === false: leave the link rows out.
  const geometry = async (workflow, opts) => {
    const res = { load_error: null };
    try {
      await window.app.loadGraphData(workflow, true, false, null, {});
    } catch (e) {
      res.load_error = String(e && e.stack ? e.stack : e);
    }
    fitAll();
    window.app.canvas.draw(true, true);
    await frames(opts.frames ?? 12);
    window.app.canvas.draw(true, true);
    await frames(4);
    const g = window.app.graph;
    const LG = window.LiteGraph;
    res.serialized = clone(g.serialize());
    if (opts.prompt) {
      try {
        const p = await window.app.graphToPrompt();
        res.prompt = clone(p.output);
      } catch (e) {
        res.prompt_error = String(e && e.stack ? e.stack : e);
      }
    }
    res.settings = {
      links_render_mode: window.app.canvas.links_render_mode,
      always_snap_to_grid: !!LG.alwaysSnapToGrid,
      canvas_grid_size: LG.CANVAS_GRID_SIZE,
      snap_to_grid_size: (() => { try { return g.getSnapToGridSize(); } catch (e) { return null; } })(),
      node_title_height: LG.NODE_TITLE_HEIGHT,
      node_slot_height: LG.NODE_SLOT_HEIGHT,
      node_collapsed_width: LG.NODE_COLLAPSED_WIDTH,
      group_text_size: LG.GROUP_TEXT_SIZE,
      group_font: LG.GROUP_FONT,
    };
    const vueBoxes = LG.vueNodesMode ? measureVue() : {};
    const anchor = (n, input, i) => {
      try { return Array.from(input ? n.getInputPos(i) : n.getOutputPos(i)); } catch (e) { return null; }
    };
    res.nodes = g._nodes.map((n) => ({
      id: String(n.id),
      type: n.type,
      title: n.title,
      collapsed: !!(n.flags && n.flags.collapsed),
      pos: [n.pos[0], n.pos[1]],
      size: [n.size[0], n.size[1]],
      bounding: Array.from(n.boundingRect),
      collapsed_width: n._collapsed_width ?? null,
      vue_box: vueBoxes[String(n.id)] ?? null,
      inputs: (n.inputs || []).map((s, i) => ({ name: s.name, widget: s.widget ? s.widget.name ?? null : null, link: s.link ?? null, anchor: anchor(n, true, i) })),
      outputs: (n.outputs || []).map((s, i) => ({ name: s.name, links: clone(s.links), anchor: anchor(n, false, i) })),
    }));
    // Links: a test context with identity transform, so Path2D coordinates are graph units.
    const ctx = document.createElement('canvas').getContext('2d');
    ctx.lineWidth = opts.stroke ?? 3;
    res.links = [];
    for (const link of opts.links === false ? [] : g.links.values()) {
      const a = g.getNodeById(link.origin_id), b = g.getNodeById(link.target_id);
      if (!a || !b) continue;
      const p0 = Array.from(a.getOutputPos(link.origin_slot));
      const p3 = Array.from(b.getInputPos(link.target_slot));
      const dist = Math.hypot(p3[0] - p0[0], p3[1] - p0[1]);
      const c1 = [p0[0] + dist * 0.25, p0[1]];
      const c2 = [p3[0] - dist * 0.25, p3[1]];
      const row = { id: link.id, origin: [String(link.origin_id), link.origin_slot], target: [String(link.target_id), link.target_slot], start: p0, end: p3, c1, c2, rendered: !!link.path, samples: 0, on_path: 0 };
      if (link.path) {
        for (let k = 0; k <= 20; k++) {
          const [x, y] = bezier(p0, c1, c2, p3, k / 20);
          row.samples++;
          if (ctx.isPointInStroke(link.path, x, y)) row.on_path++;
        }
      }
      res.links.push(row);
    }
    // Groups last: recomputeInsideNodes mutates graph.groups order.
    res.groups = g.groups.map((gr) => ({ id: gr.id, title: gr.title, bounding: Array.from(gr._bounding), font_size: gr.font_size, title_height: gr.titleHeight }));
    for (const row of res.groups) {
      const gr = g.groups.find((x) => x.id === row.id);
      gr.recomputeInsideNodes();
      row.member_nodes = gr._nodes.map((n) => String(n.id)).sort();
      row.child_groups = [...gr.children].filter((c) => c instanceof gr.constructor).map((c) => c.id).sort();
    }
    return res;
  };

  window.__cwc = { versions, probe, truth, createAndSerialize, frames, layout, geometry };
})();
