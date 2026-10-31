# 贡献与开发规范

## 1. 命名规范

| 对象 | 规则 | 示例 |
|---|---|---|
| 包 / 模块 | 全小写，单词间下划线，单数 | `field_dict.py` `term_parser.py` |
| 类 | PascalCase，名词 | `CustodySegment` `TermParser` |
| 函数 / 方法 | 全小写，动词开头 | `parse_term()` `compute_end_date()` |
| 变量 | 全小写，下划线 | `principal_type` `merged_sentence` |
| 常量 | UPPER_SNAKE | `MAX_PARAGRAPH_LEN` `DEFAULT_ENCODINGS` |
| 私有 | 前缀单下划线 | `_normalize_fullwidth()` |
| 测试 | `test_<被测模块>_<行为>` | `test_term_parser_rejects_ambiguous()` |
| 规则文件 | `{层}_{字段}.v{版本}.json` | `head_case_no.v1.json` |
| 样本 | `{样本编号}__{案号}.txt` | `S001__京0105刑初123号.txt` |
| 分支 | `feat/` `fix/` `docs/` `rule/` + 短横线 | `feat/pdf-ingest` `rule/head-birthdate` |

字段的代码标识一律用英文 snake_case，与 `docs/00-字段字典-spec-v0.1.md` 的字段表严格对应；中文名只出现在 UI 与文档中。

## 2. 提交信息

遵循 Conventional Commits：

```
<type>(<scope>): <subject>

type:  feat | fix | docs | refactor | test | chore | rule | perf
scope: domain | ingest | extract | calc | render | storage | ui | docs | ci
```

示例：

```
feat(extract): 新增首部出生日期规则 head_birthdate.v1
fix(calc): 修正月末日起算时届满日多算一天
rule(extract): 放宽案号正则以覆盖刑终/刑再案件
docs(handoff): 更新当前阶段与下一步
```

**一条提交只做一件事。** 规则变更必须与对应测试样本同一次提交，不允许"先提交规则、稍后补测试"。

## 3. 分层约束（review 必查）

依赖方向单向向下：`ui → storage / render / extract → ingest / calc → domain`。

- `domain` 不得 import 任何上层模块，也不得依赖第三方库。
- `calc` 不得 import `ingest`——计算层不认识文本，只认识数值和日期。
- 跨层调用一律走显式接口，禁止在 `ui` 里直接写正则。

## 4. 规则开发的硬性要求

1. **一条规则一个文件**，附最小测试样本与 `evidence` 字段，写明"依据哪份样本的哪段文字"。禁止只写正则不写依据。
2. **禁止硬编码样本特征**：具体案号、具体日期、具体被告人姓名不得出现在正则里。review 时逐条检查。
3. **一次只改一类失败**。修 A 字段不得顺手放宽 B 字段的正则。
4. 规则入库前跑全量回归：`python -m pytest -q`。
5. 规则 JSON 必须带 `version` 与 `updated_at`，程序启动时校验 schema 版本。

## 5. 测试要求

- 每个公开函数至少一个正常路径 + 一个边界/异常路径用例。
- 日期与刑期相关必须有月末日、闰年、跨年用例。
- 新增规则必须同时提交 golden case（输入文本片段 + 期望字段值）。
- 本地提交前：`python -m pytest -q && python -m ruff check src tests`。

## 6. 数据安全红线

**不得**将真实判决书、真值表、生成文书提交到仓库或任何外部服务。`data/` 已在 `.gitignore` 中排除，提交前务必确认 `git status` 未出现任何案件材料。违反此条等同于泄密事故。
