# めんたるIG ストーリー自動投稿

- 毎朝6:00（JST）に `images/番号/1.jpg`（問いかけ）→ `2.jpg`（未投稿の若い番号から1日1セット）（答え）をストーリーに投稿
- 原稿と画像の元：Vault `株式会社PROST/AI講座/コミュニティ運営/mental集客IG/ストーリーズ/`（制作ツールは `_制作ツール/`）
- 画像は公開リポジトリの raw URL から Instagram に取り込ませるため、このリポジトリは公開
- 記録 `history.json`。ストック切れ・失敗・鍵の期限前はLINE（saki's AI）に通知
- Secrets: `IG_ACCESS_TOKEN`・`IG_USER_ID`・`LINE_CHANNEL_ACCESS_TOKEN`・`LINE_GROUP_ID`／Variables: `IG_USERNAME`・`POST_HOUR`（既定7）
