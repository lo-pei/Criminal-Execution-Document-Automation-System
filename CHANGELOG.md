# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

本项目在 v1.0.0 之前，`MINOR` 用于新增能力层（如接入 PDF 通道），`PATCH` 用于规则库与缺陷修复。**规则库单独标记 `rule_version`**，与程序版本解耦，允许现场热替换。

## [Unreleased]

### Added
- 离线交付能力：`packaging/cedas.spec`（PyInstaller onedir）、`packaging/launcher.py`、`tools/build_offline.py`
- 构建闸门：前置检查（平台/位数/解释器版本/tcl-tk/依赖/PyInstaller）、打包后冒烟测试、SBOM、SHA256 校验和
- 构建目标 profile：`win7`（默认，Win7+Win10 通用）与 `win10`；版本不符直接拒绝，
  `--allow-unsupported-python` 仅用于验证链路且产物标 `-unsupported`
- `requirements-build-win7.txt` / `requirements-build-win10.txt`：两套固定配方，功能一致
- `cedas doctor [--json]` 子命令：路径、依赖、可写性自检，供内网现场排障
- 规则两层加载：`<只读>/resources/rules` 基线 + `<可写>/resources/rules` 现场补丁，同名文件覆盖
- 文档 `07-离线部署与交付`（含 Win7 补丁、Office 兼容、现场拦截清单）
- ADR-010（onedir 零安装交付）、ADR-011（只读/可写分离与规则覆盖）、ADR-012（固定构建环境）、
  ADR-013（解释器降至 3.8 以覆盖 Win7）
- PDF 通道回归：`tools/make_sample_pdf.py` 把 TXT 样本按真实版式排成合成 PDF（`examples/`），
  `tests/test_pdf_reader.py` 断言 TXT 与 PDF 两条通道的段落逐条一致
- `RawBlock.page_height`：页眉判定需要把纵坐标换算为页面相对位置，PDF 通道填充，TXT 通道为 `None`
- B 组抽取 `extract/defendant.py`：被告人身份（性别/出生日期/身份证号/民族/文化程度/住址/羁押场所）、
  强制措施段（拘留→逮捕，支持「同年」省略年份）、罪名与主刑月数、罚金（中文与阿拉伯数字）、缓刑标记；
  刑期区间按最早羁押日调用 `calc.compute_span` 计算，本层不做日期运算
- 公开语料回归：`data/public-corpus/` 收录 36 份北大法宝公开判决书的主文摘录（含原文链接），
  `tools/corpus_regress.py` 从主文解析「刑期 + 起止日」并用 `calc` 复算比对；
  `tests/test_corpus_regress.py` 把结果固化为计算层防回退闸门
- `docs/08-公开语料与计算回归`：语料来源与边界、真实文书暴露的六个形态、回归结论
- 真值表新增「待确认口径」工作表：六项只能由办案人员定的口径（起算日、生效日、上诉期顺延、
  书写格式、缓刑是否走本系统、数罪并罚起算）
- `ParsedTerm.days` / `SentenceSpan.days` / `Defendant.merged_days`：刑期的「日」分量独立保存
- 端到端生成 `src/cedas/pipeline.py` + `cedas render` 子命令：一份判决书 → 抽取 → 刑期 →
  模板填充 → 归档命名（含版本探测）→ 台账写入，一条命令完成
- `extract/case_info.py`：D 组 4 字段——判决日期（落款最后一个日期）、审判组织、书记员、
  公诉机关；中文年份逐位读（「二〇二三」→ 2023），不误用位值解析
- `tools/make_demo_template.py`：生成合成执行通知书模板，`--check` 比对占位符与映射表；
  真实模板属受控材料不入库，无模板时整条链无法在 CI 上跑
- `render.find_mapping` / `render.find_template`：映射表与模板也走「只读基线 + 现场补丁」
  两层查找，与规则库同构；找不到时报出应放目录与现有文件
- `config.mapping_search_dirs` / `template_search_dirs`，并把两层查找抽成 `_search_dirs`
- `ingest.read_document`：通道选择的唯一实现，命令行与流水线共用
- 阻断码体系 `E-*` / 告警码 `W-*`：核心字段缺失不生成文书，报告写明原因与**起算日来源**
- 文档 `09-端到端渲染与归档`：链路图、阻断码表、起算日两级来源、归档版本规则、模板替换步骤

### Changed
- **解释器下界由 3.9 降至 3.8**：目标机含 Windows 7，CPython 3.9 起不再支持 Win7（ADR-013）
- 依赖随之降档：`pymupdf 1.23.26`、`python-docx 1.1.2`、`MarkupSafe 2.1.5`、`PyInstaller 6.10.0`
  （均已实测有 cp38 轮子）
- 内存预算由 4GB 下调为 **2GB**（Win7 机器往往更老）：界面不得一次性加载全部台账或全文高亮
- CI 增加 Python 3.8 矩阵，并提供构建前置检查 job

### Fixed
- **起算日回退后未写回 `defendant.span`，导致报告显示起止日而文书该两格空白**。映射表的
  起日/止日/释放日是派生字段，读的是 `defendant.span`；流水线算完必须回写。
  `tests/test_pipeline.py::test_start_date_falls_back_to_effective_date` 锁住该行为。
- **刑期解析丢掉「日」分量**，拘役一个月十五日按一个月算，届满日差 15 天**。真实文书
  （2026）皖0881刑初147号载明「判处拘役一个月十五天……自2026年9月30日至2026年11月13日止」，
  旧逻辑得 2026-10-29。现按「先加月、再加日、最后减 1 日」计算，与文书吻合；
  先加日再加月会得 2026-11-14，亦不正确。
- **页眉判定只靠「全文重复 ≥3 次」，2 页文书的页眉删不掉**，还会被关键字启发式判成"标题"混进段落列表。
  PDF 通道改为「页面顶部 10% + 字号小于正文 90%」判定，字号与正文相当时退回跨页重复；
  TXT 通道保持重复计数，两条线索都不成立时保留，避免误删首页大标题。
- **`config.py` 将只读资源与可写数据混为一处**，打包后数据库与生成文书会落入 `sys._MEIPASS` 临时目录，
  程序退出即丢失。已拆为 `BUNDLE_DIR`（只读）与 `APP_HOME`（可写，可用 `CEDAS_HOME` 覆盖）。
- 交付 zip 缺少版本目录前缀，解压时上千文件直接铺在当前目录。

### Planned
- 接入 PDF 通道（PyMuPDF `get_text("dict")`），恢复页码级溯源
- 剩余首部锚点规则（出生日期、身份证号、住址、强制措施）
- 主文锚点规则与多被告人切分
- docxtpl 模板映射与占位符校验器
- Tkinter 台账与复核界面（按 2GB 内存约束设计，分页加载）
- 目标机实测：Win10 与 Win7 各一台，记录冷启动、单份耗时、内存峰值

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
