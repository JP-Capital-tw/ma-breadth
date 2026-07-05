# 均線廣度儀表板

台股上市櫃普通股均線廣度掃描（還原價），每個交易日 21:30 (台北) 由 GitHub Actions
自動抓取 FinLab 資料、重建靜態頁面並部署到 GitHub Pages。

- 網址: https://lzzz666.github.io/ma-breadth/
- 條件篩選: 站上/跌破/新站上/新跌破 MA5~240、多排/空排、上揚/下彎 (AND)
- 點個股開 K 線圖: 預設 60 交易日, 滑鼠滾輪縮放 20~240 日

## 本機重建

```bash
uv run --with finlab python build_dashboard.py   # 產出 dist/index.html
```

需要 `FINLAB_API_TOKEN`（或本機已有 finlab 登入 session）。
`FINLAB_DB_DIR` 可指向既有 feather 快取目錄加速。

## Secrets

- `FINLAB_API_TOKEN`: FinLab API token（Settings → Secrets and variables → Actions）
