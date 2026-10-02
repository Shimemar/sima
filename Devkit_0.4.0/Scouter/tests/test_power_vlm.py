#!/usr/bin/env python3
"""戦闘力計算・VLM回答JSON解析の単体テスト(pyneat不要): python3 tests/test_power_vlm.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import power  # noqa: E402
from vlm_worker import parse_vlm_json  # noqa: E402

GOOD = {"fashion": 82, "confidence": 74, "energy": 63, "coolness": 91, "pose": "腕組み",
        "item": "バックパック", "title": "歴戦のプロジェクトリーダー", "comment": "静かにこちらを見ている。"}


def stats(v):
    return {k: v for k in power.STAT_KEYS}


def test_readme_formula_without_boost():
    p = power.calc_power(GOOD, 1, boost=False, bonus=False)
    base = 82 * 35 + 74 * 45 + 63 * 30 + 91 * 50
    assert base * 0.9 <= p <= base * 1.15, p
    assert power.calc_power(stats(100), 1, boost=False, bonus=False) <= 18_400


def test_power_stable_per_track_id():
    assert power.calc_power(GOOD, 7) == power.calc_power(GOOD, 7)


def test_boost_reaches_all_ranks():
    ranks = {power.rank_of(power.calc_power(stats(v), 3, bonus=False))[0] for v in (10, 50, 70, 85, 100)}
    assert ranks == {"一般人級", "強者級", "エリート級", "ボス級", "測定不能"}, ranks


def test_boost_is_exponential():
    # +10 weighted-avg points ~ x2.1, independent of track-ID jitter
    a, b = power.calc_power(stats(50), 5, bonus=False), power.calc_power(stats(60), 5, bonus=False)
    assert 2.0 < b / a < 2.2, b / a


def test_live_visitors_spread_across_ranks():
    # blended stats actually measured on-device (2026-10-01): weighted avg 45-67
    live = [(51, 76, 49, 55), (40, 72, 74, 50), (34, 61, 28, 50), (41, 72, 84, 70)]
    pw = [power.calc_power(dict(zip(power.STAT_KEYS, s)), i, bonus=False) for i, s in enumerate(live, 1)]
    assert max(pw) / min(pw) > 4, pw


def test_pose_and_item_bonus():
    assert power.bonus_of({"pose": "両手を上げてバンザイ", "item": "なし"}) == (1.4, ["HANDS UP x1.4"])
    m, labels = power.bonus_of({"pose": "腕組み", "item": "バックパック"})
    assert abs(m - 1.43) < 1e-9 and labels == ["ARMS CROSSED x1.3", "ITEM x1.1"]
    assert power.bonus_of({"pose": "座っている", "item": "なし"}) == (1.0, [])
    assert power.bonus_of({"pose": "親指を立てて", "item": "なし"})[1] == ["THUMBS UP x1.3"]
    plain = {**stats(60), "pose": "立っている", "item": "なし"}
    posed = {**stats(60), "pose": "ジャンプしている", "item": "なし"}
    assert power.calc_power(posed, 4) == int(power.calc_power(plain, 4) * 1.8) or \
        abs(power.calc_power(posed, 4) - power.calc_power(plain, 4) * 1.8) <= 2


def test_parse_fenced_json_with_chatter():
    text = "はい、分析結果です。\n```json\n" + str(GOOD).replace("'", '"') + "\n```\n以上です。"
    r = parse_vlm_json(text)
    assert r["fashion"] == 82 and r["title"] == "歴戦のプロジェクトリーダー"


def test_parse_short_keys_v4():
    r = parse_vlm_json('{"f":63,"c":71,"e":42,"k":58,"p":"ピース","i":"なし","t":"平和の使者","m":"笑顔でピース"}')
    assert (r["fashion"], r["coolness"], r["pose"], r["title"]) == (63, 58, "ピース", "平和の使者")


def test_parse_clamps_and_defaults():
    r = parse_vlm_json('{"fashion": 150, "confidence": "70", "energy": -5, "coolness": 50.6}')
    assert (r["fashion"], r["confidence"], r["energy"], r["coolness"]) == (100, 70, 0, 50)
    assert r["item"] == "なし" and r["title"] == ""


def test_parse_rejects_garbage():
    for bad in ("申し訳ありませんが分析できません。", '{"pose": "立つ"}', '{"fashion": 80, '):
        try:
            parse_vlm_json(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted: {bad!r}")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if fails else 0)
