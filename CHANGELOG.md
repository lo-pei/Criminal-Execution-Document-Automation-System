# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

本项目在 v1.0.0 之前，`MINOR` 用于新增能力层（如接入 PDF 通道），`PATCH` 用于规则库与缺陷修复。**规则库单独标记 `rule_version`**，与程序版本解耦，允许现场热替换。

## [Unreleased]

### Added
- 离线交付能力：`packaging/cedas.spec`（PyInstaller onedir）、`packaging/launcher.py`、`tools/build_offline.py`
- 构建闸门：前置检查（平台/位数/Python 版本/tcl-tk/依赖/PyInstaller）、打包后冒烟测试、SBOM、SHA256 校验和
- `cedas doctor [--json]` 子命令：路径、依赖、可写性自检，供内网现场排障
- 规则两层加载：`<只读>/resources/rules` 基线 + `<可写>/resources/rules` 现场补丁，同名文件覆盖
- 文档 `07-离线部署与交付`、`requirements-build.txt`
- ADR-010（onedir 零安装交付）、ADR-011（只读/可写分离与规则覆盖）、ADR-012（固定构建环境）

### Fixed
- **`config.py` 将只读资源与可写数据混为一处**，打包后数据库与生成文书会落入 `sys._MEIPASS` 临时目录，
  程序退出即丢失。已拆为 `BUNDLE_DIR`（只读）与 `APP_HOME`（可写，可用 `CEDAS_HOME` 覆盖）。
- 交付 zip 缺少版本目录前缀，解压时上千文件直接铺在当前目录。

### Planned
- 接入 PDF 通道（PyMuPDF `get_text("dict")`），恢复页码级溯源
- 剩余首部锚点规则（出生日期、身份证号、住址、强制措施）
- 主文锚点规则与多被告人切分
- docxtpl 模板映射与占位符校验器
- Tkinter 台账与复核界面
- 目标机（4GB 内存 / 旧 CPU / Win10）实机实测

## [0.1.0] - 2026-10-31

### Added
- 工程骨架：src layout、分层目录、pyproject（ruff/mypy/pytest 配置）、CI 工作流
- `domain` 层数据模型：`Case` / `Defendant` / `Crime` / `CustodySegment` / `FieldValue` / `ProseParagraph`
- `calc` 层：中文刑期解析器、刑期届满日与折抵计算、一致性校验
- `ingest` 层：TXT 读取（编码自动识别）、清洗与段落重建、字符规范化
- `extract` 层：三段锚点定位器、规则注册表、案号抽取规则（首条可运行规则）
- `storage` 层：SQLite 台账建表与基础读写
- `render` 层：模板映射表加载、docxtpl 渲染封装、未解析占位符检测
- `ui` 层：Tkinter 主窗口骨架（台账列表 + 复核面板占位）
- 文档集：字段字典 spec v0.1、可行性评估、系统架构、ADR、样本规范、交接手册、路线图
- 真值填写表生成器 `tools/make_truth_template.py`

### Known Limitations
- 样本通道为人工提取 TXT，`ProseParagraph.page` 恒为 `None`
- 抽取层仅实现案号一条规则，其余字段为 `NotImplemented` 占位
- UI 为骨架，未接真实数据流
- 未打包，未在目标机实测
