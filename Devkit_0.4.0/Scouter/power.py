"""Power level calculator + rank table (README sections 7-8).

README section 7's formula (fashion*35 + confidence*45 + energy*30 + coolness*50, then
x0.90-1.15) tops out at 100*160*1.15 = 18,400, so section 8's BOSS (30,000+) and
測定不能 (100,000+) ranks could never appear -- and being linear, live visitors (whose
stats land in a narrow 40-70 band) all came out at ~7-12k on-device.

With `boost=True` (default) the same weighted stats are averaged (README weights) and
mapped EXPONENTIALLY: power = 1000 * e^(K * (avg - 30)), K = ln(120)/65, i.e. +7.7% per
point, x2.1 per 10 points. Rank boundaries then sit at weighted avg ~45 (強者), ~61
(エリート), ~76 (ボス) and ~94 (測定不能), and a pose bonus can lift a visitor a full
rank. Set `boost=False` (power_boost=false) for the README's exact linear formula.

Pose/item bonus (`bonus=True`): a striking pose multiplies the power level (x1.3-x1.8,
POSE_BONUS below; matched by keyword in the VLM's free-text "pose"), and carrying
something visible adds x1.1. The HUD shows the bonus, so visitors learn that posing
raises their number -- the interactive hook for the exhibit. The VLM's 4 stats alone
cluster (it answers deterministically, in round 10s), so this is also the main source of
spread between people.

The random factor is seeded by track ID, so a person's number never flickers between
redraws and is reproducible from the log.
"""

from __future__ import annotations

import math
import random

STAT_KEYS = ("fashion", "confidence", "energy", "coolness")
WEIGHTS = {"fashion": 35, "confidence": 45, "energy": 30, "coolness": 50}

# (lower bound, Japanese class, English HUD class)
RANKS = [
    (100_000, "測定不能", "UNMEASURABLE"),
    (30_000, "ボス級", "BOSS"),
    (10_000, "エリート級", "ELITE"),
    (3_000, "強者級", "STRONG"),
    (0, "一般人級", "CIVILIAN"),
]
UNMEASURABLE = RANKS[0][0]

# (keywords matched in the VLM "pose" text, multiplier, HUD label). First match wins,
# so stronger poses come first.
POSE_BONUS = [
    (("ジャンプ", "跳"), 1.8, "JUMP"),
    (("構え", "ファイティング", "戦闘態勢", "かめはめ"), 1.6, "FIGHTING STANCE"),
    (("ガッツ", "力こぶ", "拳を"), 1.5, "GUTS POSE"),
    (("バンザイ", "万歳", "両手を上", "両手を挙", "手を上げ", "手を挙げ"), 1.4, "HANDS UP"),
    (("ピース", "Vサイン"), 1.3, "PEACE"),
    (("親指", "サムズアップ", "グッド", "いいね"), 1.3, "THUMBS UP"),
    (("腕組", "腕を組"), 1.3, "ARMS CROSSED"),
    (("指差", "指さ", "指を差"), 1.3, "POINTING"),
    (("決めポーズ", "ポーズを決め", "ポーズをと"), 1.3, "POSE"),
]
ITEM_BONUS = 1.1
NO_ITEM = ("なし", "無し", "ない", "特になし", "")


def bonus_of(result: dict) -> tuple[float, list[str]]:
    """-> (multiplier, HUD labels) from the VLM's pose/item text."""
    mult, labels = 1.0, []
    pose = str(result.get("pose", ""))
    for keywords, m, label in POSE_BONUS:
        if any(k in pose for k in keywords):
            mult *= m
            labels.append(f"{label} x{m:.1f}")
            break
    if str(result.get("item", "")).strip() not in NO_ITEM:
        mult *= ITEM_BONUS
        labels.append(f"ITEM x{ITEM_BONUS:.1f}")
    return mult, labels


EXP_BASE = 1000.0
EXP_K = math.log(120.0) / 65.0


def weighted_avg(result: dict) -> float:
    stats = {k: max(0, min(100, int(result.get(k, 0)))) for k in STAT_KEYS}
    return sum(stats[k] * WEIGHTS[k] for k in STAT_KEYS) / sum(WEIGHTS.values())


def calc_power(result: dict, track_id: int, boost: bool = True, bonus: bool = True) -> int:
    if boost:
        power = EXP_BASE * math.exp(EXP_K * (weighted_avg(result) - 30.0))
    else:
        power = weighted_avg(result) * sum(WEIGHTS.values())
    power *= random.Random(track_id * 7919).uniform(0.90, 1.15)
    if bonus:
        power *= bonus_of(result)[0]
    return int(power)


def rank_of(power: int) -> tuple[str, str]:
    for lower, ja, en in RANKS:
        if power >= lower:
            return ja, en
    return RANKS[-1][1], RANKS[-1][2]
