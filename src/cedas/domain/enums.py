"""枚举定义。字段取值一律用这里的枚举，禁止在业务代码中出现裸字符串比较。"""

from enum import Enum


class PrincipalType(str, Enum):
    """主刑种类。"""

    CONTROL = "管制"
    DETENTION = "拘役"
    FIXED_TERM = "有期徒刑"
    LIFE = "无期徒刑"
    DEATH = "死刑"
    UNKNOWN = "未知"

    @property
    def is_determinate(self) -> bool:
        """是否有确定期限（可计算起止日）。"""
        return self in (PrincipalType.CONTROL, PrincipalType.DETENTION, PrincipalType.FIXED_TERM)


class AdditionalType(str, Enum):
    """附加刑种类。"""

    FINE = "罚金"
    CONFISCATION = "没收财产"
    POLITICAL_RIGHTS = "剥夺政治权利"
    DEPORTATION = "驱逐出境"


class CustodyType(str, Enum):
    """强制措施种类。"""

    DETENTION = "刑事拘留"
    ARREST = "逮捕"
    BAIL = "取保候审"
    RESIDENTIAL = "监视居住"
    DESIGNATED_RESIDENTIAL = "指定居所监视居住"

    @property
    def deprives_liberty(self) -> bool:
        """是否属于羁押性措施（可直接折抵刑期）。"""
        return self in (CustodyType.DETENTION, CustodyType.ARREST)


class ParagraphKind(str, Enum):
    """清洗后段落的类型，用于锚点定位时缩小搜索范围。"""

    BODY = "正文"
    HEADING = "标题"
    SECTION = "分项"
    CASE_NO = "案号"
    SIGNATURE = "落款"
    HEADER = "页眉"
    FOOTER = "页脚"


class FieldStatus(str, Enum):
    """字段抽取状态。"""

    EXTRACTED = "已抽取"
    MISSING = "缺失"
    UNPARSED = "无法解析"
    CONFIRMED = "已确认"
    CORRECTED = "已修正"


class Consistency(str, Enum):
    """刑期一致性校验结果。"""

    PASS = "一致"
    MISMATCH = "不一致"
    NO_TEXT_END = "文本未载明"
    NOT_APPLICABLE = "不适用"


class Gender(str, Enum):
    MALE = "男"
    FEMALE = "女"


class CombinationKind(str, Enum):
    """数罪并罚的形态。

    区分这四种不是为了在文书上写出来，而是因为**起算日来源不同**：
    第 69 条按本罪羁押日起算，第 70/71/77 条的起算日必须人工指定
    （见 ``extract/combination.py`` 与 ADR-016）。
    """

    NONE = "未并罚"
    MERGE = "数罪并罚"
    MERGE_THEN_DEDUCT = "先并后减"
    DEDUCT_THEN_MERGE = "先减后并"
    REVOKE_PROBATION = "撤销缓刑并罚"

    @property
    def needs_manual_start(self) -> bool:
        """起算日是否必须人工指定。"""
        return self in (
            CombinationKind.MERGE_THEN_DEDUCT,
            CombinationKind.DEDUCT_THEN_MERGE,
            CombinationKind.REVOKE_PROBATION,
        )
