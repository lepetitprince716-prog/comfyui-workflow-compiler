<p align="right"><a href="README.md">English</a> · <a href="../README.zh-CN.md">返回主 README</a></p>

# 3 分钟手把手教程与工作流样板间 (Showcase)

本目录收录了开箱即用的 ComfyUI 编写图（`.json`）以及由 `cwc` 编译好的成品画布（`.ui.json`）。你可以直接把这里的任意 `.ui.json` 文件拖进 ComfyUI 浏览器窗口，立刻体验自动分组、驾驶舱提取和原生拐点（Reroute）走线的效果。

---

## 样板间一览

| 样板文件 | 布局预设 | 规模 | 核心演示点 |
|---|---|---|---|
| [`01-basic-t2i.json`](01-basic-t2i.json) → [`01-basic-t2i.ui.json`](01-basic-t2i.ui.json) | `tower-preview` | 7 节点 · 4 分组 | 标准文生图工作流：通过 `"ui_role": "cockpit"` 把采样器提进左侧 **Controls 驾驶舱**，右侧留出 `>= 880×680` 的大尺寸预览区。 |
| [`02-hires-fix.json`](02-hires-fix.json) → [`02-hires-fix.ui.json`](02-hires-fix.ui.json) | `bento` | 9 节点 · 7 分组 | 两阶段潜空间高清放大（`Base pass` → `Upscale Latent By` → `High-res pass`），**零 `_meta` 分组标注**，100% 由编译器根据拓扑结构自动推断分组。 |
| [`03-optional-style-lora.json`](03-optional-style-lora.json) → [`03-optional-style-lora.ui.json`](03-optional-style-lora.ui.json) | `bento` | 9 节点 · 6 分组 | 将 `PrimitiveInt` 步数抽成左侧驾驶舱旋钮，并用 `"ui_optional": true` 声明一个默认旁路（Bypassed / `mode: 4`）的**可选风格 LoRA 模块**。 |

---

## 教程 1：普通 ComfyUI 玩家的「零命令行 / 一键拖拽」用法

如果你在 ComfyUI 里连出了一团乱麻的“意大利面条图”，或者手里有一份脚本导出的 API JSON，想把它变成整整齐齐的分块画布：

### 第 1 步：从 ComfyUI 导出 API 格式 JSON
在 ComfyUI 设置里开启 **Dev Mode Options（开发者选项）**，点击菜单上的 **Save (API Format)** 导出 `.json` 文件（或者直接拿本目录下的 `.json` 试手）。

### 第 2 步：一键编译
- **Windows 拖拽懒人法**：下载并解压 `cwc-portable.zip`，直接用鼠标把 `.json` 文件拖到 **`compile-drop-here.bat`** 图标上松手。
- **macOS / Linux / 单文件法**：下载单文件 `cwc.pyz`（仅 `377 KB`，无需 `pip install`，无需配虚拟环境），运行：
  ```bash
  python3 cwc.pyz compile 03-optional-style-lora.json -o out
  ```

### 第 3 步：拖回 ComfyUI 画布
打开同目录下自动生成的 `out/` 文件夹，把里面的 `03-optional-style-lora.ui.json` 拖进 ComfyUI 网页即可！所有正负提示词和画布尺寸都归拢在最左侧的 `01 Controls` 驾驶舱，模型、采样器、解码器按数据流顺序整齐排列。

---

## 教程 2：30 秒进阶——定制「左侧驾驶舱」与「可选旁路模块」

任何标准的 ComfyUI API JSON 不做任何修改就能直接被 `cwc` 编译。如果你想让分享出去的工作流更好用（比如发到 Civitai 上给别人下载），只需在节点里加一行 `_meta`：

### 1. 把常用参数提进最左侧的 `01 Controls` 驾驶舱
默认情况下，正负提示词和 `EmptyLatentImage`（宽高尺寸）会自动进入左侧 `01 Controls`。如果你想把采样器或步数节点也放到最左边方便用户随手调，加上 `"ui_role": "cockpit"` 即可：

```json
"steps": {
  "class_type": "PrimitiveInt",
  "inputs": {"value": 28},
  "_meta": {"title": "Steps", "ui_role": "cockpit"}
}
```

### 2. 把非必需的 LoRA / 放大器做成「默认旁路（Bypass）的可选模块」
分享工作流时，下载者往往没有装齐所有的附加画风 LoRA。加上 `"ui_optional": true`，`cwc` 就会把它单独放进下方的 **Optional models** 分组并默认设为旁路状态（对应 ComfyUI 里的 `Ctrl+B` / `mode: 4`）：

```json
"lora": {
  "class_type": "LoraLoader",
  "inputs": {
    "model": ["ckpt", "MODEL"],
    "clip": ["ckpt", "CLIP"],
    "lora_name": "sabikaze_style.safetensors",
    "strength_model": 0.85,
    "strength_clip": 0.85
  },
  "_meta": {"title": "Style LoRA (Optional)", "ui_optional": true}
}
```

编译时加上 `--all-enabled`，`cwc` 会同时校验「默认旁路」与「全部可选模块开启（`<name>.full.api.json`）」两条通路是否都 100% 合法。

### 3. 一键切换 5 套布局预设 (`--preset`)
你可以在命令行传 `--preset <名称>`，或者在 JSON 顶层写 `"_workflow": {"format": 1, "layout": {"preset": "<名称>"}}`：
- `bento`（默认）：主干走上排、控制分支与可选模块走下排，长图自动折行。
- `tower-preview`：把最右侧的输出组撑大到至少 `880×680` 像素，边调参数边看大图预览。
- `swimlanes`：多条 ControlNet / 遮罩分支各自对齐在它所控制的采样阶段正下方。
- `cockpit-split`：适合 60+ 节点的巨型工作流，把核心控制项（提示词、尺寸、种子）与次要调参框上下分舱。
- `stepped`：按 `Step 1`、`Step 2`、`Step 3` 分列编号，最适合做教学演示工作流。

---

## 教程 3：让 AI 编程助手（Claude Code / Cursor / Gemini / Codex）替你写画布

大模型非常擅长写扁平的 API JSON（`class_type` + `inputs`），但让它直接手算像素坐标和 LiteGraph 的 `widgets_values` 数组必出错。

直接把仓库里的 [`../skills/comfyui-workflow-compiler/SKILL.md`](../skills/comfyui-workflow-compiler/SKILL.md) 挂载给你的 Agent，或者对它说：

> *「请使用 `cwc`（`python3 cwc.pyz`）帮我编写一个【你的需求】ComfyUI 工作流。先运行 `python3 cwc.pyz guide` 阅读格式规范，用 `python3 cwc.pyz node --search <关键词>` 查询节点插槽，再用 `python3 cwc.pyz compile graph.json -o out` 编译校验，直到零错误输出 `.ui.json` 画布。」*
