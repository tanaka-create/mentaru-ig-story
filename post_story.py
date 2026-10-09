#!/usr/bin/env python3
"""めんたるIG（店舗経営者向けコミュニティ）の Instagram ストーリーを毎日自動投稿する。

毎朝 POST_HOUR 時に、images/その日の日付/ の 1.jpg（問いかけ）→ 2.jpg（答え）を順にストーリーへ出す。
- 1日1回だけ。投稿済みかは history.json で判定。途中で止まった日は予備の起動で続きから出す
- その日の画像がないとき（ストック切れ）はLINEで知らせる
- 鍵（トークン）の期限が残り7日・3日・1日の朝と、失敗したときはLINE（saki's AI →「わたしのしごと」）に知らせる

環境変数（GitHub Secrets）:
    IG_ACCESS_TOKEN   instagram_content_publish 権限つきのトークン（システムユーザー）
    IG_USER_ID        めんたるIGの Instagram ユーザーID
    IG_USERNAME       めんたるIGのユーザー名（接続先の確認用）
    LINE_CHANNEL_ACCESS_TOKEN / LINE_GROUP_ID   お知らせ用
"""
import argparse
import datetime as dt
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
HISTORY = BASE / "history.json"
JST = dt.timezone(dt.timedelta(hours=9))
API = "https://graph.facebook.com/v22.0"
RAW = "https://raw.githubusercontent.com/tanaka-create/mentaru-ig-story/main/images/"
POST_HOUR = int(os.environ.get("POST_HOUR") or 7)
LATEST_HOUR = 15        # GitHubの起動が大幅に遅れても、この時刻を過ぎたら出さない
NOTIFY_DAYS = (7, 3, 1)
RENEW_HOWTO = ("【更新のしかた】ビジネス設定 → システムユーザー → トークンを生成（60日・instagram_content_publish 入り）\n"
               "→ GitHub の mentaru-ig-story の Secrets「IG_ACCESS_TOKEN」に上書き\n"
               "わからなければClaudeに「めんたるのストーリーの鍵を更新したい」でOK")


def now():
    return dt.datetime.now(JST)


def api(method, path, token, **params):
    params["access_token"] = token
    data = urlencode(params).encode()
    url = f"{API}/{path}"
    req = Request(url if method == "POST" else f"{url}?{data.decode()}", data=data if method == "POST" else None, method=method)
    try:
        with urlopen(req, timeout=60) as r:
            return json.load(r)
    except HTTPError as e:
        raise RuntimeError(f"{path}: {json.load(e).get('error', {}).get('message', str(e))}") from None


def line(text):
    tok, to = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""), os.environ.get("LINE_GROUP_ID", "")
    if not tok or not to:
        print("[WARN] LINEの設定がないので通知しない")
        return
    body = json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode()
    try:
        urlopen(Request("https://api.line.me/v2/bot/message/push", data=body, method="POST",
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}), timeout=30).read()
    except HTTPError as e:
        print("[WARN] LINE通知に失敗", e.code)


def check_expiry(token, force=False):
    try:
        info = api("GET", "debug_token", token, input_token=token).get("data", {})
        exp = info.get("expires_at") or info.get("data_access_expires_at")
        left = (dt.datetime.fromtimestamp(exp, JST) - now()).days if exp else None
    except Exception as e:
        print("[WARN] 期限の確認に失敗", e)
        return
    print("[INFO] 鍵の残り日数:", left if left is not None else "無期限")
    if left is not None and (left in NOTIFY_DAYS or left <= 0 or force):
        line(f"📸 めんたるIGのストーリー自動投稿\nInstagramの鍵が{'今日で切れます' if left <= 0 else f'あと{left}日で切れます'}。\n{RENEW_HOWTO}")


