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
- 通过全页反向覆盖发现未写入 Manifest 的遗漏轴线、箭头、小标记和组件。
- 对图表周边的 `t`、`%`、刻度、单位和微型箭头执行多尺度漏检检查。
- 同时验证“贝塞尔—Trace”和“Trace—源图色彩笔画”，阻止只追踪到一小段错误曲线。
- 使用宽松的 Lab 色差、彩度保留和色格序列检查；允许轻微色偏，但拒绝彩色组件变黑白或状态格数量改变。
- 校验 PPTX ZIP、关系、媒体哈希、OOXML 核心 Schema 和 PowerPoint 可打开性。

## 最简单的安装方式：直接交给 Codex

把下面这段话连同仓库地址发给 Codex，它可以自行安装 Skill、部署 CLI 并检查依赖：

```text
请安装这个 Codex Skill：
https://github.com/Q1351998764/high-fidelity-image-to-editable-ppt-skill

请完成以下工作：
1. 安装名为 high-fidelity-image-to-editable-ppt 的 Skill。
2. 定位安装后的 Skill 根目录，并用 uv tool 或 pipx 以 editable 模式安装其中的 cli 目录。
3. 运行 editppt setup 和 editppt doctor，补齐缺失的本地依赖。
4. 检查是否配置 PaddleOCR-VL Token；如果没有，告诉我申请地址和配置命令，但不要要求我发送或提交任何私钥。
5. 安装完成后告诉我是否需要重启 Codex 或新开任务才能识别 Skill。
```

Skill 安装器只负责把 Skill 文件放到正确位置；`editppt` CLI 及其 Python 依赖仍需安装。上面的提示词要求 Codex 把两部分一起完成。

## 命令行安装

需要 Node.js 22.20 或更高版本：

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

安装完成后建议重启 Codex 或新开一个任务，让 Skill 索引重新加载。

## 百度 PaddleOCR-VL Token

PaddleOCR-VL Token 是默认高保真流程的**强制质量门禁**。它用于识别页面文字内容、修正文本框边界、字体大小和同级文字分组。没有 Token、调用失败或 OCR 结果退化时，程序仍会生成本地 `builtin-ink` 诊断数据，但 `editppt run next` 会停在 `ocr_quality_gate`，不会重建或派发页面。

申请地址：

