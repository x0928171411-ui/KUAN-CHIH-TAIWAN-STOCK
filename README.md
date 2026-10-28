# 台股盤後選股

每個交易日收盤後，依基本面、籌碼與技術條件篩選上市、上櫃普通股，挑出評分最高的五檔，用 Telegram 推播。沒有符合的股票時，訊息會寫「本日無推薦」。

這是一個獨立專案。請把這個資料夾當成 GitHub repository 的根目錄，不要和別的專案放在同一個 repo，否則 `.github/workflows/stock_scanner.yml` 不會被 GitHub 執行。

## 篩選條件

全部要同時成立：

| 條件 | 預設 | 怎麼判斷 |
| --- | --- | --- |
| 營收 | 近 3 個月年增率都大於 20% | FinMind 月營收，和去年同月相比 |
| 毛利率 | 連續 2 季改善 | 最近三季毛利率一次比一次高，而且是季報（間隔 3 個月） |
| 現金流 | 營業現金流為正 | 現金流量表是年初至今的累計數，程式會換成最新單季 |
| 籌碼 | 外資近 10 日買超 | 外陸資買賣超，不含外資自營商 |
| 技術 | 剛帶量站上月線 | 近 3 個交易日內收盤由下往上穿過 20 日均線，突破當日量能至少 1.5 倍，而且最新收盤仍在均線上 |

最新一個月的官方營收年增率若已經不到 20%，就不會再去查財報，用來節省 FinMind 額度。近三個月是否都達標，仍以 FinMind 為準。

通過的股票會打 0 到 100 分，取前 5 名。分數看營收年增的幅度、毛利率抬升、外資買超相對成交量、突破量能，以及股價離月線是否還近。型態分成「短線爆發潛力」、「適合波段」，或兩者都標。

權重在 `scanner/rules.py` 開頭。門檻可以用環境變數改，不必改程式：

| 環境變數 | 預設 | 意義 |
| --- | --- | --- |
| `REVENUE_YOY_MIN` | `0.20` | 營收年增門檻 |
| `REVENUE_MONTHS` | `3` | 要連續幾個月 |
| `MARGIN_IMPROVEMENTS` | `2` | 毛利率連續改善幾季 |
| `FOREIGN_LOOKBACK` | `10` | 外資回看交易日 |
| `MA_WINDOW` | `20` | 月線天數 |
| `CROSS_LOOKBACK` | `3` | 突破要發生在最近幾個交易日內 |
| `VOLUME_RATIO_MIN` | `1.5` | 突破當日量能倍數 |
| `TOP_N` | `5` | 推播幾檔 |
| `MAX_FUNDAMENTAL_CHECKS` | `60` | 最多查幾檔財報 |

## 資料來源

- 日成交：證交所「每日收盤行情」、櫃買「上櫃股票行情」。一次抓全市場，用來算 20 日均線。
- 三大法人：證交所 T86、櫃買三大法人日報。
- 最新月營收：證交所、櫃買開放資料，只做進場前的快速排除。
- 歷史月營收、綜合損益、現金流量：FinMind。毛利率、單季現金流和「連續三個月」都要靠它。

yfinance 沒有三大法人、月營收和季毛利率，所以沒有拿來當主資料源。

只看四碼普通股。`0050` 這類 ETF，以及代號帶英文字母的特別股、權證，都不會進清單。

## 專案結構

```text
main.py                          主程式
scanner/config.py                可調參數
scanner/market_data.py           證交所、櫃買
scanner/fundamentals.py          FinMind 財報與營收
scanner/rules.py                 條件、評分、型態
scanner/report.py                推播文字
scanner/notify.py                Telegram
.github/workflows/stock_scanner.yml
requirements.txt
.env.example
```

## 本機先試一次

需要 Python 3.12。

```powershell
cd tw-stock-scanner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

把 `.env` 裡的 `DRY_RUN` 留著 `1`，先不要填 Telegram，程式只會把結果印在終端機：

```powershell
python main.py
```

規則測試不需要網路：

```powershell
python -m unittest tests.test_rules
```

確認文字沒問題後，再填下面三個金鑰，把 `DRY_RUN` 改成 `0` 或刪掉那一行，重新執行。這次會真的傳 Telegram。

`.env` 已經在 `.gitignore`，不要提交。

## 1. 建立 Telegram 機器人

1. 在 Telegram 搜尋 `@BotFather`，打開官方機器人。
2. 傳送 `/newbot`。
3. 依提示取一個顯示名稱，以及一個以 `bot` 結尾的使用者名稱。
4. BotFather 會回一串 token，形如 `123456789:AA...`。這就是 `TELEGRAM_BOT_TOKEN`。
5. 在 Telegram 打開你剛建立的機器人，按開始，並傳一則任意訊息。沒有這一步，後面會查不到 chat id。
6. 用瀏覽器打開（把 token 換成你的）：

   `https://api.telegram.org/bot<你的TOKEN>/getUpdates`

