#!/usr/bin/env python3
"""めんたるIGのストーリーの数字（見た人数・返信など）を自動で記録する。

ストーリーの数字は、出ている24時間のあいだしか取れない。
なので数時間ごとに起動して、いま出ているストーリーの最新の数字を insights/stories.json に上書き保存する。
（同じストーリーは何度取っても1行。最後に取れた数字＝ほぼ24時間分の結果になる）

保存するのはIGの数字だけ。LINE（L Harness）のお客さん情報は入れない。
リンクスタンプのタップ数はAPIで取れないので、LINE側の流入リンクの数で見る。

環境変数（GitHub Secrets）: IG_ACCESS_TOKEN / IG_USER_ID（post_story.py と同じ）
"""
import datetime as dt
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = Path(__file__).resolve().parent
OUT = BASE / "insights" / "stories.json"
JST = dt.timezone(dt.timedelta(hours=9))
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
UID = os.environ.get("IG_USER_ID", "")
API = "https://graph.instagram.com/v22.0" if TOKEN.startswith("IGAA") else "https://graph.facebook.com/v22.0"
# 1つずつ取る（どれか1つが使えなくても、ほかは取れるように）
METRICS = ["views", "reach", "replies", "shares", "total_interactions", "follows", "profile_visits", "navigation"]


def get(path, **params):
    params["access_token"] = TOKEN
    try:
        with urlopen(f"{API}/{path}?{urlencode(params)}", timeout=60) as r:
            return json.load(r)
    except HTTPError as e:
        raise RuntimeError(json.load(e).get("error", {}).get("message", str(e))) from None


def metric(media_id, name):
    params = {"metric": name}
    if name == "navigation":
        params["breakdown"] = "story_navigation_action_type"
    data = get(f"{media_id}/insights", **params).get("data", [])
    if not data:
        return None
    item = data[0]
    if name == "navigation":
        # 次へ・戻る・次のアカウントへ・閉じた の内訳
        out = {}
        for b in item.get("total_value", {}).get("breakdowns", []):
            for r in b.get("results", []):
                out[r["dimension_values"][0]] = r["value"]
        if out:
            return out
    if "total_value" in item:
        return item["total_value"].get("value")
    values = item.get("values") or [{}]
    return values[0].get("value")


def main():
    if not TOKEN or not UID:
        raise SystemExit("[ERROR] IG_ACCESS_TOKEN / IG_USER_ID が未設定")
    saved = json.loads(OUT.read_text()) if OUT.exists() else {}
    stories = get(f"{UID}/stories", fields="id,media_type,timestamp,permalink").get("data", [])
    print(f"いま出ているストーリー: {len(stories)}件")
    fetched_at = dt.datetime.now(JST).isoformat(timespec="minutes")
    for s in stories:
        row = saved.get(s["id"], {})
        posted = dt.datetime.fromisoformat(s["timestamp"].replace("+0000", "+00:00")).astimezone(JST)
        row.update({"posted_at": posted.isoformat(timespec="minutes"), "media_type": s.get("media_type"),
                    "permalink": s.get("permalink"), "fetched_at": fetched_at})
        errors = {}
        for m in METRICS:
            try:
                v = metric(s["id"], m)
                if v is not None:
                    row[m] = v
            except RuntimeError as e:
                errors[m] = str(e)[:120]
        if errors:
            row["errors"] = errors
        else:
            row.pop("errors", None)
        saved[s["id"]] = row
        print(s["id"], row["posted_at"], {k: row.get(k) for k in ("views", "reach", "replies", "navigation")}, errors or "")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(dict(sorted(saved.items(), key=lambda kv: kv[1]["posted_at"])), ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
