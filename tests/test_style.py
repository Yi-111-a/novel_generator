from novel_engine.narration.style import AntiAbstractValidator


def test_named_emotion_is_rejected():
    v = AntiAbstractValidator()
    res = v.check("她很伤心，愤怒地哭了。")
    assert not res.ok
    assert "伤心" in res.hits and "愤怒" in res.hits


def test_concrete_prose_passes():
    v = AntiAbstractValidator()
    prose = "她攥紧那半枚玉佩，指节发白，转身没有再看那扇门。"
    res = v.check(prose)
    assert res.ok, res.summary()
    assert res.density == 0.0


def test_empty_prose_fails():
    v = AntiAbstractValidator()
    assert not v.check("   ").ok