def post_image(token, uid, url, tries=3):
    for i in range(1, tries + 1):
        try:
            cid = api("POST", f"{uid}/media", token, image_url=url, media_type="STORIES")["id"]
            for _ in range(30):
                st = api("GET", cid, token, fields="status_code").get("status_code")
                if st == "FINISHED":
                    break
                if st == "ERROR":
                    raise RuntimeError(f"画像の取り込みに失敗: {url}")
                time.sleep(3)
            return api("POST", f"{uid}/media_publish", token, creation_id=cid)["id"]
        except RuntimeError as e:
            if i == tries:
                raise
            print(f"[WARN] {i}回目失敗、30秒後にやり直す: {e}")
            time.sleep(30)


def connect():
    token, uid, name = os.environ.get("IG_ACCESS_TOKEN", ""), os.environ.get("IG_USER_ID", ""), os.environ.get("IG_USERNAME", "")
    if not token or not uid:
        raise SystemExit("[ERROR] IG_ACCESS_TOKEN / IG_USER_ID が未設定")
    try:
        me = api("GET", uid, token, fields="username")
    except Exception as e:
        line(f"⚠️ めんたるIGのストーリー自動投稿が止まりました（Instagramにつながらない）\n{e}\n\n{RENEW_HOWTO}")
        raise
    if name and me.get("username") != name:
        raise SystemExit(f"[ERROR] 接続先が違う: {me.get('username')}")
    print("[INFO] 接続先:", me.get("username"))
    return token, uid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="時刻と投稿済みの判定を無視（手動テスト用）")
    ap.add_argument("--date", help="出す日付（手動テスト用。既定は今日）")
    a = ap.parse_args()
    if a.check:
        token, uid = connect()
        lim = api("GET", f"{uid}/content_publishing_limit", token, fields="quota_usage,config")
        print("[CHECK] 投稿権限OK:", lim.get("data", [{}])[0])
        check_expiry(token, force=os.environ.get("NOTIFY_TEST") == "1")
        return 0
    t = now()
    day = a.date or t.date().isoformat()
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    entry = next((h for h in history if h["date"] == day), None)
    files = sorted(p.name for p in (BASE / "images" / day).glob("*.jpg")) if (BASE / "images" / day).exists() else []
    if not files:
        print("[SKIP] 今日の画像がない:", day)
        if not a.dry_run and t.hour >= POST_HOUR and not any(h.get("missing") == day for h in history):
            line(f"⚠️ めんたるIGのストーリー：{day} の画像がありません（ストック切れ）。Claudeに「めんたるのストーリーズ次の30日分つくって」でOK")
            history.append({"date": day, "missing": day, "posted": 0})
            HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1))
        return 0
    done = entry.get("posted", 0) if entry else 0
    if done >= len(files) and not a.force:
        print("[SKIP] 今日はもう投稿済み")
        return 0
    if not a.force and not done:
        if t.hour >= LATEST_HOUR:
            print(f"[SKIP] {LATEST_HOUR}時を過ぎたので今日は出さない")
            return 0
        wait = (t.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0) - t).total_seconds()
        if wait > 25 * 60:
            print("[SKIP] まだ早い")
            return 0
        if wait > 0 and not a.dry_run:
            print(f"[INFO] {POST_HOUR}:00まで{int(wait)}秒待つ")
            time.sleep(wait)
    rest = files[done:] if not a.force else files
    print("[INFO] 出す画像:", [RAW + f"{day}/{f}" for f in rest])
    if a.dry_run:
        return 0
    token, uid = connect()
    check_expiry(token)
    if not entry:
        entry = {"date": day, "time": now().strftime("%H:%M"), "posted": 0}
        history.append(entry)
    try:
        for f in rest:
            mid = post_image(token, uid, RAW + f"{day}/{f}")
            entry["posted"] = entry.get("posted", 0) + 1
            print(f"[OK] {day}/{f} → {mid}")
            time.sleep(5)
    except Exception as e:
        line(f"⚠️ めんたるIGのストーリー自動投稿で失敗しました（{entry['posted']}/{len(files)}枚まで）\n次の予備の起動でもう一度試します。\n{e}")
        raise
    finally:
        HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
