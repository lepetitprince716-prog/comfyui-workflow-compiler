---
name: comfyui-workflow-compiler
description: Turn a ComfyUI API prompt you wrote into a grouped, laid-out canvas workflow with cwc. Use when a task asks for a ComfyUI workflow file a person will open in ComfyUI.
---

# comfyui-workflow-compiler

1. Run `cwc guide` once and follow it: it is the reference for the input format, every `_meta`
   and `_workflow` key and its effect, and a worked example.
2. Write the API prompt; check node inputs with `cwc node TYPE`.
3. `cwc compile GRAPH.json -o out`. Fix every error and warning on stderr (each has a hint) and
   compile again until the summary line says `ok` with the warnings you accept.
4. `cwc render out/GRAPH.ui.json -o GRAPH.svg` to look at the canvas. `cwc presets` if the layout
   should change.
