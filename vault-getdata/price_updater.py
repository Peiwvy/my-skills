#!/usr/bin/env python3
from __future__ import annotations
"""
价格更新工具 - 获取标的当前价格并写入 Obsidian frontmatter
数据源：Yahoo Finance API（股票/指数/外汇）+ 新浪财经（现货贵金属/原油）
无需任何第三方依赖，纯标准库实现

用法:
  python3 price_updater.py                          # 更新「持有」或「重点关注」的标的
  python3 price_updater.py --all                    # 更新 33-Micro 下所有标的
  python3 price_updater.py /path/to/specific.md     # 更新指定文件（忽略状态过滤）
  python3 price_updater.py --dry-run                # 只显示价格，不写入文件
"""

import urllib.request
import urllib.parse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MICRO_DIR = VAULT_ROOT / "33-Micro"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ── TradingView exchange → yfinance 后缀 ─────────────────────────

EXCHANGE_SUFFIX = {
    "SSE":    ".SS",    # A股上证
    "SZSE":   ".SZ",    # A股深证
    "NASDAQ": "",       # 美股
    "NYSE":   "",       # 美股
    "AMEX":   "",       # 美股
    "HKEX":   ".HK",   # 港股
    "TSE":    ".T",     # 日股
    "LSIN":   ".L",     # 伦敦
    "XETR":   ".DE",    # 德国 XETRA
}

# 特殊品种 → Yahoo Finance ticker
YAHOO_TICKER_MAP = {
    # 指数
    "VIX": "^VIX", "SPX": "^GSPC", "SPX500": "^GSPC",
    "DXY": "DX-Y.NYB", "NI225": "^N225", "HSI": "^HSI", "HSTECH": "^HSTECH",
    # 加密
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
    # 外汇
    "USDJPY": "USDJPY=X", "EURUSD": "EURUSD=X", "AUDUSD": "AUDUSD=X",
    "GBPUSD": "GBPUSD=X", "USDCHN": "CNH=X", "USDCNH": "CNH=X", "USDCHF": "USDCHF=X",
}

# 现货品种 → 新浪财经代码（CFD/现货价格，非期货）
SINA_TICKER_MAP = {
    "GOLD": "hf_XAU", "XAUUSD": "hf_XAU",   # 现货黄金
    "XAGUSD": "hf_XAG", "SILVER": "hf_XAG",    # 现货白银
    "COPPER": "hf_HG",                         # 铜
    "CRUDE": "hf_CL", "USOIL": "hf_CL",       # WTI 原油
    "UKOIL": "hf_OIL",                         # 布伦特原油
}


# ── URL 解析 ──────────────────────────────────────────────────────

def parse_tradingview_url(url: str) -> dict | None:
    m = re.search(r"/symbols/([^/?]+)", url)
    if not m:
        return None
    part = m.group(1)
    if "-" in part:
        exchange, code = part.split("-", 1)
        return {"exchange": exchange.upper(), "code": code.upper()}
    return {"exchange": None, "code": part.upper()}


def parse_eastmoney_url(url: str) -> dict | None:
    m = re.search(r"/(sh|sz)(\d+)\.html", url)
    if not m:
        return None
    return {"market": m.group(1), "code": m.group(2)}


# ── 转换为数据源 ticker ───────────────────────────────────────────

def to_ticker(tv_info: dict | None, em_info: dict | None) -> tuple[str, str] | None:
    """返回 (source, ticker)，source 为 'yahoo' 或 'sina'"""
    # 东方财富 URL → A股 Yahoo ticker
    if em_info:
        suffix = ".SS" if em_info["market"] == "sh" else ".SZ"
        return ("yahoo", em_info["code"] + suffix)

    if tv_info is None:
        return None

    code = tv_info["code"]
    exchange = tv_info["exchange"]

    # 新浪现货品种优先（CFD 价格）
    if code in SINA_TICKER_MAP:
        return ("sina", SINA_TICKER_MAP[code])

    # Yahoo 特殊品种
    if code in YAHOO_TICKER_MAP:
        return ("yahoo", YAHOO_TICKER_MAP[code])

    # 按交易所加后缀 → Yahoo
    if exchange in EXCHANGE_SUFFIX:
        suffix = EXCHANGE_SUFFIX[exchange]
        if exchange == "HKEX":
            code = code.lstrip("0").zfill(4)
        return ("yahoo", code + suffix)

    return None


# ── 价格获取 ─────────────────────────────────────────────────────

def fetch_yahoo_batch(tickers: list[str]) -> dict:
    """Yahoo Finance API 批量获取"""
    result = {}
    for ticker in tickers:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(ticker, safe='')}?range=5d&interval=1d")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if not closes:
                continue
            price = closes[-1]
            change_pct = ""
            if len(closes) >= 2:
                prev = closes[-2]
                pct = (price - prev) / prev * 100
                change_pct = f"{pct:+.2f}%"
            result[ticker] = {"price": round(price, 4), "change_pct": change_pct}
        except Exception as e:
            print(f"  [yahoo] {ticker}: {e}")
    return result