[百度飞桨 AI Studio Access Token](https://aistudio.baidu.com/account/accessToken)

个人免费额度通常足够普通 PPT 转换。申请后配置：

```bash
editppt config --paddle-ocr-token "<your-paddle-ocr-token>"
editppt doctor
```

如果已经创建了转换任务，再执行：

```bash
editppt run hints <run-directory>
```

这会重新生成当前任务的 `text_hints.json` 和 `text_hints.png`。Token 保存在用户级 `~/.editppt/config.yaml`，不要写进项目、README、提示词、Manifest 或 Git 仓库。使用外部 OCR 时，当前转换页图片会发送给 OCR 服务；保密材料或要求完全离线时应明确拒绝外部 OCR。

如果用户明确选择完全离线并接受文字质量下降，才可记录一次可审计的授权：

```bash
editppt run allow-offline-hints <run-directory> \
  --reason "用户明确要求完全离线并接受 OCR 质量下降"
```

Agent 不得自行填写这一授权，也不能把 PaddleOCR 调用失败静默当成离线许可。

## 图像处理后端

复杂背景修复、图标和装饰组件分离需要图像生成/编辑后端，按以下顺序使用：

1. Codex 环境内置的 `image_gen.imagegen`，可用时无需单独配置 API Key。
2. Codex OAuth 图像接口。
3. 用户已经配置的 OpenAI 兼容图像 API。

第三种方式可用以下命令配置：

```bash
editppt config \
  --api-key "<key>" \
  --base-url "<openai-compatible-base-url>" \
  --model "<image-model>"
```

API Key 同样只保存在用户级配置中，不应提交到 Git。

## 当前处理流程

1. **输入归一化**：`editppt prepare` 将图片、多张图片、PDF 或图片型 PPTX 转成逐页 `source.png`，保留原始页面比例、页序和备注映射。
2. **OCR 与强制门禁**：优先通过 PaddleOCR-VL 生成文字内容、边界、字形高度和建议字号；缺 Token、调用失败或退化为本地墨迹检测时阻止页面重建，除非用户显式授权离线模式。
3. **完整页面清单**：先清点背景、文字、公式、图标、照片、箭头、坐标轴、括号、图例和全部结构组件，再决定每个对象的来源。
4. **背景处理**：纯色、渐变、面板和规则结构使用 PowerPoint 原生对象；复杂且被文字或图标遮挡的背景通过图像编辑生成干净底图。
5. **前景组件分离**：图标、人物、设备、装饰箭头和其他语义视觉对象通过素材表分离，禁止用 Emoji、相似图标或直接截图替代。
6. **对象级重建**：文字变为真实文本框；面板、表格、坐标轴、条形块和连接线变为 PowerPoint 原生对象；公式通过 LaTeX 渲染。
7. **曲线和矢量化**：所有非填充曲线（数据、图例和结构曲线）都必须从源像素追踪并拟合为可编辑三次贝塞尔路径；验证 Trace 对源色彩笔画的支持率、主方向跨度和源笔画覆盖率；合规的分离图标可进一步转为 SVG/EMF。
8. **全局排版优化**：执行包含、对齐、等距、流式排列、非重叠和图标避让约束，并对未声明关系的文本框执行全局碰撞检测。
9. **开放世界保真校验**：除了检查 Manifest 已声明对象，还反向查找预览无法解释的源图边缘；图表必须登记微型文字和箭头。`geometry_inventory` 检查轴线、括号、图例和结构曲线，每个图片对象必须被 `visual_inventory` 逐一覆盖。
10. **容差型颜色语义校验**：图标检查彩度保留和宽粒度色相分组，小状态格使用默认 DeltaE 34、最高 45 的宽松容差核对填充状态。压缩、抗锯齿、亮度和轻微色偏不会因逐像素差异而失败。
11. **页面构建与验证**：从 `manifest.json` 生成 `page.pptx`、预览图和对照图，验证文字、媒体来源、哈希、关系和 OOXML Schema。
12. **记录与汇总**：每页通过 `editppt run record` 后，再由 `editppt run finalize` 从页面 Manifest 重建最终 PPTX，失败页面不能进入最终文件。

## 需要安装的软件

| 软件或组件 | 要求 | 用途 |
|---|---:|---|
| Codex 或兼容的 Skill Agent | 必需 | 执行 Skill 工作流和图像工具调用 |
| Python | 3.10+，必需 | 运行 `editppt` CLI |
| uv tool 或 pipx | 二选一，必需 | 隔离安装 CLI 和 Python 依赖 |
| Node.js / npm | 22.20+，使用 `npx skills` 时必需 | 从 GitHub 安装 Skill；由 Codex 自行安装时可使用其内置安装渠道 |
| Git | 推荐 | 克隆、更新和开发 Skill |
| Microsoft PowerPoint | Windows 上强烈推荐 | 真实打开、渲染和检查是否触发修复提示；没有 PowerPoint 仍可构建 PPTX |
| PaddleOCR-VL Token | 默认流程必需；显式离线授权可豁免 | 内容感知 OCR、文本框和字号校准 |
| 图像生成/编辑后端 | 复杂页面必需 | 背景修复、图标与装饰组件的素材表分离 |
| Inkscape | 可选 | 将 SVG 额外导出为 EMF |
| ImageMagick | 可选 | 在本地预览中渲染 SVG/EMF；不影响 PowerPoint 自身显示 SVG |
| TeX 引擎及转换工具 | 有复杂公式时可选 | 将 LaTeX 公式渲染为 SVG/PNG/PDF |

安装 CLI 时会自动安装 PyMuPDF、Pillow、OpenAI SDK、PyYAML、NumPy、Requests 和 OpenCV Headless，无需逐个安装。

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
