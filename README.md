# 桃園托育地圖（taoyuan-childcare-map）

輸入住家地址，在一張地圖上看到全市合法立案托育資源：評鑑、收費、名額、聯絡方式。
純靜態、零金鑰、開放資料驅動。白牌設計——其他縣市 fork 後改 `site/config/` 即可複用。

## 快速開始

```bash
python3 scripts/pipeline.py all   # 抓最新名冊 → 清洗 → geocode（有快取）→ GeoJSON
python3 scripts/pipeline.py tycg && python3 scripts/pipeline.py enrich && python3 scripts/pipeline.py build
# 前端：site/ 為純靜態，任何 http server 可跑
```

## 部署

GitHub Pages（Actions workflow）。已知限制：以 OAuth token push 不會觸發 push 事件的 workflow run，
因此 push 後需補一手 `gh workflow run pages.yml`（或等每週一排程自動重佈）。

`data/geocode_cache.json` 已入版控——clone 後不需要打任何 geocoding API 就能 build。

## 資料來源

| 資料 | 來源 | 授權 |
|---|---|---|
| 托嬰中心名冊（194 家，含評鑑/月費/核定床位） | 桃園市開放資料 dataset 168379 | 政府資料開放授權條款 v1 |
| 準公共化名冊 | dataset 168385 | 同上 |
| 官方座標／收托時間／公設民營候補人數 | 桃園育兒資源網公開 API（babycare.tycg.gov.tw，`pipeline.py tycg` 抓取） | 公開網站資料，標示來源與抓取日期 |
| 行政區界 | g0v/twgeojson | CC0 |

原始 CSV 快照存 `data/raw/`（帶日期戳，可追溯、防來源下架）。
`負責人` 欄位不進處理後資料（個資最小化）。

## 地圖資料 API 決策（zero-key 原則）

| 需求 | Demo／開發 | 公開版 | 說明 |
|---|---|---|---|
| **機構座標** | pipeline 離線批次 geocode（Nominatim，1req/1.1s＋UA）→ 快取入版控 | 同左（一次性） | 未命中自動退階：完整地址→街道層，`geo_precision` 欄位標示 exact/street |
| **底圖 tiles** | OSM 標準 raster tile（輕量使用＋attribution，符合 OSMF tile policy） | **OpenFreeMap**（免金鑰免註冊 vector tiles，MapLibre 直接吃）或自建 PMTiles 丟 CDN | 公開版不可長期掛 OSM 公用 tile（policy 禁 production 重度使用）；OpenFreeMap 上線前需再驗可用性 |
| **使用者地址搜尋** | 行政區選單＋瀏覽器定位為主；Nominatim search 為輔（節流＋attribution） | 同左；量大再評估 TGOS（需申請金鑰，破壞 zero-key，列為升級選項） | 主流程不依賴線上 geocode——選行政區/定位就能用，API 掛了也不壞 |

## 資料模型備註

`vacancy` / `available_from` / `vacancy_source` / `vacancy_updated_at` 四欄位 MVP 一律空值——
即時空位需要制度配套（機構申報或系統介接），本專案不假裝有。schema 先長對，政策層接上資料不改結構。

## License

MIT（code）；資料依各來源授權標示。
