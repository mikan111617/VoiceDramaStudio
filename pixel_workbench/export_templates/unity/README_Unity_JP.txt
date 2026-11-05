Pixel Battle アニメーションの Unity 取り込み手順

■ 対象
Unity 2020.3 以降（2D）。

■ 手順
1. ZIP を展開し、中の「PixelBattle」フォルダを Unity プロジェクトの Assets フォルダへコピーする。
   2本目以降の書き出しも同じ場所へコピーする（PixelBattle/Scripts は上書きでよい）。
2. Unity に戻り、読み込みが終わるまで待つ。
   PixelBattle/Sequences/<名前>/frames の PNG は、自動で次の設定になる。
   スプライト、Point（ぼかしなし）、圧縮なし、ミップマップなし、Pixels Per Unit 16。
3. Project ウィンドウで PixelBattle/Sequences/<名前> フォルダ（または中の pixelbattle.json）を選ぶ。
4. メニュー「Assets」→「Pixel Battle」→「Build Animation」を実行する。
   同じフォルダに次の3つができる。
   <名前>.anim … アニメーションクリップ（効果音のタイミングはアニメーションイベント）
   <名前>.controller … クリップを再生するアニメーターコントローラー
   <名前>.prefab … SpriteRenderer・Animator・AudioSource・PixelBattleCuePlayer 付きのプレハブ
5. <名前>.prefab をシーンに置いて再生する。

■ 補足
- 1コマの画像は画面全体（シーケンスの幅×高さ）。画面中央がスプライトの中心になる。
- 大きさを変える場合は pixelbattle.json の pixels_per_unit を変えてから手順4をやり直す。
  frames の PNG は右クリック →「Reimport」で再設定する。
- ループさせる場合は pixelbattle.json の loop を true にして手順4をやり直す。
- 効果音は最後まで再生される（タイムライン上の長さでは切れない）。
  Unity が対応していない形式（m4a など）の効果音は読み込まれず、Console に警告が出る。
- spritesheet.png は全コマを並べた1枚画像。並び順と大きさは pixelbattle.json の spritesheet を参照。
  コマが多く 8192 ピクセルを超える場合は作られない。
- 手順4をやり直すと .anim と .controller は作り直され、.prefab は上書きされる。