def fetch_sina_batch(tickers: list[str]) -> dict:
    """新浪财经 API 批量获取（现货贵金属/原油）"""
    if not tickers:
        return {}
    result = {}
    url = f"https://hq.sinajs.cn/list={','.join(tickers)}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://finance.sina.com.cn"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk")
        for line in text.strip().split("\n"):
            if '=""' in line or "=" not in line:
                continue
            code = line.split("_str_")[1].split("=")[0]
            data = line.split('"')[1]
            fields = data.split(",")
            if len(fields) < 5:
                continue
            try:
                price = float(fields[0])
                prev_close = float(fields[4]) if fields[4] else 0
                change_pct = ""
                if prev_close > 0:
                    pct = (price - prev_close) / prev_close * 100
                    change_pct = f"{pct:+.2f}%"
                result[code] = {"price": round(price, 4), "change_pct": change_pct}
            except (ValueError, IndexError):
                continue
    except Exception as e:
        print(f"  [sina] {e}")
    return result


# ── Frontmatter 读写 ─────────────────────────────────────────────

def read_frontmatter_fields(filepath: Path) -> dict:
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm_raw = text[3:end]
    fm = {}
    for key in ("tradingview", "东方财富"):
        m = re.search(rf"^{re.escape(key)}:\s*(.+)$", fm_raw, re.MULTILINE)
        if m:
            fm[key] = m.group(1).strip()
    # 解析 状态 列表
    statuses = re.findall(r"^  - (.+)$",
                          fm_raw[fm_raw.find("状态:"):] if "状态:" in fm_raw else "",
                          re.MULTILINE)
    fm["状态"] = [s.strip() for s in statuses]
    return fm


def update_frontmatter(filepath: Path, price: float, updated_time: str) -> bool:
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("---", 3)
    if end == -1:
        return False

    fm_raw = text[3:end]
    body = text[end + 3:]

    for key, val in [("current_price", price), ("price_updated", updated_time)]:
        if re.search(rf"^{key}:", fm_raw, re.MULTILINE):
            fm_raw = re.sub(rf"^{key}:.*$", f"{key}: {val}", fm_raw, flags=re.MULTILINE)
        else:
            fm_raw = fm_raw.rstrip("\n") + f"\n{key}: {val}\n"

    filepath.write_text(f"---{fm_raw}---{body}", encoding="utf-8")
    return True


# ── 主逻辑 ────────────────────────────────────────────────────────

def collect_files(args: list[str]) -> list[Path]:
    if args:
        files = []
        for arg in args:
            p = Path(arg)
            if not p.exists():
                print(f"文件不存在: {arg}")
            elif p.is_file():
                files.append(p)
            elif p.is_dir():
                found = sorted(p.rglob("*.md"))
                print(f"扫描 {p.name}/，找到 {len(found)} 个文件")
                files.extend(found)
        return files
    if not MICRO_DIR.exists():
        print(f"目录不存在: {MICRO_DIR}")
        sys.exit(1)
    files = sorted(MICRO_DIR.rglob("*.md"))
    print(f"扫描 33-Micro/，找到 {len(files)} 个文件")
    return files


ACTIVE_STATUSES = {"持有", "重点关注"}


def main():
    dry_run = "--dry-run" in sys.argv
    fetch_all = "--all" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    # 指定了具体文件/目录时，不做状态过滤
    explicit_files = bool(args)
    files = collect_files(args)

    # 第一遍：收集所有 ticker
    file_ticker_map = []  # [(filepath, source, ticker)]
    skipped = 0
    for f in files:
        fm = read_frontmatter_fields(f)
        tv_url = fm.get("tradingview", "")
        em_url = fm.get("东方财富", "")
        if not tv_url and not em_url:
            continue

        # 状态过滤：默认只更新「持有」或「重点关注」
        if not explicit_files and not fetch_all:
            if not ACTIVE_STATUSES.intersection(fm.get("状态", [])):
                skipped += 1
                continue

        tv_info = parse_tradingview_url(tv_url) if tv_url else None
        em_info = parse_eastmoney_url(em_url) if em_url else None
        result = to_ticker(tv_info, em_info)
        if result:
            source, ticker = result
            file_ticker_map.append((f, source, ticker))
        else:
            print(f"  ✗ {f.name} - 无法映射 ticker")

    if skipped:
        print(f"跳过 {skipped} 个非关注标的（使用 --all 更新全部）")

    if not file_ticker_map:
        print("没有找到可更新的标的")
        return

    # 第二遍：按数据源分组，批量获取价格
    yahoo_tickers = list(set(t for _, s, t in file_ticker_map if s == "yahoo"))
    sina_tickers = list(set(t for _, s, t in file_ticker_map if s == "sina"))

    total = len(yahoo_tickers) + len(sina_tickers)
    print(f"获取 {total} 个 ticker 的价格（Yahoo: {len(yahoo_tickers)}, 新浪: {len(sina_tickers)}）...")

    prices = {}
    if yahoo_tickers:
        prices.update(fetch_yahoo_batch(yahoo_tickers))
    if sina_tickers:
        prices.update(fetch_sina_batch(sina_tickers))

    # 第三遍：写入结果
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M")
    ok = 0
    for filepath, source, ticker in file_ticker_map:
        if ticker not in prices:
            print(f"  ✗ {filepath.name} ({ticker}) - 获取失败")
            continue
        p = prices[ticker]
        if dry_run:
            print(f"  ✓ {filepath.name}: {p['price']} {p['change_pct']} [{ticker}]")
        elif update_frontmatter(filepath, p["price"], now_str):
            print(f"  ✓ {filepath.name}: {p['price']} {p['change_pct']}")
        else:
            print(f"  ✗ {filepath.name}: 写入失败")
            continue
        ok += 1

    print(f"\n完成: {ok}/{len(file_ticker_map)} 个标的已更新")


if __name__ == "__main__":
    main()
