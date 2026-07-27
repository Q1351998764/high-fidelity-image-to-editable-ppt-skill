# High-Fidelity Image to Editable PPT Skill

把幻灯片截图、图片型 PPT/PPTX 和扫描 PDF 重建为对象级可编辑的 PowerPoint，并通过确定性校验约束视觉保真度。

## 主要增强

- 从源像素追踪数据曲线和图例曲线，并拟合为可编辑三次贝塞尔路径。
- 将曲线描边与面积填充分离，避免闭合路径改变曲线端点。
- 支持 SVG/EMF 图标矢量化，并保留素材来源记录。
- 支持全局包含、对齐、等距、避让和非重叠约束。
- 支持真正的 DrawingML 线端箭头，校验轴线起止标记。
- 区分方括号、圆括号、花括号和测量括号，校验样式与方向。
- 通过 `geometry_inventory` 对轴线、括号、图例和结构曲线执行对象级源图—渲染图边缘比对。
- 校验 PPTX ZIP、关系、媒体哈希、OOXML 核心 Schema 和 PowerPoint 可打开性。

## 安装

```bash
npx -y skills@latest add Q1351998764/high-fidelity-image-to-editable-ppt-skill \
  --skill high-fidelity-image-to-editable-ppt \
  --global
```

安装或更新随 Skill 提供的 `editppt` CLI：

```bash
uv tool install --force --editable ./skills/high-fidelity-image-to-editable-ppt/cli
editppt setup
editppt doctor
```

也可以使用 `pipx install --force --editable` 安装 CLI。

## 使用

在 Codex 中附加图片、PDF 或图片型 PPTX，然后调用：

```text
$high-fidelity-image-to-editable-ppt 把这张图片还原成对象级可编辑 PPT。
```

本 Skill 用于还原已有视觉幻灯片，不用于从文章或大纲创作全新演示文稿。

## 来源与致谢

本项目基于 [ningzimu/image-to-editable-ppt-skill](https://github.com/ningzimu/image-to-editable-ppt-skill) 开发，感谢原作者 **ningzimu** 提供完整的图片转可编辑 PowerPoint 工作流、运行时和开源基础。

本派生版本保留原项目的 MIT License 与版权声明，并在此基础上增加贝塞尔曲线追踪、线端箭头、括号语义、图例基线关系、对象级视觉保真校验、布局约束以及更严格的 OOXML 检查。

## License

[MIT](LICENSE)
