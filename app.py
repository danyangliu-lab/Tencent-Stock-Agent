"""
腾讯股票 AI Agent - 后端服务
提供新闻抓取、股票数据获取、AI分析等API
"""

import os
import json
import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

# 确保从 app.py 所在目录加载 .env
_APP_DIR = Path(__file__).resolve().parent
_env_file = _APP_DIR / ".env"
load_dotenv(_env_file)

app = FastAPI(title="腾讯股票AI Agent")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


@app.on_event("startup")
async def startup_log():
    import logging
    logging.warning(
        f"[Config] env={_env_file} exists={_env_file.exists()} "
        f"LLM_KEY={'SET(' + str(len(LLM_API_KEY)) + ')' if LLM_API_KEY else 'EMPTY'} "
        f"MODEL={LLM_MODEL}"
    )

# 简单内存缓存
_cache: dict = {}
CACHE_TTL = 300  # 5分钟

# AI 评级每日缓存（key: 日期字符串, value: 评级结果 dict）
_rating_cache: dict = {}


def _get_cache(key: str):
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _set_cache(key: str, data):
    _cache[key] = (time.time(), data)


# ---------------------------------------------------------------------------
# 股票数据获取 (使用公开接口)
# ---------------------------------------------------------------------------
async def fetch_stock_data() -> dict:
    """获取腾讯控股(00700.HK)实时股票数据"""
    cached = _get_cache("stock_data")
    if cached:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn",
    }

    stock_info = {
        "name": "腾讯控股",
        "code": "00700.HK",
        "current_price": "--",
        "change": "--",
        "change_percent": "--",
        "open": "--",
        "high": "--",
        "low": "--",
        "prev_close": "--",
        "volume": "--",
        "turnover": "--",
        "market_cap": "--",
        "pe_ratio": "--",
        "52w_high": "--",
        "52w_low": "--",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            # 新浪港股实时数据接口
            resp = await client.get(
                "https://hq.sinajs.cn/list=rt_hk00700",
                headers={**headers, "Referer": "https://finance.sina.com.cn"},
            )
            if resp.status_code == 200:
                text = resp.text
                # 格式: var hq_str_rt_hk00700="...字段用逗号分隔..."
                if '"' in text:
                    data_str = text.split('"')[1]
                    fields = data_str.split(",")
                    if len(fields) > 15:
                        stock_info.update({
                            "name": "腾讯控股",
                            "name_en": fields[0] if fields[0] else "TENCENT",
                            "current_price": fields[6],
                            "change": fields[7],
                            "change_percent": fields[8],
                            "prev_close": fields[3],
                            "open": fields[2],
                            "high": fields[4],
                            "low": fields[5],
                            "volume": fields[12],
                            "turnover": fields[11],
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
    except Exception as e:
        print(f"[Stock] 新浪接口异常: {e}")

    # 尝试腾讯财经接口获取更多数据
    try:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            resp = await client.get(
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param=hk00700,day,,{today},5,qfq"
            )
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data and "hk00700" in data["data"]:
                    qt = data["data"]["hk00700"].get("qt", {}).get("hk00700", [])
                    if qt and len(qt) > 45:
                        if stock_info["current_price"] == "--":
                            stock_info["current_price"] = qt[3]
                        stock_info["market_cap"] = qt[45] if len(qt) > 45 else "--"
                        stock_info["pe_ratio"] = qt[39] if len(qt) > 39 else "--"
                        stock_info["52w_high"] = qt[48] if len(qt) > 48 else "--"
                        stock_info["52w_low"] = qt[49] if len(qt) > 49 else "--"
                        stock_info["dividend_yield"] = qt[47] if len(qt) > 47 else "--"
                        stock_info["pb_ratio"] = qt[51] if len(qt) > 51 else "--"
                        stock_info["turnover_rate"] = qt[50] if len(qt) > 50 else "--"
                        stock_info["amplitude"] = qt[43] if len(qt) > 43 else "--"
                        stock_info["total_shares"] = qt[69] if len(qt) > 69 else "--"
                        stock_info["float_shares"] = qt[70] if len(qt) > 70 else "--"
                        stock_info["nav_per_share"] = qt[72] if len(qt) > 72 else "--"
    except Exception as e:
        print(f"[Stock] 腾讯接口异常: {e}")

    _set_cache("stock_data", stock_info)
    return stock_info


async def fetch_kline_data(period: str = "day", count: int = 60) -> list:
    """获取腾讯K线数据
    period: day(日线), week(周线), month(月线)
    count: 请求数据条数
    """
    cache_key = f"kline_{period}_{count}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    # 限制合法值
    if period not in ("day", "week", "month"):
        period = "day"
    count = min(max(count, 10), 1500)

    kline_list = []
    try:
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param=hk00700,{period},,{today},{count},qfq"
            )
            if resp.status_code == 200:
                raw = resp.json()
                d = raw.get("data", {})
                if isinstance(d, dict):
                    hk = d.get("hk00700", {})
                    if isinstance(hk, dict):
                        items = (
                            hk.get(period, [])
                            or hk.get(f"qfq{period}", [])
                        )
                        for row in items:
                            kline_list.append({
                                "date": row[0],
                                "open": float(row[1]),
                                "close": float(row[2]),
                                "high": float(row[3]),
                                "low": float(row[4]),
                                "volume": float(row[5]) if len(row) > 5 else 0,
                            })
    except Exception as e:
        print(f"[KLine] 获取K线异常({period}/{count}): {e}")

    _set_cache(cache_key, kline_list)
    return kline_list


# ---------------------------------------------------------------------------
# 新闻抓取 (多维度专业股票资讯)
# ---------------------------------------------------------------------------

# 股票专业关键词，用于标记新闻类型
_STOCK_KEYWORDS = [
    "股价", "港股", "涨", "跌", "市值", "财报", "营收", "净利", "利润",
    "研报", "评级", "目标价", "回购", "分红", "派息", "增持", "减持",
    "大行", "券商", "分析师", "投行", "摩根", "高盛", "瑞银", "花旗",
    "美银", "汇丰", "摩通", "大摩", "小摩", "中金", "中信", "华泰",
    "估值", "PE", "市盈率", "EPS", "业绩", "季报", "年报", "中报",
    "K线", "均线", "技术面", "基本面", "做多", "做空", "融资", "融券",
    "恒生", "恒指", "科技股", "中概股", "ADR", "成交额", "换手率",
    "牛市", "熊市", "反弹", "回调", "突破", "支撑", "阻力",
    "00700", "HK", "控股",
    # English keywords for international media
    "stock", "share", "rally", "surge", "drop", "plunge", "dividend",
    "earnings", "revenue", "profit", "valuation", "analyst", "upgrade",
    "downgrade", "target price", "buyback", "IPO", "HKEX",
    "Morgan", "Goldman", "UBS", "Citi", "HSBC", "JP Morgan",
    "bull", "bear", "rally", "sell-off",
]


def _classify_news(title: str, summary: str = "") -> str:
    """根据标题和摘要判断新闻类型: stock(股票专业) / general(综合资讯)"""
    text = title + summary
    for kw in _STOCK_KEYWORDS:
        if kw in text:
            return "stock"
    return "general"


async def _search_sina(client: httpx.AsyncClient, query: str, num: int = 15) -> list:
    """通用新浪搜索方法"""
    results = []
    try:
        resp = await client.get(
            "https://search.sina.com.cn/news",
            params={
                "q": query,
                "c": "news",
                "from": "channel",
                "ie": "utf-8",
                "num": str(num),
            },
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            for item in soup.select(".box-result"):
                h2 = item.select_one("h2 a")
                if h2:
                    title = h2.get_text(strip=True)
                    url = h2.get("href", "")
                    info = item.select_one(".fgray_time")
                    time_str = info.get_text(strip=True) if info else ""
                    summary_el = item.select_one(".content")
                    summary = summary_el.get_text(strip=True)[:120] if summary_el else ""
                    if title:
                        tag = _classify_news(title, summary)
                        results.append({
                            "title": title,
                            "url": url,
                            "source": "新浪财经",
                            "time": time_str,
                            "summary": summary,
                            "tag": tag,
                        })
    except Exception as e:
        print(f"[News] 新浪搜索({query})异常: {e}")
    return results


async def fetch_news() -> list:
    """多维度抓取腾讯控股专业股票资讯"""
    cached = _get_cache("news_data")
    if cached:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    }

    all_news: list = []

    async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
        # 并发发起多维度搜索
        tasks = [
            _search_sina(client, "腾讯控股 股价", 10),
            _search_sina(client, "腾讯 港股 分析", 10),
            _search_sina(client, "00700 研报", 8),
            _search_sina(client, "腾讯", 10),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, list):
                all_news.extend(res)

    # 补充来源: 新浪财经滚动新闻
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            resp = await client.get(
                "https://feed.mix.sina.com.cn/api/roll/get",
                params={
                    "pageid": "153",
                    "lid": "2516",
                    "k": "腾讯",
                    "num": "20",
                    "page": "1",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("result", {}).get("data", [])
                for item in items:
                    title = item.get("title", "").strip()
                    intro = item.get("intro", "")
                    if title and ("腾讯" in title or "腾讯" in intro):
                        tag = _classify_news(title, intro)
                        all_news.append({
                            "title": title,
                            "url": item.get("url", ""),
                            "source": item.get("media_name", "新浪财经") or "新浪财经",
                            "time": datetime.fromtimestamp(
                                int(item.get("ctime", 0))
                            ).strftime("%m-%d %H:%M") if item.get("ctime") else "",
                            "summary": intro[:120] if intro else "",
                            "tag": tag,
                        })
    except Exception as e:
        print(f"[News] 新浪财经滚动异常: {e}")

    # 来源: Google News RSS (国际媒体英文新闻)
    for gn_query in ["Tencent+stock", "Tencent+00700"]:
        try:
            async with httpx.AsyncClient(timeout=12, headers=headers, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://news.google.com/rss/search?q={gn_query}&hl=en&gl=US&ceid=US:en"
                )
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "xml")
                    for item in soup.select("item")[:8]:
                        title = item.select_one("title")
                        link = item.select_one("link")
                        source_el = item.select_one("source")
                        pub_date = item.select_one("pubDate")
                        if title and link:
                            t = title.get_text(strip=True)
                            s = source_el.get_text(strip=True) if source_el else "Google News"
                            time_str = ""
                            if pub_date:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    dt = parsedate_to_datetime(pub_date.get_text(strip=True))
                                    time_str = dt.strftime("%m-%d %H:%M")
                                except Exception:
                                    pass
                            tag = _classify_news(t, "")
                            all_news.append({
                                "title": t,
                                "url": link.get_text(strip=True),
                                "source": s,
                                "time": time_str,
                                "summary": "",
                                "tag": tag,
                                "lang": "en",
                            })
        except Exception as e:
            print(f"[News] Google News({gn_query})异常: {e}")

    # 去重（按标题）
    seen: set = set()
    unique_news: list = []
    for n in all_news:
        if n["title"] not in seen:
            seen.add(n["title"])
            unique_news.append(n)

    # 排序：股票专业类优先
    unique_news.sort(key=lambda x: (0 if x.get("tag") == "stock" else 1))

    result = unique_news[:25]
    _set_cache("news_data", result)
    return result


# ---------------------------------------------------------------------------
# AI 分析 (流式)
# ---------------------------------------------------------------------------
async def stream_ai_analysis(stock_data: dict, news_list: list, kline_data: list):
    """调用大模型生成流式分析报告"""
    # 构建分析上下文
    news_text = "\n".join(
        [f"- {n['title']}（{n['source']}）" for n in news_list[:10]]
    ) or "暂无最新新闻"

    kline_summary = ""
    if kline_data:
        recent = kline_data[-5:]
        kline_summary = "近5个交易日行情:\n"
        for k in recent:
            kline_summary += (
                f"  {k['date']}: 开{k['open']} 收{k['close']} "
                f"高{k['high']} 低{k['low']}\n"
            )

    prompt = f"""你是一位资深港股分析师和AI投资顾问。请根据以下腾讯控股(00700.HK)的最新数据，
给出专业、详细的股票分析报告和投资建议。

## 当前股票数据
- 股票名称: {stock_data.get('name', '腾讯控股')}
- 股票代码: {stock_data.get('code', '00700.HK')}
- 当前价格: {stock_data.get('current_price', '--')} HKD
- 涨跌额: {stock_data.get('change', '--')}
- 涨跌幅: {stock_data.get('change_percent', '--')}%
- 今开: {stock_data.get('open', '--')}
- 最高: {stock_data.get('high', '--')}
- 最低: {stock_data.get('low', '--')}
- 昨收: {stock_data.get('prev_close', '--')}
- 成交量: {stock_data.get('volume', '--')}
- 成交额: {stock_data.get('turnover', '--')}
- 市值: {stock_data.get('market_cap', '--')}
- 市盈率: {stock_data.get('pe_ratio', '--')}
- 52周最高: {stock_data.get('52w_high', '--')}
- 52周最低: {stock_data.get('52w_low', '--')}

{kline_summary}

## 最新相关新闻
{news_text}

## 请输出以下内容:
1. **市场概览** - 当前价格走势分析
2. **技术面分析** - 基于K线数据的技术指标分析
3. **消息面分析** - 根据最新新闻解读市场情绪
4. **基本面分析** - 估值水平和业务发展
5. **风险提示** - 当前面临的主要风险
6. **操作建议** - 给出具体的投资建议（短期/中期/长期）

请使用Markdown格式输出，要求专业、客观、全面。分析日期: {datetime.now().strftime('%Y年%m月%d日')}

⚠️ 免责声明：以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。"""

    if not LLM_API_KEY:
        # 没有配置API Key时，返回模拟分析
        yield _generate_fallback_analysis(stock_data, news_list, kline_data)
        return

    try:
        req_body = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": "你是一位资深港股分析师，擅长技术分析和基本面分析。你的分析专业、客观、全面。"},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 3000,
        }
        # Gemini 2.5 thinking 模型: 用 low 限制思考 token，把更多配额给实际输出
        if "2.5" in LLM_MODEL:
            req_body["max_tokens"] = 8000
            req_body["reasoning_effort"] = "low"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            async with client.stream(
                "POST",
                f"{LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=req_body,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = ""
                    try:
                        err = json.loads(body)
                        if isinstance(err, list) and err:
                            err = err[0]
                        error_msg = err.get("error", {}).get("message", "")[:200]
                    except Exception:
                        error_msg = body.decode("utf-8", errors="ignore")[:200]
                    print(f"[AI] API返回 {response.status_code}: {error_msg}")
                    yield f"\n\n> ⚠️ **AI模型调用失败**（HTTP {response.status_code}）：{error_msg}\n\n> 已降级为本地模板分析。\n\n---\n\n"
                    yield _generate_fallback_analysis(stock_data, news_list, kline_data)
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
    except Exception as e:
        print(f"[AI] 调用异常: {e}")
        yield _generate_fallback_analysis(stock_data, news_list, kline_data)


def _generate_fallback_analysis(stock_data: dict, news_list: list, kline_data: list) -> str:
    """当AI接口不可用时的本地分析报告"""
    price = stock_data.get("current_price", "--")
    change = stock_data.get("change", "--")
    change_pct = stock_data.get("change_percent", "--")
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    # 分析K线趋势
    trend_text = ""
    if kline_data and len(kline_data) >= 5:
        recent_5 = kline_data[-5:]
        closes = [k["close"] for k in recent_5]
        if closes[-1] > closes[0]:
            trend_text = "近5个交易日整体呈上涨趋势"
        elif closes[-1] < closes[0]:
            trend_text = "近5个交易日整体呈下跌趋势"
        else:
            trend_text = "近5个交易日整体呈震荡态势"

        avg_5 = sum(closes) / len(closes)
        if len(kline_data) >= 20:
            avg_20 = sum(k["close"] for k in kline_data[-20:]) / 20
            if avg_5 > avg_20:
                trend_text += "，5日均线位于20日均线上方，短期偏多"
            else:
                trend_text += "，5日均线位于20日均线下方，短期偏空"

    news_section = ""
    if news_list:
        news_items = "\n".join([f"- {n['title']}（来源: {n['source']}）" for n in news_list[:8]])
        news_section = f"""
### 📰 最新新闻动态

{news_items}

以上新闻反映了市场对腾讯的最新关注点。投资者需结合新闻内容分析对股价的潜在影响。
"""
    else:
        news_section = "\n### 📰 最新新闻动态\n\n暂未获取到最新的腾讯相关新闻。\n"

    kline_detail = ""
    if kline_data and len(kline_data) >= 5:
        kline_rows = ""
        for k in kline_data[-5:]:
            change_val = k["close"] - k["open"]
            emoji = "🔴" if change_val >= 0 else "🟢"
            kline_rows += f"| {k['date']} | {k['open']} | {k['close']} | {k['high']} | {k['low']} | {emoji} {change_val:+.2f} |\n"
        kline_detail = f"""
| 日期 | 开盘 | 收盘 | 最高 | 最低 | 涨跌 |
|------|------|------|------|------|------|
{kline_rows}"""

    return f"""# 🏦 腾讯控股（00700.HK）AI分析报告

> 📅 分析时间：{now}

---

## 1. 📊 市场概览

腾讯控股（00700.HK）当前报价 **{price} HKD**，涨跌额 {change}，涨跌幅 {change_pct}%。

| 指标 | 数值 |
|------|------|
| 当前价格 | {price} HKD |
| 今日开盘 | {stock_data.get('open', '--')} |
| 最高价 | {stock_data.get('high', '--')} |
| 最低价 | {stock_data.get('low', '--')} |
| 昨日收盘 | {stock_data.get('prev_close', '--')} |
| 成交量 | {stock_data.get('volume', '--')} |
| 成交额 | {stock_data.get('turnover', '--')} |
| 市盈率(PE) | {stock_data.get('pe_ratio', '--')} |
| 总市值 | {stock_data.get('market_cap', '--')} |
| 52周最高 | {stock_data.get('52w_high', '--')} |
| 52周最低 | {stock_data.get('52w_low', '--')} |

---

## 2. 📈 技术面分析

{trend_text if trend_text else "暂无足够的K线数据进行技术分析。"}

{kline_detail}

**技术指标解读：**
- 关注成交量变化，放量上涨为积极信号
- 关注关键支撑位与阻力位的突破情况
- 建议结合MACD、RSI等技术指标综合判断

---

## 3. 📰 消息面分析

{news_section}

---

## 4. 🏢 基本面分析

腾讯控股作为中国最大的互联网公司之一，业务涵盖：
- **游戏业务**：全球领先的游戏发行商，持续贡献核心收入
- **社交平台**：微信/WeChat月活超13亿，具备强大的生态壁垒
- **金融科技**：微信支付、理财通等金融科技服务持续增长
- **云服务**：腾讯云在国内市场份额稳步提升
- **投资生态**：持有众多优质公司股权，投资回报可观

当前市盈率为 {stock_data.get('pe_ratio', '--')}，投资者可参考历史估值中枢评估当前估值水平。

---

## 5. ⚠️ 风险提示

1. **政策监管风险**：互联网行业政策持续演变，需关注监管动态
2. **宏观经济风险**：全球经济不确定性可能影响业务增长
3. **行业竞争风险**：短视频、电商等领域竞争加剧
4. **地缘政治风险**：中美关系变化可能影响港股市场情绪
5. **汇率风险**：港币兑人民币汇率波动影响实际收益

---

## 6. 💡 操作建议

| 策略 | 建议 |
|------|------|
| **短期（1-2周）** | 关注技术面支撑/压力位，轻仓灵活操作 |
| **中期（1-3月）** | 关注财报发布和业务数据，逢低布局 |
| **长期（6月以上）** | 腾讯基本面优质，适合长期价值投资 |

---

> ⚠️ **免责声明**：以上分析由AI生成，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。请投资者根据自身风险承受能力做出独立判断。
"""


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------
@app.get("/api/stock")
async def get_stock():
    """获取腾讯股票实时数据"""
    data = await fetch_stock_data()
    return {"code": 0, "data": data}


@app.get("/api/kline")
async def get_kline(period: str = "day", count: int = 60):
    """获取K线数据  period=day|week|month  count=数据条数"""
    data = await fetch_kline_data(period=period, count=count)
    return {"code": 0, "data": data}


@app.get("/api/news")
async def get_news():
    """获取腾讯相关新闻"""
    data = await fetch_news()
    return {"code": 0, "data": data}


@app.get("/api/analysis")
async def get_analysis():
    """获取AI分析报告(流式)"""
    stock_data, news_list, kline_data = await asyncio.gather(
        fetch_stock_data(),
        fetch_news(),
        fetch_kline_data(),
    )

    async def _gen():
        async for chunk in stream_ai_analysis(stock_data, news_list, kline_data):
            yield chunk

    return _sse_wrap(_gen())


@app.post("/api/refresh")
async def refresh_data():
    """刷新缓存，重新获取数据"""
    _cache.clear()
    _rating_cache.clear()
    stock_data, news_list, kline_data = await asyncio.gather(
        fetch_stock_data(),
        fetch_news(),
        fetch_kline_data(),
    )
    return {
        "code": 0,
        "data": {
            "stock": stock_data,
            "news": news_list,
            "kline_count": len(kline_data),
        },
    }


# ---------------------------------------------------------------------------
# AI 每日评级
# ---------------------------------------------------------------------------
@app.get("/api/rating")
async def get_rating():
    """获取 AI 每日评级（同一天内缓存结果）"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 检查每日缓存
    if today in _rating_cache:
        return {"code": 0, "data": _rating_cache[today]}

    # 并发获取数据
    stock_data, news_list, kline_data = await asyncio.gather(
        fetch_stock_data(),
        fetch_news(),
        fetch_kline_data(period="day", count=30),
    )

    # 没有 API Key 时返回默认中性评级
    if not LLM_API_KEY:
        fallback = {
            "date": today,
            "rating": "中性",
            "score": 50,
            "summary": "未配置 AI 大模型 API Key，无法生成智能评级。请在 .env 中配置 LLM_API_KEY 后重试。",
            "factors": {
                "technical": "无法分析",
                "fundamental": "无法分析",
                "sentiment": "无法分析",
            },
        }
        _rating_cache[today] = fallback
        return {"code": 0, "data": fallback}

    # 构建评级 Prompt
    news_text = "\n".join(
        [f"- {n['title']}（{n['source']}）" for n in news_list[:12]]
    ) or "暂无最新新闻"

    kline_summary = ""
    if kline_data:
        recent = kline_data[-10:]
        kline_summary = "近10个交易日行情:\n"
        for k in recent:
            kline_summary += (
                f"  {k['date']}: 开{k['open']} 收{k['close']} "
                f"高{k['high']} 低{k['low']}\n"
            )

    prompt = f"""你是一位资深港股分析师。请根据以下腾讯控股(00700.HK)最新数据，给出今日投资评级。

## 当前股票数据
- 当前价格: {stock_data.get('current_price', '--')} HKD
- 涨跌幅: {stock_data.get('change_percent', '--')}%
- 今开: {stock_data.get('open', '--')} 最高: {stock_data.get('high', '--')} 最低: {stock_data.get('low', '--')}
- 成交量: {stock_data.get('volume', '--')}  成交额: {stock_data.get('turnover', '--')}
- PE: {stock_data.get('pe_ratio', '--')}  PB: {stock_data.get('pb_ratio', '--')}
- 市值: {stock_data.get('market_cap', '--')}亿
- 换手率: {stock_data.get('turnover_rate', '--')}%  振幅: {stock_data.get('amplitude', '--')}%
- 52周高: {stock_data.get('52w_high', '--')} 52周低: {stock_data.get('52w_low', '--')}

{kline_summary}

## 最新新闻
{news_text}

## 评级要求
请严格以如下 JSON 格式返回（不要输出其他内容，仅返回 JSON）：
{{
  "rating": "强烈推荐/推荐/中性/谨慎/回避（五选一）",
  "score": 0-100的整数评分,
  "summary": "一句话评级理由（30字以内）",
  "factors": {{
    "technical": "技术面一句话判断（20字以内）",
    "fundamental": "基本面一句话判断（20字以内）",
    "sentiment": "消息面一句话判断（20字以内）"
  }}
}}

评分参考: 强烈推荐 80-100, 推荐 60-79, 中性 40-59, 谨慎 20-39, 回避 0-19
评级日期: {today}"""

    try:
        result_text = ""
        req_body = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": "你是一位资深港股分析师。请严格按要求的JSON格式返回评级结果，不要输出任何其他内容。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        if "2.5" in LLM_MODEL:
            req_body["reasoning_effort"] = "low"

        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
            resp = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=req_body,
            )
            if resp.status_code == 200:
                body = resp.json()
                print(f"[Rating] API响应keys: {list(body.keys())}")
                msg = body.get("choices", [{}])[0].get("message", {})
                content = msg.get("content") or ""
                # Gemini 2.5 thinking 模型可能在 parts 中返回
                if not content and "parts" in msg:
                    for part in msg["parts"]:
                        if isinstance(part, dict) and part.get("text"):
                            content = part["text"]
                            break
                result_text = content.strip()
                print(f"[Rating] 提取到内容长度: {len(result_text)}, 前100字: {result_text[:100]}")
            else:
                err_body = resp.text[:300]
                print(f"[Rating] API返回 {resp.status_code}: {err_body}")
                raise Exception(f"API HTTP {resp.status_code}")

        if not result_text:
            raise Exception("AI返回内容为空")

        # 解析 JSON（容错处理：去掉可能的 markdown 代码块标记）
        cleaned = result_text
        if "```" in cleaned:
            # 提取第一个 ``` 和最后一个 ``` 之间的内容
            import re
            json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", cleaned, re.DOTALL)
            if json_match:
                cleaned = json_match.group(1)
        cleaned = cleaned.strip()

        rating_data = json.loads(cleaned)

        # 校验必要字段
        valid_ratings = ["强烈推荐", "推荐", "中性", "谨慎", "回避"]
        if rating_data.get("rating") not in valid_ratings:
            rating_data["rating"] = "中性"
        score = int(rating_data.get("score", 50))
        rating_data["score"] = max(0, min(100, score))
        rating_data["date"] = today

        if "factors" not in rating_data:
            rating_data["factors"] = {
                "technical": "--",
                "fundamental": "--",
                "sentiment": "--",
            }

        _rating_cache[today] = rating_data
        return {"code": 0, "data": rating_data}

    except Exception as e:
        print(f"[Rating] 评级异常: {e}")
        fallback = {
            "date": today,
            "rating": "中性",
            "score": 50,
            "summary": f"AI 评级生成失败，请稍后重试。",
            "factors": {
                "technical": "--",
                "fundamental": "--",
                "sentiment": "--",
            },
        }
        return {"code": 0, "data": fallback}


# ---------------------------------------------------------------------------
# 通用流式 LLM 调用
# ---------------------------------------------------------------------------
async def _stream_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2000):
    """通用流式 LLM 调用，yield 文本 chunk"""
    if not LLM_API_KEY:
        yield "> ⚠️ 未配置 LLM_API_KEY，无法调用 AI 模型。\n"
        return

    # 构建请求体
    req_body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    # Gemini 2.5 thinking 模型: 用 low 限制思考 token，把更多配额给实际输出
    if "2.5" in LLM_MODEL:
        req_body["max_tokens"] = max(max_tokens, 8000)
        req_body["reasoning_effort"] = "low"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            async with client.stream(
                "POST",
                f"{LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=req_body,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = ""
                    try:
                        err = json.loads(body)
                        if isinstance(err, list) and err:
                            err = err[0]
                        error_msg = err.get("error", {}).get("message", "")[:200]
                    except Exception:
                        error_msg = body.decode("utf-8", errors="ignore")[:200]
                    print(f"[LLM] API返回 {response.status_code}: {error_msg}")
                    yield f"\n> ⚠️ AI模型调用失败（HTTP {response.status_code}）：{error_msg}\n"
                    return

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
    except Exception as e:
        print(f"[LLM] 调用异常: {e}")
        yield f"\n> ⚠️ AI调用异常：{str(e)[:100]}\n"


def _sse_wrap(generator):
    """包装 async generator 为 SSE StreamingResponse"""
    async def event_stream():
        async for chunk in generator:
            yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 新闻摘要 API
# ---------------------------------------------------------------------------
@app.get("/api/summary")
async def get_summary():
    """AI 总结最新新闻资讯（流式）"""
    stock_data, news_list = await asyncio.gather(
        fetch_stock_data(),
        fetch_news(),
    )

    news_text = "\n".join(
        [f"{i+1}. [{n.get('lang','zh')=='en' and 'EN' or 'CN'}] {n['title']}（{n['source']}）"
         for i, n in enumerate(news_list[:20])]
    ) or "暂无新闻"

    prompt = f"""请对以下腾讯控股（00700.HK）相关新闻进行专业总结分析。

## 当前股价信息
- 价格: {stock_data.get('current_price', '--')} HKD
- 涨跌: {stock_data.get('change', '--')} ({stock_data.get('change_percent', '--')}%)

## 最新新闻列表
{news_text}

## 请输出:
1. **新闻要点总结**（3-5个核心要点，每个1-2句话）
2. **市场情绪判断**（偏多/偏空/中性，并说明原因）
3. **关键关注点**（未来需要跟踪的重点事件或数据）

要求：简洁精炼，要点明确，中文输出。如果有英文新闻请翻译总结。
分析时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}"""

    return _sse_wrap(_stream_llm(
        system_prompt="你是一位资深财经新闻编辑和港股分析师，擅长从海量新闻中提炼核心信息。请用简洁专业的风格总结。",
        user_prompt=prompt,
        max_tokens=1500,
    ))


# ---------------------------------------------------------------------------
# 自定义提问 API
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    prompt: str


@app.post("/api/chat")
async def post_chat(req: ChatRequest):
    """用户自定义提示词分析（流式）"""
    stock_data, news_list, kline_data = await asyncio.gather(
        fetch_stock_data(),
        fetch_news(),
        fetch_kline_data(),
    )

    news_brief = "\n".join(
        [f"- {n['title']}" for n in news_list[:10]]
    ) or "暂无"

    kline_brief = ""
    if kline_data:
        recent = kline_data[-5:]
        kline_brief = "\n".join(
            [f"  {k['date']}: 开{k['open']} 收{k['close']} 高{k['high']} 低{k['low']}" for k in recent]
        )

    context = f"""## 腾讯控股(00700.HK) 当前数据
- 价格: {stock_data.get('current_price', '--')} HKD
- 涨跌: {stock_data.get('change', '--')} ({stock_data.get('change_percent', '--')}%)
- 今开: {stock_data.get('open', '--')} 最高: {stock_data.get('high', '--')} 最低: {stock_data.get('low', '--')}
- 成交量: {stock_data.get('volume', '--')} 成交额: {stock_data.get('turnover', '--')}
- PE: {stock_data.get('pe_ratio', '--')} 市值: {stock_data.get('market_cap', '--')}

## 近5日行情
{kline_brief or '暂无'}

## 最新新闻
{news_brief}

---

## 用户的问题
{req.prompt}
"""

    return _sse_wrap(_stream_llm(
        system_prompt="你是一位资深港股分析师和AI投资顾问。用户会基于腾讯控股的实时数据向你提问，请给出专业、客观的回答。使用Markdown格式输出。",
        user_prompt=context,
        max_tokens=3000,
    ))


# 静态文件和首页
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
