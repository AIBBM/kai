# kai README 主 GIF：Agent Factory → 自然語言交派任務 → 分身讀檔分析
# 錄製：demo/record.sh（會先重建 /tmp/kai-demo，結束後清理程序）

Output demo/out/hero.mp4
Output demo/out/hero.gif

Set Shell "bash"
Set FontFamily "WenQuanYi Zen Hei Mono"
Set FontSize 14
Set Width 1280
Set Height 720
Set Padding 8
Set Framerate 20
Set TypingSpeed 45ms

Hide
Type "uv run python app.py /tmp/kai-demo" Enter
Wait+Screen@60s /工作目錄/
Sleep 3s
Type "/greeting off" Enter
Sleep 1s
Show

Sleep 1500ms
Type "/create-agent 台股技術分析師，擅長 K 線、均線與籌碼分析" Sleep 400ms Enter
Wait+Screen@120s /Agent 已建立/
Sleep 2s

Type "打開台股技術分析師，請他讀 notes/watchlist.md，每檔用一句話條列技術面重點，不用上網查" Sleep 400ms Enter
Wait+Screen@120s /來自/
Wait+Screen@240s /send_to_agent/
# 停在分身的分析結果
Sleep 10s
