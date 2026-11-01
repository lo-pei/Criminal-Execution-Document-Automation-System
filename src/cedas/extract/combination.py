"""数罪并罚的形态识别：这份判决属于哪一种并罚。

刑法里有四种会出现在执行通知书里的并罚形态，**起算日各不相同**，
认错了就会把刑期起止写成另一个日期，而这种错误在文书上看不出来：

===========================  ==========  ==========================================
形态                          法条        刑期起算日
===========================  ==========  ==========================================
数罪并罚（判决宣告前一人犯数罪）  第 69 条     本罪羁押起始日（与普通案件相同）
先并后减（漏罪）              第 70 条     前罪刑期起算日，已执行部分计入新刑期
先减后并（又犯新罪）            第 71 条     本判决执行之日，前罪未执行部分自该日起算
撤销缓刑后并罚                第 77 条     本判决执行之日
===========================  ==========  ==========================================

因此本模块只做一件事：判定形态，把起算日的口径交给上层。**不推算日期**——
后三种形态的起算日一律要求人工填写（ADR-016）：判决书写了「已经执行的刑期
计算在本判决决定的刑期以内」，却没有任何一句话写出这个起算日，
程序按羁押日自动算必然错，而这个错只有在罪犯被多关或少关时才被发现。

判定顺序即优先级。「撤销缓刑」必须先于「前罪所判处」：「撤销缓刑后并罚」
的判决书里同时出现「前罪」字样，若先判「先并后减」就会把起算日引向前罪，
而正确做法是从本判决执行之日起算。
"""

from __future__ import annotations

import re
from typing import Optional

from ..domain.enums import CombinationKind

# 形态 → 起算日口径说明。上层直接拿去显示，避免各处重复写这段中文。
START_HINTS: dict[CombinationKind, str] = {
    CombinationKind.MERGE_THEN_DEDUCT: "应取前罪刑期起算日，已执行部分计入新刑期",
    CombinationKind.DEDUCT_THEN_MERGE: "应取本判决执行之日，前罪未执行部分自该日起算",
    CombinationKind.REVOKE_PROBATION: "应取本判决执行之日",
}

# 起算日必须人工指定的形态。第 69 条的普通数罪并罚不在此列。
MANUAL_START_KINDS: tuple[CombinationKind, ...] = (
    CombinationKind.MERGE_THEN_DEDUCT,
    CombinationKind.DEDUCT_THEN_MERGE,
    CombinationKind.REVOKE_PROBATION,
)

_REVOKE_CLUES = ("撤销缓刑", "宣告缓刑", "缓刑宣告")
# 撤销缓刑 / 撤销假释在真实文书里的写法差异极大，枚举短语必然漏：
#   「撤销本院（2022）京0105刑初111号刑事判决中对被告人…宣告缓刑…的执行部分」
#   「撤销山东省梁山县人民法院（2013）梁刑初字第65号刑事判决书对被告人张某祥
#     犯聚众斗殴罪，判处有期徒刑一年六个月缓刑二年的缓刑宣告」——注意是「缓刑宣告」，
#     不是「宣告缓刑」，按短语匹配一个都不中，被误判成第 69 条数罪并罚
#   「撤销山东省淄博市中级人民法院（2013）淄刑执字第841号刑事裁定书」——撤销假释，
#     裁定书本身即是假释裁定，正文里连「假释」二字都没有
# 所以认结构不认短语：「撤销」与「缓刑/假释」同现，即撤销缓刑/假释后并罚。
# 只有「撤销」而无「缓刑/假释」的（如撤销假释裁定书），留在第 71 条先减后并——
# 那种案子的主文写「与前罪未执行的刑罚并罚」，本来就是按 71 条处理。
_REVOKE_VERBS = ("撤销",)
_REVOKE_OBJECTS = ("缓刑", "假释")
# 但「撤销」与「缓刑」同现还不够——**撤销的宾语是什么**才决定它是不是第 77 条。
#
#   撤销本院……刑事判决中对被告人宣告缓刑三年的执行部分   ← 77 条，撤销缓刑本身
#   撤销……判处有期徒刑一年六个月缓刑二年的缓刑宣告        ← 77 条，撤销缓刑本身
#   撤销延吉市人民法院……刑事判决中第二项，即被告人申某某犯
#     故意伤害罪，判处有期徒刑六个月，缓刑六个月          ← 再审撤销原判项
#
# 前两种撤销的是**缓刑这一执行方式**，「缓刑」是宾语中心词，后面不接期限
# （「缓刑宣告」「缓刑…的执行部分」）；第三种撤销的是**判决中的某一项**，
# 「缓刑六个月」只是被撤销那一项的内容——缓刑后面紧跟期限，是量刑而不是宾语。
# 这一条不补，（2015）延中刑再终字第9号的申某某会被判成撤销缓刑并罚，
# 而正确答案是再审改判，与并罚无关（见 docs/12 第 2 节）。
_PROBATION_WITH_TERM = re.compile(r"缓刑[零〇○一二三四五六七八九十百\d]{1,4}[年月]")
# 明确以缓刑为宾语的写法。命中即认定撤销的是缓刑，不再看后面有没有期限——
# 「…缓刑二年的缓刑宣告」里既有「缓刑二年」又有「缓刑宣告」。
_PROBATION_AS_OBJECT = ("缓刑宣告", "宣告缓刑", "缓刑的执行", "缓刑执行")
_DEDUCT_CLUES = (
    # 认词根不认整句。真实文书的宾语五花八门：
    #   「与前罪未执行完毕的有期徒刑三年六个月并罚」
    #   「与前罪未执行的刑罚并罚」
    #   「连同前罪没有执行的有期徒刑二年八个月……决定执行有期徒刑二年十个月」
    #     （2014）甬余刑初字第633号——写的是「没有执行的有期徒刑」，
    #     差「刑罚」二字，按短语匹配就落到第 69 条去了
    "未执行",
    "没有执行的",
    "余刑",
    "剩余刑期",
)
_MERGE_THEN_DEDUCT_CLUES = (
    "已经执行的刑期",
    "已执行的刑期",
    "前罪所判处",
    "前后两个判决",
    "新发现的罪",
    "漏罪",
    "第七十条",
)
# 入场券：出现任何一条才谈得上「并罚」。单一罪名也可能先并后减或撤销缓刑，
# 所以不能只拿「罪名数 > 1」或「并罚」二字当判据——「已经执行的刑期，
# 计算在本判决决定的刑期以内」这句话里一个「并罚」都没有，却正是第 70 条。
_ALL_CLUES = frozenset(
    ("并罚", "决定执行", "总和刑期", *_REVOKE_CLUES, *_DEDUCT_CLUES, *_MERGE_THEN_DEDUCT_CLUES)
)


