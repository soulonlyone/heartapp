# 推广文案（草稿）

发帖时直接用，按渠道挑长度。核心卖点始终是这四条，不要丢：

1. **免费导出原始数据**（同类工具通常付费）
2. **本地优先**：不注册、不上云；服务端可自建
3. **只报数字，不推断情绪**
4. **游戏浮窗 + 多人同图对比**

---

## 电梯演讲

### 中文 · 一句话

> 打游戏时的心率，不用切出游戏就能看见，而且原始数据免费导出。

### 中文 · 50 字

> HeartApp 是给 Windows 玩家的心率小窗：连上蓝牙手表，游戏里浮一层实时心率和曲线，
> 本地存全部记录，需要时导出原始数据。不注册、不上云、不推断情绪。

### 中文 · 150 字

> 玩恐怖游戏或打团战时，想不想知道自己心率到底飙到多少？
>
> HeartApp 从任意支持标准 BLE 心率服务的手表读取数据（OPPO、华为、小米、三星、Garmin 都行），
> 在游戏上浮一个小窗显示实时心率和最近一分钟曲线，鼠标可穿透，不挡操作。
>
> 所有记录存在本机 `data\heartrate.db`，随时能导出原始 CSV——不是截图、不是付费墙后的报告。
> 不注册账号、不上传云端，也不给你贴「压力值」这种标签。
>
> 想和朋友一起看，还能自建一个服务器，最多 4 人在同一张图上对比。
> Windows 10/11，解压即用，MIT 开源。

### English · One-liner

> See your heart rate while gaming — in an overlay, without alt-tabbing, and your raw data exports for free.

### English · 50 words

> HeartApp is a heart-rate overlay for Windows gamers. Connect any BLE smartwatch, get a
> click-through overlay with live bpm and a rolling chart, and keep every session locally in
> SQLite with free CSV export. No account, no cloud, no emotion scoring.

### English · 150 words

> Ever wondered how high your heart rate actually goes during a horror game or a ranked match?
>
> HeartApp reads from any watch that broadcasts the standard BLE Heart Rate Service (OPPO,
> Huawei, Xiaomi, Samsung, Garmin…), and shows a click-through overlay with live bpm and the
> last minute of data — so you never leave the game.
>
> Everything is stored locally in `data\heartrate.db`, and the raw CSV is yours to export.
> No account, no upload, no "stress score" or emotion inference — it only reports what it
> measures.
>
> Want to compare with friends? Self-host the tiny relay (Linux one-liner or Windows exe) and
> up to 4 people share one chart.
>
> Windows 10/11, unzip and run. MIT licensed.

---

## 分渠道改写

### V2EX（分享创造）

标题：`[分享] 做了个打游戏时看心率的 Windows 小工具，开源`

正文开头用**中文 150 字**，然后补充一段技术细节（Qt + asyncio + BLE，PySide6 手绘曲线、
SQLite、自建 WebSocket 服务端），最后放仓库链接和截图。
V2EX 喜欢技术实现细节，别只发广告。

### Reddit（r/pcgaming、r/software、r/QuantifiedSelf）

标题：`HeartApp – a local-first heart-rate overlay for PC gaming (open source)`

正文用**英文 150 字**，加上：
- 支持任意标准 BLE 心率广播手表（不绑定品牌）
- 数据只在本机，导出免费
- 不注入游戏、不读内存（避免被误会成外挂）

⚠️ 各版对自荐有规则，先看版规；r/pcgaming 通常要求非推广日或 mod 许可。

### Hacker News（Show HN）

标题：`Show HN: Heart-rate overlay for PC gaming, local-first with free CSV export`

正文用**英文 150 字**，第一段就说清「为什么做」和「和 Pulsoid 之类有什么不同」。
HN 反感营销腔，直接讲技术选择和取舍即可，例如：
- 只用标准 BLE 服务，不碰厂商 SDK
- 曲线先在原始采样上找缺口再按像素压缩，避免长记录被画成假断线
- 服务端只是自建转发，不存历史

### B 站 / 小红书

视频比文字有效得多。建议脚本：

1. 开场 3 秒：游戏画面 + 右上角小窗心率数字跳动
2. 切到「回看」页，展示整段曲线和那个尖峰：「刚才那波团战，心率到 150」
3. 展示导出 CSV，强调「原始数据是你的」
4. 结尾：4 人同图对比截图

标题参考：
- 「打游戏时我的心率飙到 150，于是我做了个工具」
- 「不用切出游戏也能看心率」

### 少数派

适合写成「为什么我做了个不推断情绪的心率工具」这种产品思考文，
重点讲设计取舍（为什么不做情绪评分、为什么坚持本地优先、为什么服务端要能自建）。

---

## 发帖清单

- [ ] 仓库 topics 与描述已填（16 个 topics）
- [ ] 社交预览图已上传（Settings → Social preview）
- [ ] README 首屏有截图和一句话说明
- [ ] Release 里下载物命名清晰（便携版排第一）
- [ ] 帖子里的链接指向 `releases/latest`，不要指向具体版本号
- [ ] 发完 24 小时内回帖答疑（回复速度直接影响推荐权重）
