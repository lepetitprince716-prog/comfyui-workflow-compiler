<p align="right"><a href="README.md">English</a></p>

<h1 align="center">comfyui-workflow-compiler</h1>

<p align="center">
  <code>cwc</code> 把一张 ComfyUI API 图（编程 agent 写的或手写的）编译成人看得懂的画布：分好组、排好版、用原生 reroute 连线，并且检查过它导出的 prompt 和编译前的一致。
</p>

<p align="center"><code>pip install .</code> 之后 <code>cwc compile graph.json -o out</code></p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/hero-dark.png">
    <img src="docs/images/hero-light.png" width="830" alt="cwc 编译出的画布左边部分：Controls 组里是正负提示词和画布尺寸，Models 组里是模型加载，Sampling 组里是第一遍采样，组之间用原生 reroute 连接">
  </picture>
</p>

上图是 `cwc compile` 为 [`docs/images/src/hires.json`](docs/images/src/hires.json) 写出的画布的左边部分。这张图有九个节点，做文生图再加一遍高清放大。图是 ComfyUI 前端的截图，只隐藏了前端自己的菜单。完整的画布见[五种预设](#五种预设)。

## 试一下

需要 Python 3.10 或更新版本；核心只用标准库。

```bash
git clone https://github.com/lepetitprince716-prog/comfyui-workflow-compiler
cd comfyui-workflow-compiler
python3 -m venv .venv && . .venv/bin/activate
pip install .
cwc compile docs/images/src/hires.json -o out
```

```text
G9 warning: the canvas extent is 2560 x 820, aspect ratio 3.12; it should be 1 to 2.8
cwc compile: ok; preset bento; 7 groups, 0 optional modules, 1 warning; preview: cwc render out/hires.ui.json -o hires.svg
wrote out/hires.ui.json
wrote out/hires.api.json
wrote out/hires.report.json
```

把 `out/hires.ui.json` 拖到 ComfyUI 画布上就能打开。`hires.api.json` 是这张画布导出的 prompt，可选模块处于旁路状态；`hires.report.json` 是各项检查的结论和测量值。前两行写在 stderr：一条布局警告（这张画布又宽又扁）和摘要。

## 给 agent 用

1. `cwc guide` 打印编写参考：输入格式、每个 `_meta` 和 `_workflow` 键及其效果，外加一个带可选模块的例子。
2. `cwc node TYPE` 显示一个节点的输入、控件和默认值、每个下拉框的可选值、输出；`cwc node --search KW` 用来找类型。
3. 写好 API prompt，运行 `cwc compile GRAPH.json -o out`。错误和警告写到 stderr，每条带一个提示；`--json` 在 `finding_list` 里给出同样的列表，旁边是计数 `findings`。
4. 改了再编译，直到摘要行写着 `ok`；`cwc render` 把画布画成 SVG，不开 ComfyUI 也能看。

拼错会得到建议：

```text
E2: node 'sampler': KSampler has no input 'sampler_nme'; did you mean 'sampler_name'?
  hint: valid inputs: model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise
cwc compile: removed out/steps.ui.json, out/steps.api.json from an earlier run, which this run does not replace
cwc compile: 1 error(s); see out/steps.report.json
wrote out/steps.report.json
```

图文件叫 `<name>.json` 时，`compile` 在输出目录里留下：

- 总有 `<name>.report.json`。成功的运行还写 `<name>.ui.json`、`<name>.api.json`，加 `--all-enabled` 时还有 `<name>.full.api.json`。
- 上一次运行留下的同名文件里，这次不写的会被删掉，stderr 上有一行说删了哪些：失败的运行只留下报告，旧画布不会被当成新的。
- 成功的运行在 stderr 以一行摘要结束：用了哪个预设（`auto` 时还说为什么）、组数、可选模块数、警告数，以及预览画布的 `cwc render` 命令。

所有命令用同一套退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 完成 |
| 1 | 有检查没过，只写了报告；`cwc node --search` 没有匹配时也是 1 |
| 2 | 命令行写错 |
| 3 | 输入读不了、格式不对，或没过编写检查；`cwc node TYPE` 给了未知类型时也是 3 |
| 4 | 节点定义拿不到、读不了或格式不对 |
| 5 | 有输出写不进去；这次运行的文件尽量删掉了 |
| 70 | cwc 自己的 bug，stderr 上一行说明 |

## 输入是什么

一张 API 图：每个节点是一个 `class_type` 加它的 `inputs`，连线写成 `[节点, 输出]`。节点 id 可以用名字，声明了默认值的输入可以不写。`_meta` 里的提示可写可不写；不写时 `cwc` 从图的结构推断分组，`hires.json` 就是这样。下面是 [`docs/images/src/landscape.json`](docs/images/src/landscape.json) 里的一个节点：

```json
"lora": {
  "class_type": "LoraLoader",
  "inputs": {
    "model": ["ckpt", "MODEL"], "clip": ["ckpt", "CLIP"],
    "lora_name": "film_grain.safetensors",
    "strength_model": 0.6, "strength_clip": 0.6
  },
  "_meta": {"title": "Style LoRA", "ui_optional": true}
}
```

`ui_optional` 把节点放进一个可选模块，画布交出去时这个模块是旁路的（下面各图里 Optional models 组中变暗的 Style LoRA）。全部的键见 `cwc guide`。

## Controls 组

Controls 组是用户每次运行都要改的地方。正负提示词和空 latent（图的尺寸）会自己进去。别的节点在 `_meta` 里写 `"ui_role": "cockpit"` 就进去，写 `"ui_role": "pipeline"` 则留在外面。比如要把步数做成控件，就让采样器的步数来自一个 primitive 节点（`cwc node PrimitiveInt` 能看到它的控件 `value`）：

```json
"steps": {"class_type": "PrimitiveInt", "inputs": {"value": 28},
          "_meta": {"title": "Steps", "ui_role": "cockpit"}},
"sampler": {"class_type": "KSampler", "inputs": {"steps": ["steps", 0], ...}}
```

在一张普通的文生图上，Controls 组里于是有两个提示词、空 latent 和一个叫 Steps 的节点。去掉 `ui_role` 那一行，这个节点就待在 Sampling 组里。

成功编译的报告说明哪些 `_meta` 键在画布上体现出来了。在 `metrics.authoring_keys` 下，每个键的 `nodes` 列出编译结果与它相符的节点，`no_effect_on` 列出写了这个键、画布上却没体现的节点。一个键如果只是重复 `cwc` 本来就会做的事（比如给提示词写 `"ui_role": "cockpit"`），也算在 `nodes` 里。上面那张图的报告是：

```json
"authoring_keys": [
 {"key": "_meta.title", "nodes": ["steps"]},
 {"key": "_meta.ui_role", "nodes": ["steps"]}
]
```

暂时没有效果的键不在这里列出，它们在 E8 那条结论里。

## 五种预设

`--preset` 决定怎么排，`cwc presets` 说明每种的样子和什么时候用。下面是同一张十二个节点的图 `landscape.json` 在各个预设下的样子：

### bento

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/bento-dark.png">
  <img src="docs/images/bento-light.png" width="830" alt="bento 预设">
</picture>

默认预设。先是 Controls，然后模型和提示词并排，再是采样、后处理和输出，排成一条主行；控制分支和可选模块在下面第二行。主行比自身高度的 2.4 倍还宽时，折到下面新起一行。

### swimlanes

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/swimlanes-dark.png">
  <img src="docs/images/swimlanes-light.png" width="830" alt="swimlanes 预设">
</picture>

先是 Controls，其余各组按数据流顺序排成一行，每组一列；控制分支站在它所控制的那个阶段的那一列里。

### cockpit-split

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/cockpit-split-dark.png">
  <img src="docs/images/cockpit-split-light.png" width="830" alt="cockpit-split 预设">
</picture>

和 swimlanes 一样，不同的是 Controls 组里除了核心控件（提示词、参考图、尺寸、种子）还有别的控件时，其余的放进紧挨在下面的第二个框。

### tower-preview

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/tower-preview-dark.png">
  <img src="docs/images/tower-preview-light.png" width="830" alt="tower-preview 预设">
</picture>

和 bento 一样，另外留出大预览图：输出组里有显示预览的节点时，这个组至少 880 × 680 像素，预览节点撑满它。

### stepped

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/stepped-dark.png">
  <img src="docs/images/stepped-light.png" width="830" alt="stepped 预设">
</picture>

组名是 Step 1、Step 2 这样的步骤，模型组和 Controls 组上下叠在第一列。适合要人按顺序看的图，比如教程。

### auto

`--preset auto` 按顺序取第一条成立的规则，摘要行和报告会说用的是哪条：

| 图 | 预设 |
|---|---|
| 不超过 16 个节点，且没有可选模块 | `tower-preview` |
| 三条及以上控制或蒙版分支 | `swimlanes` |
| 60 个节点及以上 | `cockpit-split` |
| 其余 | `bento` |

不给 `--preset` 时，`cwc` 用图里的 `_workflow.layout.preset`，没有就用 `bento`。

## 命令

```text
cwc node     [TYPE | --search KW] [--nodedefs FILE|URL] [--json]
cwc compile  GRAPH.json -o DIR [--nodedefs FILE|URL] [--preset NAME]
             [--all-enabled] [--allow-unknown] [--json]
cwc presets  [--json]
cwc guide
cwc learn    FILE [FILE ...] -o FILE [--nodedefs FILE|URL] [--json]
cwc render   GRAPH.ui.json -o OUT.svg [--nodedefs FILE|URL] [--json]
```

| 命令 | 作用 |
|---|---|
| `node` | 显示一个节点定义（输入、控件和默认值、每个下拉框的可选值、输出），或按名字、显示名、分类搜索。未知的 TYPE 退出码 3，并在 stderr 给出最接近的类型；搜索没有匹配时退出码 1，stdout 什么都不写，`--json` 下是 `[]`。 |
| `compile` | 把 API 图编译成画布和它的 API prompt。加 `--all-enabled` 还会写出 `<name>.full.api.json`，即所有可选模块都打开时的 prompt。 |
| `presets` | 列出各个预设、它们的样子、什么时候用，以及 `auto` 的规则。 |
| `guide` | 打印编写指南。 |
| `learn` | 从成对的画布和 API prompt 里学习控件模板。 |
| `render` | 不用浏览器，把画布画成 SVG。 |

核心节点的定义已经打包在内。用到自定义节点时，用 `--nodedefs` 传入 `/object_info` 导出的文件，或者一个正在运行的 ComfyUI 服务器的地址。给地址时读的是 `<URL>/api/object_info`，最多跟三次重定向，只跟到 `http` 或 `https`，并且不会从 `https` 跳到 `http`。`cwc` 不判断服务器或重定向指向哪里：通常是本机或局域网里的服务器，所以只填你信任的服务器。

<details>
<summary>编写检查的结论代码</summary>

报告里的 `title` 字段是同样的文字；其他代码族检查的是画布。

| 代码 | 含义 |
|---|---|
| E1 | 未知的节点类型 |
| E2 | 这个节点类型没有这个输入名 |
| E3 | 缺了没有声明默认值的必填输入 |
| E4 | 连线两端类型不兼容 |
| E5 | 字面值类型不对、超出范围，或不是已知的选项 |
| E6 | 连线指向不存在的节点或输出，或者输出名有歧义 |
| E7 | 图的结构无效（有环、id 不对、`_workflow` 不对、可选组混搭） |
| E8 | 编写键被接受，但暂时没有效果 |

E7 也管 `cwc` 不认识的 `_meta` 键：以 `ui_` 开头的是错误，并给出最接近的键；其他的是警告。同一个节点上一个 `_meta` 键被另一个键盖过时，是一条 E7 警告，点出两个键并说该删哪个；退出码和 `ok` 不变。`ui_role: cockpit` 盖过 `ui_group`，`ui_group` 盖过 `ui_role: plumbing`，`ui_optional` 盖过 `ui_role` 的 `cockpit` 和 `plumbing`。

`cwc` 接受但暂时不用的键（`ui_locked`、`ui_expose`、`ui_note`、`_workflow.title`、`_workflow.locale`、`_workflow.groups.<id>.note`）每次编译汇成一条 info 结论 E8：stderr 上一行，列出这些键和设了它们的节点；成功编译的报告里，`data.keys` 逐个列出这些键。退出码和 `ok` 不变。

</details>

<details>
<summary>相对本仓库第一版改变了的行为</summary>

- `cwc node TYPE` 给了未知类型时退出码是 3，以前是 4。搜索没有匹配时退出码是 1，以前是 0。
- 以 `ui_` 开头但不是 cwc 的键的 `_meta` 键是错误（E7，退出码 3）。以前是警告，拼错的键什么也不做。
- 暂时没有效果的键会在 E8 info 结论里点名。以前什么都不说。
- 同一个节点上被另一个键盖过的 `_meta` 键是一条 E7 警告。以前被盖过的键什么也不做，也没有任何提示。

</details>

<details>
<summary>输入上限</summary>

输入只在入口检查一次，超过下面某个上限就拒绝，消息里点名是哪个上限。这些上限远高于真实的工作流（大画布也就几百个节点、几 MB）。JSON 文件先按文件上限读入，再按文档上限拒绝。数值是 `src/cwc/ingest/limits.py`、`src/cwc/ingest/png.py`、`src/cwc/nodedefs/derive.py`、`src/cwc/nodedefs/registry.py` 和 `src/cwc/nodedefs/learn.py` 里的常量。

| 输入 | 上限 | 超过时 |
|---|---|---|
| 一个输入文件（画布、编写文件、PNG），读入量 | 256 MiB，管道和还在变大的文件也一样 | I3，退出码 3 |
| 一个 JSON 文档，或一个 PNG 块的文本 | 64 MiB | I3，退出码 3 |
| 一个 PNG 的压缩文本块解压后合计 | 64 MiB | I3，退出码 3 |
| JSON 数组和对象的嵌套 | 64 层 | I3，退出码 3 |
| 画布节点数，或编写条目数 | 5000 | I3，退出码 3 |
| 画布连线数 | 20000 | I3，退出码 3 |
| 画布节点、连线、槽或 reroute 的 id（整数或数字串） | 32 位数字 | I1，退出码 3 |
| 由数字组成的编写键 | 32 位数字 | E7，退出码 3 |
| 画布坐标或尺寸（节点、组、reroute） | 绝对值最多 1e9 | I1，退出码 3 |
| 节点定义来源（文件、URL 响应，或 gzip 解压后） | 64 MiB，嵌套 64 层 | D1，退出码 4 |
| URL 来源：跟随的重定向，只跟到 http 或 https，不从 https 跳到 http | 3 | D1，退出码 4 |
| 节点定义里 Autogrow 的 `min` 或 `max` | 1000 | D1，退出码 4 |
| 一个 Autogrow 组的槽数（模板输入数乘以 `max`） | 10000 | D1，退出码 4 |
| `learn`：一个节点的控件值个数乘以候选名个数，对齐时 | 250000 格 | 这个节点只按长度投票（`too_large`），不报错 |

比浮点数能表示的还大的整数也不再是内部错误：在控件里它得到通常的范围结论，在画布坐标里它就是超过了上面的上限。

</details>

## 现状

预览阶段（pre-alpha）。已经能用的：完整的编译流程（normalize、instantiate、annotate、layout、route、verify），五种预设，以及上面六个命令。

计划中，`cwc --help` 里还没有：只改几何、不动其他内容地整理已有画布，画布内的注释和导览，PNG 的嵌入和提取，以及对正在运行的前端做实时检查。

```text
cwc beautify messy.ui.json [--preset ...] [--regroup] -o out/
cwc check    x.ui.json [--api x.api.json] [--live URL] [--publish]
cwc embed    x.ui.json --image cover.png -o x.workflow.png
cwc extract  image.png -o out/
cwc snapshot --from URL [--via-frontend] -o nodedefs.json
```

原理见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)（模块、布局规则、退出码）、[`docs/m0-findings.md`](docs/m0-findings.md)（真实前端的行为）、[`docs/m1-templates.md`](docs/m1-templates.md)（控件模板及其评估）和 [`docs/m2-frontend-facts.md`](docs/m2-frontend-facts.md)（前端怎么存组、节点框和连线），这些文档是英文的。

