#!/usr/bin/env python3
"""めんたるIG（店舗経営者向けコミュニティ）の Instagram ストーリーを毎日自動投稿する。

毎朝 POST_HOUR 時に、images/ の中でまだ出していない一番若い番号のセット（001, 002…）の 1.jpg（問いかけ）→ 2.jpg（答え）を出す。
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
# 「EAA〜」＝システムユーザーのトークン（graph.facebook.com）／「IGAA〜」＝Instagramログインのトークン（graph.instagram.com）
API = "https://graph.instagram.com/v22.0" if os.environ.get("IG_ACCESS_TOKEN", "").startswith("IGAA") else "https://graph.facebook.com/v22.0"
TOKEN_ISSUED = os.environ.get("IG_TOKEN_ISSUED", "")  # IGAAトークンは期限を問い合わせられないので、発行日から60日で数える
RAW = "https://raw.githubusercontent.com/tanaka-create/mentaru-ig-story/main/images/"
POST_HOUR = int(os.environ.get("POST_HOUR") or 6)
LATEST_HOUR = 15        # GitHubの起動が大幅に遅れても、この時刻を過ぎたら出さない
NOTIFY_DAYS = (7, 3, 1)
RENEW_HOWTO = ("【更新のしかた】Metaの開発者画面 → アプリ → Instagram → APIセットアップ → めんたるIGの「トークンを生成」（IGAA〜）\n"
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
        if token.startswith("IGAA"):
            left = (dt.date.fromisoformat(TOKEN_ISSUED) + dt.timedelta(days=60) - now().date()).days if TOKEN_ISSUED else None
        else:
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
    ap.add_argument("--date", help="今日の日付の上書き（テスト用）")
    a = ap.parse_args()
    if a.check:
        token, uid = connect()
        lim = api("GET", f"{uid}/content_publishing_limit", token, fields="quota_usage,config")
        print("[CHECK] 投稿権限OK:", lim.get("data", [{}])[0])
        check_expiry(token, force=os.environ.get("NOTIFY_TEST") == "1")
        return 0
    if not os.environ.get("IG_ACCESS_TOKEN") and not a.dry_run:
        print("[SKIP] Instagramの鍵がまだ登録されていない（設定が終わるまで何もしない）")
        return 0
    t = now()
    today = a.date or t.date().isoformat()
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    sets = sorted(p.name for p in (BASE / "images").iterdir() if p.is_dir())
    # 途中で止まったセットがあれば、その続き。なければ次の未投稿セット
    entry = next((h for h in history if h.get("posted", 0) < h.get("total", 2)), None)
    if not entry:
        if not a.force and any(h["date"] == today for h in history):
            print("[SKIP] 今日はもう投稿済み")
            return 0
        done_sets = {h["set"] for h in history}
        nxt = next((x for x in sets if x not in done_sets), None)
        left = len([x for x in sets if x not in done_sets])
        if not nxt:
            print("[SKIP] 未投稿のセットがない（ストック切れ）")
            if not a.dry_run and t.hour >= POST_HOUR:
                line("⚠️ めんたるIGのストーリーのストックが切れました（自動補充が動いていない可能性）")
            return 0
        entry = {"date": today, "set": nxt, "posted": 0, "total": len(list((BASE / "images" / nxt).glob("*.jpg")))}
        print(f"[INFO] 次のセット: {nxt}（未投稿 {left}セット）")
    day = entry["set"]
    files = sorted(p.name for p in (BASE / "images" / day).glob("*.jpg"))
    done = entry["posted"]
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
    if entry not in history:
        entry["time"] = now().strftime("%H:%M")
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
