from novel_engine.narration.quality_checks import (
    detect_repeated_opening,
    detect_repeated_paragraphs,
)


def test_repeated_opening_flags_near_verbatim_recap():
    shared = "六月的临江市像个蒸笼，陈野坐在写字楼的奶茶店里，面前一杯喝到底的杨枝甘露，人事部的消息措辞礼貌。"
    prev = shared + "他端着纸箱离开了办公楼，那盆养了三年的绿萝还活着。"
    cur = shared + "但此刻他已经在老城区的店铺里，对着一台不该亮的电脑。"
    assert len(detect_repeated_opening(cur, prev)) >= 40


def test_distinct_openings_not_flagged():
    prev = "雨下了一整夜，沈知夏站在勘查车旁，盯着那扇锈蚀的院门，帽檐压得很低。" * 2
    cur = "陈野把出租车停在巷口，深吸一口气，伸手推开了店铺半拉的卷帘门。" * 2
    assert detect_repeated_opening(cur, prev) == ""


def test_short_repeated_dialogue_is_not_a_duplicate_paragraph():
    prose = "他问是否确认。\n\n“是。”\n\n她隔着门又问了一遍。\n\n“是。”"
    assert detect_repeated_paragraphs(prose) == []


def test_substantive_repeated_paragraph_is_still_rejected():
    paragraph = "门外的脚步声停了，灯光从门缝里切进来，照亮地上的半张旧照片。"
    assert detect_repeated_paragraphs(f"{paragraph}\n\n过渡。\n\n{paragraph}")