本页和 `docs/` 里的路径（`src/`、`tests/`、`tools/`）都相对于仓库根目录，里面的命令也在那里运行。`python3 -m unittest discover -s tests` 跑测试。只有 `tools/oracle/` 里的真实前端检查和 [`tools/make_readme_images.py`](tools/make_readme_images.py) 需要 Playwright，后者从一个正在运行的 ComfyUI 服务器生成本页的图。

<details>
<summary>里程碑</summary>

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 真实前端的对照工具；测量布局依赖的常数（`tools/oracle/`、`docs/m0-findings.md`） | 完成 |
| M1 | 实例化和等价性；API 图变成导出同一个 prompt 的画布（`docs/m1-templates.md`） | 完成 |
| M2 | 五种预设、布局和原生连线路由检查（`docs/ARCHITECTURE.md`、`docs/m2-frontend-facts.md`） | 完成 |
| M3 | reroute 路由第二轮、画布内注释和导览、无损整理已有画布、嵌入和提取 | 计划中 |
| M4 | 给编程 agent 用的技能包（已完成，在 [`skills/`](skills/comfyui-workflow-compiler/SKILL.md)）和一组示例工作流（计划中） | 部分完成 |
| M5 | 首个版本之后的可选扩展 | 计划中 |

</details>

## 许可

Apache License 2.0（[`LICENSE`](LICENSE)）。`cwc` 为你写出的文件（现在是 `.ui.json`、`.api.json`、`.svg` 和报告；相应命令有了之后还有 `.guide.md` 和嵌入了工作流的 `.png`）归工作流的作者所有，不受这份许可约束，见 [`LICENSE-OUTPUTS.md`](LICENSE-OUTPUTS.md)。

本仓库没有复制任何 GPL 或 AGPL 项目的代码或受版权保护的数据。打包的核心节点快照只保留互操作所需的事实（名称、类型、顺序）。