def revokes_probation(text: str) -> bool:
    """是否撤销缓刑后并罚，即刑法第 77 条。

    认结构不认短语：「撤销」与「缓刑/假释」同现即成立。详见 ``_REVOKE_VERBS``
    上方的说明——真实文书写「缓刑宣告」「缓刑…的执行部分」「撤销…假释裁定」，
    短语式匹配必然漏掉其中几种。

    同现之后再看**撤销的宾语**（``_PROBATION_WITH_TERM``）：缓刑后面接期限的，
    撤销的是载有该缓刑的判项，不是缓刑本身。再审判决书通篇是这种写法，
    不加这一层，每一起再审改判都会被判成并罚。

    这个函数同时被 :mod:`defendant` 用来判断「撤销缓刑/假释的单列一项」，
    两处必须用同一套判据——各自演化一次，就会出现「形态判定说不是撤销缓刑、
    罪名抽取说这一项是撤销缓刑」这种自相矛盾的报告。
    """
    if not any(verb in text for verb in _REVOKE_VERBS):
        return False
    if "假释" in text:
        # 假释后面不接期限（「假释考验期」是考验期，不是假释的期限），
        # 无需再做宾语判定。
        return True
    if "缓刑" not in text:
        return False
    if any(phrase in text for phrase in _PROBATION_AS_OBJECT):
        return True
    return _PROBATION_WITH_TERM.search(text) is None


def _classify(text: str) -> Optional[CombinationKind]:
    """在一段文本里找形态线索。找不到返回 None，由调用方决定再往哪看。"""
    if not text:
        return None
    if revokes_probation(text):
        return CombinationKind.REVOKE_PROBATION
    if any(clue in text for clue in _DEDUCT_CLUES):
        return CombinationKind.DEDUCT_THEN_MERGE
    if any(clue in text for clue in _MERGE_THEN_DEDUCT_CLUES):
        return CombinationKind.MERGE_THEN_DEDUCT
    return None


def detect_combination(
    text: str,
    crime_count: int,
    reasoning: str = "",
) -> CombinationKind:
    """判定并罚形态。认不出就当普通案件，不猜。

    Args:
        text: 该被告人**主文判项**的合并文本（「判决如下」之后的文字，不含说理）。
        crime_count: 已识别到的罪名数。
        reasoning: 说理段里**提到该被告人**的句子，仅作兜底。

    两段文本分开喂、且主文优先，是因为真实判决把线索放在哪一段并不固定：

    - （2014）甬余刑初字第633号把「没有执行的有期徒刑」写在主文；
    - （2021）苏0723刑初53号主文只写「与前罪刑罚……并罚」，
      「已经执行的刑期，应当计算在新判决决定的刑期以内」却写在「本院认为」里。

    先看说理会把两者搞反：（2014）梁刑初字第12号的说理段同时写「宿某建撤销假释」
    与「张某祥撤销缓刑」，段落归属只能给其中一人，另一人就会被套上不属于他的形态。
    """
    if (
        crime_count <= 1
        and not revokes_probation(text)
        and not any(clue in text for clue in _ALL_CLUES)
    ):
        return CombinationKind.NONE

    found = _classify(text)
    if found is not None:
        return found

    found = _classify(reasoning)
    if found is not None:
        return found

    return CombinationKind.MERGE


def start_hint(kind: CombinationKind) -> str:
    """取该形态的起算日口径说明；无需人工的形态返回空串。"""
    return START_HINTS.get(kind, "")


__all__ = [
    "MANUAL_START_KINDS",
    "START_HINTS",
    "detect_combination",
    "revokes_probation",
    "start_hint",
]
