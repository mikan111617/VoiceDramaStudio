Pixel Battle アニメーションの GBA 向け書き出し

■ 中身
frames/frame_000.png …   1コマずつのインデックスカラーPNG（240×160）
spritesheet.png …         全コマを並べた1枚画像（並びは pixelbattle_gba.json の spritesheet）
palette.pal …             パレット（JASC-PAL形式。Aseprite・Usenti などで読める）
palette.gpl …             パレット（GIMP形式）
palette_bgr555.bin …      パレット（GBA本体と同じ15ビットBGR、リトルエンディアン）
sounds/ …                 効果音（元のファイルのまま）
pixelbattle_gba.json …    コマ数・FPS・色数・効果音のタイミングなど

■ 色のルール
- 全コマで1つのパレットを共有する。
- パレット0番は透明（色はマゼンタ 248,0,248）。
- 16色版は透明＋15色、256色版は透明＋255色。
- 色は GBA の15ビットカラー（各色8の倍数）にそろえてある。

■ 時間
- GBA は1秒に60回画面を描き替える。1コマを表示する回数は pixelbattle_gba.json の gba_vblanks_per_frame。
  12fpsなら5回、15fpsなら4回、20fpsなら3回。
- 効果音の開始位置は sounds の start_frame（コマ）と gba_vblank（描き替え回数）。

■ 容量
- 1コマのデータ量は bytes_per_frame、全体は estimated_total_bytes（画像データのみ）。
  16色版は1コマ19,200バイト、256色版は38,400バイト。

■ GBA の画面の制約
- 1枚の画像として画面全体（240×160）を表示するには、ビットマップ表示（256色）か、背景（タイル）として扱う必要がある。
- スプライト（OBJ）として使う場合、1つの大きさは最大64×64。大きい絵は分割して並べる。
- 取り込み先のツールが求める形式（色数・大きさ・並べ方）は、そのツールの説明を確認する。
