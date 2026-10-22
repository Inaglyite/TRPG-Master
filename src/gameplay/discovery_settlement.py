"""One action outcome must describe the discoveries actually committed."""

from __future__ import annotations


def paid_discoveries(discoveries: list[dict]) -> list[dict]:
    return [
        row for row in discoveries
        if row.get("discovered") and (row.get("fallback") or {}).get("mode") == "grant_clue"
    ]


def discovery_outcome_description(discoveries: list[dict]) -> str:
    return "\n".join(
        str((row.get("fallback") or {}).get("narrative") or "")
        + " 已取得的发现：" + str(row.get("text") or row.get("clue_id") or "")
        + (" 已取出并收妥实物、加入随身物品：" + str(row["granted_item"])
           + "；物品不再留在原处，后续不再要求领取。" if row.get("granted_item") else "")
        for row in paid_discoveries(discoveries)
    )
