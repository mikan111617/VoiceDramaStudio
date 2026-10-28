# Aseprite連携（VoiceDrama Studio）

Steam版のAsepriteとVoiceDrama Studioのドット絵制作ツールを、ローカルPC上で連携します。Aseprite本体の再ビルド・再配布は不要です。

## 起動

1. VoiceDrama Studioのフォルダ（app.pyがある場所）で git pull --ff-only を実行します。
2. start_windows.bat で起動し、画面上部の「🧩 Aseprite」を選びます。
3. 「Aseprite.exeのフルパス」にSteam版Asepriteの実行ファイルを指定します。
   例: C:\Program Files (x86)\Steam\steamapps\common\Aseprite\Aseprite.exe
4. 「接続テスト用サンプル生成」で、.asepriteファイルとプレビュー画像が作成できるか確認します。

URL: http://127.0.0.1:8765/aseprite/

公式CLIの --batch / --noinapp / --script オプションを使うため、AsepriteのGitHub版のビルドは不要です。<https://www.aseprite.org/docs/cli/>

## 利用できる機能

- レイヤー・フレーム付きのネイティブ .aseprite スプライト作成
- ピクセル・矩形・線・楕円による描画、既存スプライトへのレイヤー追加
- PNG/GIF/WebP画像の読み込み、PNG/GIF/スプライトシートへの書き出し
- Ollamaから図形描画JSONを生成してAsepriteへ渡す機能（任意）
- MCPクライアントからのスプライト作成・一覧・編集・出力

**重要:** Ollamaによる生成は「図形の描画指示をAIに考えさせる」もので、専用の画像生成モデルと同品質を保証するものではありません。PixelLabからの素材の受け渡しは、現状ではPNGファイルを介します。

## MCP

MCP対応クライアントに以下をstdioサーバーとして登録します。

- command: VoiceDrama Studioが使用しているPython実行ファイル（通常 .venv\Scripts\python.exe）
- args: D:\AI\VoiceDramaStudio\aseprite_mcp_server.py （インストール先に応じて変更）
- working directory: D:\AI\VoiceDramaStudio

ツール一覧: aseprite_status / aseprite_list / aseprite_create / aseprite_add_layers / aseprite_export

MCPは任意です。VoiceDrama Studio内の「🧩 Aseprite」画面を使うだけならMCPクライアントの設定は不要です。

## セキュリティ・保存先

- APIキーは不要。Aseprite.exeの絶対パスだけをローカルに保存します。
- 生成データ、設定、スプライトは workspace/aseprite_studio/ 以下に保存され、Gitにpushされません。
- MCPに任意のLuaコードやコマンドを渡すことはできません。許可した図形命令だけを受け付けます。
- Aseprite本体は同梱しません。
- ローカルサーバーは既存のVoiceDrama Studio同様、127.0.0.1で起動してください。

## テスト

python -m pytest -q tests/test_aseprite_bridge.py

CLIの実機テストにはSteam版Asepriteが必要です。自動テストではAsepriteの動作を模擬するので実機は必要ありません。