7. 在 JSON 裡找 `"chat":{"id":123456789}`。那個數字就是 `TELEGRAM_CHAT_ID`。
8. 若回傳的 `result` 是空的，回到機器人也再傳一次訊息，然後重新整理這個網址。
9. 若要傳到群組，先把機器人加進群組、在群裡發一句話，再看 `getUpdates`。群組的 id 通常是負數，請連負號一起貼上。

## 2. 取得 FinMind Token

1. 到 [FinMind](https://finmindtrade.com/) 註冊。
2. 登入後在會員頁面複製 API Token。這就是 `FINMIND_TOKEN`。
3. 免費方案大約每小時 600 次。這個程式先用全市場行情與法人做過篩，只對剩下的股票查財報，一般用得到免費額度。

沒有這個 token 也能跑，但匿名額度更低，候選股票一多就可能在半途被拒絕。

## 3. 放到 GitHub，並設定 Secrets

1. 在 GitHub 右上角 **New repository**。
2. 名稱可自訂，例如 `tw-stock-scanner`。公開或私人都可以。不要勾選加入 README，避免和本機第一次推送衝突。
3. 只上傳這個資料夾的內容，讓 `main.py` 和 `.github` 位於 repository 根目錄。在這個資料夾裡：

```powershell
git init
git add .
git commit -m "Add after-hours Taiwan stock scanner"
git branch -M main
git remote add origin https://github.com/<你的帳號>/tw-stock-scanner.git
git push -u origin main
```

4. 打開該 repository 的 **Settings**。
5. 左側點 **Secrets and variables**，再點 **Actions**。
6. 點 **New repository secret**，逐一新增下面三個。Name 要完全一致，區分大小寫：

| Name | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather 給的 token |
| `TELEGRAM_CHAT_ID` | `getUpdates` 裡的 chat id |
| `FINMIND_TOKEN` | FinMind 的 API Token |

7. Secret 存檔後不能再查看內容，只能更新。貼上時確認前後沒有空白。
8. 打開 **Actions** 分頁。若 GitHub 要求先啟用 Actions，按允許。
9. 左側選 **台股盤後選股**，右側 **Run workflow**，分支選 `main`。這是手動測試，會分析最近一個有收盤資料的交易日並推播。
10. 點進該次執行，確認 `執行選股` 是綠色。Telegram 應收到清單，或「本日無推薦」。

排程只會在預設分支（通常是 `main`）上運作。workflow 檔案一定要先推上 `main`。

## 4. 每天自動執行

`.github/workflows/stock_scanner.yml` 的排程是：

```yaml
cron: "0 10 * * 1-5"
```

GitHub Actions 的時間是 UTC。`10:00 UTC` 等於台灣時間下午 6 點，而且只排週一到週五。台灣沒有日光節約時間，不用逐季調整。

國定假日即使排程有跑，程式看到沒有當天收盤資料就會結束，不會推播「本日無推薦」，避免把假日誤認成沒有標的。

GitHub 的排程偶爾會晚幾分鐘，也可能在 repository 太久沒活動時暫停。若某天 6 點過後沒收到訊息，到 Actions 看當次紀錄，或用 **Run workflow** 手動補跑。

想改推播時間，改的是 UTC。例如改成台灣時間下午 5 點半，就是 `30 9 * * 1-5`。

## 調整條件

在 workflow 的 `env:` 加上變數即可，例如把營收門檻改成 30%、只推播 3 檔：

```yaml
env:
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
  FINMIND_TOKEN: ${{ secrets.FINMIND_TOKEN }}
  REVENUE_YOY_MIN: "0.30"
  TOP_N: "3"
```

評分權重若要改，編輯 `scanner/rules.py` 的 `WEIGHT_*`。

## 說明

篩選結果是公開資訊的條件整理，不是買賣建議。股價沒有還原除權息；若突破日剛好跨過除權息，均線會失真。證交所偶爾會擋住雲端 IP，若 Actions 失敗，直接重跑一次即可。
