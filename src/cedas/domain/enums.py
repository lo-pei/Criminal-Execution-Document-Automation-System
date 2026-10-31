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
