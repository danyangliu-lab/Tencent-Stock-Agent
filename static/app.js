/**
 * 腾讯股票小助手 AI Agent - 前端交互逻辑
 */

// ============ 状态管理 ============
const state = {
    stock: null,
    news: [],
    kline: [],
    rating: null,
    // K线图表当前设置
    chartRange: '30d',   // 30d, 60d, 6m, 1y, 5y, all
    chartPeriod: 'day',  // day, week, month
    isAnalyzing: false,
    isRefreshing: false,
};

// 范围 → {period, count} 映射
function rangeToParams(range, period) {
    const map = {
        '30d':  { day: 30,   week: 6,    month: 2   },
        '60d':  { day: 60,   week: 12,   month: 3   },
        '6m':   { day: 130,  week: 26,   month: 6   },
        '1y':   { day: 250,  week: 52,   month: 12  },
        '5y':   { day: 1250, week: 260,  month: 60  },
        'all':  { day: 1500, week: 1500, month: 500 },
    };
    return (map[range] || map['30d'])[period] || 60;
}

// ============ 工具函数 ============
function formatNumber(num) {
    if (!num || num === '--') return '--';
    const n = parseFloat(num);
    if (isNaN(n)) return num;
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + '万';
    return n.toLocaleString('zh-CN');
}

function formatVolume(vol) {
    if (!vol || vol === '--') return '--';
    const n = parseFloat(vol);
    if (isNaN(n)) return vol;
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (n >= 1e4) return (n / 1e4).toFixed(0) + '万';
    return n.toLocaleString();
}

function formatMarketCap(val) {
    if (!val || val === '--') return '--';
    const n = parseFloat(val);
    if (isNaN(n)) return val;
    // 接口返回的市值单位是亿港元
    if (n >= 10000) return (n / 10000).toFixed(2) + '万亿';
    return n.toFixed(0) + '亿';
}

function formatTurnover(val) {
    if (!val || val === '--') return '--';
    const n = parseFloat(val);
    if (isNaN(n)) return val;
    if (n >= 1e12) return (n / 1e12).toFixed(2) + '万亿';
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (n >= 1e4) return (n / 1e4).toFixed(0) + '万';
    return n.toLocaleString();
}

function formatShares(val) {
    if (!val || val === '--' || val === '0') return '--';
    const n = parseFloat(val);
    if (isNaN(n)) return val;
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿股';
    if (n >= 1e4) return (n / 1e4).toFixed(0) + '万股';
    return n.toLocaleString() + '股';
}

// 简易Markdown渲染
function renderMarkdown(text) {
    let html = text
        // 代码块
        .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
        // 标题
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        // 粗体
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        // 斜体
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // 分隔线
        .replace(/^---$/gm, '<hr>')
        // 引用
        .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
        // 表格
        .replace(/^\|(.+)\|$/gm, (match) => {
            return match;
        })
        // 无序列表
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        // 有序列表
        .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
        // 段落
        .replace(/\n\n/g, '</p><p>')
        // 换行
        .replace(/\n/g, '<br>');

    // 包裹列表项
    html = html.replace(/(<li>.*?<\/li>(\s*<br>)?)+/g, (match) => {
        return '<ul>' + match.replace(/<br>/g, '') + '</ul>';
    });

    // 合并相邻的blockquote
    html = html.replace(/<\/blockquote>\s*<br>\s*<blockquote>/g, '<br>');

    // 处理表格
    html = html.replace(
        /(\|[^\n]+\|\s*<br>\|[-| :]+\|\s*<br>)((?:\|[^\n]+\|\s*(?:<br>)?)+)/g,
        (match, header, body) => {
            const headerCells = header.split('<br>')[0]
                .split('|').filter(c => c.trim());
            const rows = body.split('<br>').filter(r => r.trim() && !r.match(/^[\s|:-]+$/));

            let table = '<table><thead><tr>';
            headerCells.forEach(c => { table += `<th>${c.trim()}</th>`; });
            table += '</tr></thead><tbody>';

            rows.forEach(row => {
                const cells = row.split('|').filter(c => c.trim());
                if (cells.length > 0) {
                    table += '<tr>';
                    cells.forEach(c => { table += `<td>${c.trim()}</td>`; });
                    table += '</tr>';
                }
            });

            table += '</tbody></table>';
            return table;
        }
    );

    return '<p>' + html + '</p>';
}

// ============ API 调用 ============
async function fetchStockData() {
    try {
        const resp = await fetch('/api/stock');
        const json = await resp.json();
        if (json.code === 0) {
            state.stock = json.data;
            renderStockHero();
        }
    } catch (e) {
        console.error('获取股票数据失败:', e);
    }
}

async function fetchKlineData(period, count) {
    period = period || state.chartPeriod;
    count = count || rangeToParams(state.chartRange, period);
    try {
        const resp = await fetch(`/api/kline?period=${period}&count=${count}`);
        const json = await resp.json();
        if (json.code === 0) {
            state.kline = json.data;
            renderChart();
        }
    } catch (e) {
        console.error('获取K线数据失败:', e);
    }
}

async function fetchNewsData() {
    try {
        const resp = await fetch('/api/news');
        const json = await resp.json();
        if (json.code === 0) {
            state.news = json.data;
            renderNewsList();
        }
    } catch (e) {
        console.error('获取新闻失败:', e);
    }
}

// ============ AI 每日评级 ============
async function fetchRating() {
    try {
        const resp = await fetch('/api/rating');
        const json = await resp.json();
        if (json.code === 0) {
            state.rating = json.data;
            renderRatingBar();
        }
    } catch (e) {
        console.error('获取评级失败:', e);
        renderRatingFallback();
    }
}

function getRatingConfig(rating) {
    const map = {
        '强烈推荐': { color: '#34c759', bg: 'rgba(52,199,89,0.10)', icon: '🚀', barColor: '#34c759' },
        '推荐':     { color: '#30d158', bg: 'rgba(48,209,88,0.10)', icon: '👍', barColor: '#30d158' },
        '中性':     { color: '#ff9500', bg: 'rgba(255,149,0,0.10)', icon: '⚖️', barColor: '#ff9500' },
        '谨慎':     { color: '#ff6b35', bg: 'rgba(255,107,53,0.10)', icon: '⚠️', barColor: '#ff6b35' },
        '回避':     { color: '#ff3b30', bg: 'rgba(255,59,48,0.10)', icon: '🛑', barColor: '#ff3b30' },
    };
    return map[rating] || map['中性'];
}

function renderRatingBar() {
    const el = document.getElementById('ratingBar');
    const d = state.rating;
    if (!d) return;

    const cfg = getRatingConfig(d.rating);
    const score = d.score || 50;
    const factors = d.factors || {};

    el.innerHTML = `
        <div class="rating-content fade-in">
            <div class="rating-left">
                <div class="rating-badge" style="background:${cfg.bg};color:${cfg.color}">
                    <span class="rating-icon">${cfg.icon}</span>
                    <span class="rating-label">${d.rating}</span>
                </div>
                <div class="rating-score-wrap">
                    <div class="rating-score-bar">
                        <div class="rating-score-fill" style="width:${score}%;background:${cfg.barColor}"></div>
                    </div>
                    <span class="rating-score-num" style="color:${cfg.color}">${score}<small>/100</small></span>
                </div>
            </div>
            <div class="rating-center">
                <p class="rating-summary">${d.summary || '--'}</p>
                <div class="rating-factors">
                    <span class="rating-factor"><b>技术面</b> ${factors.technical || '--'}</span>
                    <span class="rating-factor-sep">|</span>
                    <span class="rating-factor"><b>基本面</b> ${factors.fundamental || '--'}</span>
                    <span class="rating-factor-sep">|</span>
                    <span class="rating-factor"><b>消息面</b> ${factors.sentiment || '--'}</span>
                </div>
            </div>
            <div class="rating-right">
                <span class="rating-date">${d.date || '--'}</span>
                <span class="rating-ai-tag"><span class="ai-icon-sm">✦</span> AI 评级</span>
            </div>
        </div>
    `;
}

function renderRatingFallback() {
    const el = document.getElementById('ratingBar');
    el.innerHTML = `
        <div class="rating-content fade-in">
            <div class="rating-left">
                <div class="rating-badge" style="background:rgba(142,142,147,0.12);color:var(--text-tertiary)">
                    <span class="rating-icon">⚖️</span>
                    <span class="rating-label">加载失败</span>
                </div>
            </div>
            <div class="rating-center">
                <p class="rating-summary" style="color:var(--text-tertiary)">评级数据加载失败，请刷新页面重试</p>
            </div>
        </div>
    `;
}

// ============ 渲染函数 ============
function renderStockHero() {
    const el = document.getElementById('stockHero');
    const d = state.stock;
    if (!d) return;

    const changeNum = parseFloat(d.change) || 0;
    const changePct = d.change_percent || '--';
    const direction = changeNum > 0 ? 'up' : changeNum < 0 ? 'down' : 'flat';
    const arrow = changeNum > 0 ? '↑' : changeNum < 0 ? '↓' : '';
    const sign = changeNum > 0 ? '+' : '';

    el.innerHTML = `
        <div class="hero-compact fade-in">
            <div class="hero-identity">
                <h1>${d.name || '腾讯控股'}</h1>
                <span class="code">${d.code || '00700.HK'}</span>
            </div>
            <div class="hero-price-group">
                <span class="price-current">${d.current_price || '--'}</span>
                <span class="price-unit">HKD</span>
                <span class="change-badge ${direction}">
                    ${arrow} ${sign}${d.change || '--'} (${sign}${changePct}%)
                </span>
            </div>
            <div class="hero-metrics-inline">
                <div class="metric-inline"><span class="ml">今开</span><span class="mv">${d.open || '--'}</span></div>
                <div class="metric-inline"><span class="ml">最高</span><span class="mv">${d.high || '--'}</span></div>
                <div class="metric-inline"><span class="ml">最低</span><span class="mv">${d.low || '--'}</span></div>
                <div class="metric-inline"><span class="ml">昨收</span><span class="mv">${d.prev_close || '--'}</span></div>
                <div class="metric-inline"><span class="ml">成交量</span><span class="mv">${formatVolume(d.volume)}</span></div>
                <div class="metric-inline"><span class="ml">成交额</span><span class="mv">${formatTurnover(d.turnover)}</span></div>
                <div class="metric-inline"><span class="ml">振幅</span><span class="mv">${d.amplitude ? d.amplitude + '%' : '--'}</span></div>
                <div class="metric-inline"><span class="ml">换手率</span><span class="mv">${d.turnover_rate ? d.turnover_rate + '%' : '--'}</span></div>
                <div class="metric-inline"><span class="ml">PE</span><span class="mv">${d.pe_ratio || '--'}</span></div>
                <div class="metric-inline"><span class="ml">PB</span><span class="mv">${d.pb_ratio || '--'}</span></div>
                <div class="metric-inline"><span class="ml">市值</span><span class="mv">${formatMarketCap(d.market_cap)}</span></div>
                <div class="metric-inline"><span class="ml">总股本</span><span class="mv">${formatShares(d.total_shares)}</span></div>
                <div class="metric-inline"><span class="ml">流通股</span><span class="mv">${formatShares(d.float_shares)}</span></div>
                <div class="metric-inline"><span class="ml">每股净资产</span><span class="mv">${d.nav_per_share || '--'}</span></div>
                <div class="metric-inline"><span class="ml">股息率</span><span class="mv">${d.dividend_yield ? d.dividend_yield + '%' : '--'}</span></div>
                <div class="metric-inline"><span class="ml">52周高</span><span class="mv">${d['52w_high'] || '--'}</span></div>
                <div class="metric-inline"><span class="ml">52周低</span><span class="mv">${d['52w_low'] || '--'}</span></div>
            </div>
        </div>
    `;
}

function renderNewsList() {
    const container = document.getElementById('newsList');
    const countEl = document.getElementById('newsCount');
    const news = state.news;

    countEl.textContent = news.length;

    if (news.length === 0) {
        container.innerHTML = `
            <div class="analysis-placeholder" style="min-height:100px">
                <p style="color:var(--text-tertiary);font-size:14px">暂无新闻数据</p>
            </div>
        `;
        return;
    }

    container.innerHTML = news.map(n => {
        const isStock = n.tag === 'stock';
        const isEn = n.lang === 'en';
        let tagHtml = isStock
            ? '<span class="news-tag news-tag-stock">📈 股票</span>'
            : '<span class="news-tag news-tag-general">📰 资讯</span>';
        if (isEn) tagHtml += '<span class="news-tag news-tag-en">EN</span>';
        return `
        <a class="news-item" href="${n.url}" target="_blank" rel="noopener noreferrer" style="display:block;text-decoration:none;">
            <div class="news-item-title">${tagHtml}${n.title}</div>
            <div class="news-item-meta">
                <span class="news-item-source">${n.source}</span>
                ${n.time ? `<span>${n.time}</span>` : ''}
            </div>
        </a>
    `}).join('');
}

// ============ 图表绘制 ============
// 存储图表上下文以便交互层使用
let chartCtx = null;

function drawChartBase() {
    const canvas = document.getElementById('priceChart');
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    const data = state.kline;
    if (!data.length) {
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);
        ctx.fillStyle = '#86868b';
        ctx.font = '14px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无K线数据', rect.width / 2, rect.height / 2);
        return null;
    }

    // 高DPI适配
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const W = rect.width;
    const H = rect.height;
    const pad = { top: 20, right: 16, bottom: 40, left: 60 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    // 数据范围
    const allPrices = data.flatMap(d => [d.high, d.low]);
    const minPrice = Math.min(...allPrices);
    const maxPrice = Math.max(...allPrices);
    const priceRange = maxPrice - minPrice || 1;
    const pricePad = priceRange * 0.08;
    const yMin = minPrice - pricePad;
    const yMax = maxPrice + pricePad;

    const toX = (i) => pad.left + (i + 0.5) * (chartW / data.length);
    const toY = (price) => pad.top + chartH - ((price - yMin) / (yMax - yMin)) * chartH;

    ctx.clearRect(0, 0, W, H);

    // 网格线
    const gridLines = 5;
    ctx.strokeStyle = 'rgba(0,0,0,0.04)';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    for (let i = 0; i <= gridLines; i++) {
        const y = pad.top + (chartH / gridLines) * i;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(W - pad.right, y);
        ctx.stroke();

        const priceLabel = yMax - ((yMax - yMin) / gridLines) * i;
        ctx.fillStyle = '#86868b';
        ctx.font = '11px -apple-system, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(priceLabel.toFixed(1), pad.left - 8, y + 4);
    }

    // X轴日期标签（自适应稀疏度）
    ctx.fillStyle = '#86868b';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    const maxLabels = Math.max(4, Math.floor(chartW / 80));
    const labelStep = Math.max(1, Math.floor(data.length / maxLabels));
    for (let i = 0; i < data.length; i += labelStep) {
        const x = toX(i);
        // 根据数据量决定日期显示格式
        let dateStr;
        if (data.length > 500) {
            dateStr = data[i].date.slice(0, 7); // YYYY-MM
        } else if (data.length > 100) {
            dateStr = data[i].date.slice(2, 10); // YY-MM-DD
        } else {
            dateStr = data[i].date.slice(5); // MM-DD
        }
        ctx.fillText(dateStr, x, H - pad.bottom + 18);
    }

    // 面积图 + 折线
    const barW = Math.max(1, Math.min(chartW / data.length * 0.6, 12));

    // 收盘价折线
    ctx.beginPath();
    ctx.strokeStyle = '#0071e3';
    ctx.lineWidth = data.length > 300 ? 1 : 2;
    ctx.lineJoin = 'round';
    ctx.setLineDash([]);
    data.forEach((d, i) => {
        const x = toX(i);
        const y = toY(d.close);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // 面积填充
    const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
    gradient.addColorStop(0, 'rgba(0, 113, 227, 0.15)');
    gradient.addColorStop(1, 'rgba(0, 113, 227, 0.01)');

    ctx.beginPath();
    data.forEach((d, i) => {
        const x = toX(i);
        const y = toY(d.close);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.lineTo(toX(data.length - 1), pad.top + chartH);
    ctx.lineTo(toX(0), pad.top + chartH);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // K线柱体（数据量过大时仅画折线+面积，不画柱体）
    if (data.length <= 300) {
        data.forEach((d, i) => {
            const x = toX(i);
            const isUp = d.close >= d.open;
            ctx.fillStyle = isUp ? 'rgba(52, 199, 89, 0.6)' : 'rgba(255, 59, 48, 0.6)';

            const openY = toY(d.open);
            const closeY = toY(d.close);
            const bodyTop = Math.min(openY, closeY);
            const bodyH = Math.max(Math.abs(openY - closeY), 1);
            ctx.fillRect(x - barW / 2, bodyTop, barW, bodyH);

            ctx.strokeStyle = isUp ? 'rgba(52, 199, 89, 0.6)' : 'rgba(255, 59, 48, 0.6)';
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(x, toY(d.high));
            ctx.lineTo(x, toY(d.low));
            ctx.stroke();
        });
    }

    // 最新价格标注
    if (data.length > 0) {
        const last = data[data.length - 1];
        const lx = toX(data.length - 1);
        const ly = toY(last.close);
        ctx.beginPath();
        ctx.arc(lx, ly, 4, 0, Math.PI * 2);
        ctx.fillStyle = '#0071e3';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
    }

    return { canvas, ctx, data, dpr, W, H, pad, chartW, chartH, toX, toY };
}

function renderChart() {
    chartCtx = drawChartBase();
    if (!chartCtx) return;
    setupChartInteraction();
}

function setupChartInteraction() {
    const { canvas, data, dpr, W, H, pad, chartW, chartH, toX, toY } = chartCtx;
    let tooltip = null;

    canvas.onmousemove = (e) => {
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        if (mx < pad.left || mx > W - pad.right || my < pad.top || my > pad.top + chartH) {
            canvas.style.cursor = 'default';
            if (tooltip) { tooltip.remove(); tooltip = null; }
            // 重绘清除十字线
            chartCtx = drawChartBase();
            return;
        }

        canvas.style.cursor = 'crosshair';
        const idx = Math.round((mx - pad.left) / (chartW / data.length) - 0.5);
        if (idx < 0 || idx >= data.length) return;

        const item = data[idx];
        const x = toX(idx);
        const priceY = toY(item.close);

        // 重绘基础图然后叠加十字线
        const base = drawChartBase();
        if (!base) return;
        const ctx = base.ctx;

        // 十字线
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = 'rgba(0,0,0,0.15)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, pad.top);
        ctx.lineTo(x, pad.top + chartH);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(pad.left, priceY);
        ctx.lineTo(W - pad.right, priceY);
        ctx.stroke();
        ctx.restore();

        // 高亮点
        ctx.beginPath();
        ctx.arc(x, priceY, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#0071e3';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Tooltip
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.className = 'chart-tooltip';
            canvas.parentElement.style.position = 'relative';
            canvas.parentElement.appendChild(tooltip);
        }

        const change = item.close - item.open;
        const changeColor = change >= 0 ? 'var(--green)' : 'var(--red)';
        const periodLabel = { day: '日', week: '周', month: '月' }[state.chartPeriod] || '';
        tooltip.innerHTML = `
            <div style="font-weight:600;margin-bottom:4px">${item.date}${periodLabel ? ' (' + periodLabel + 'K)' : ''}</div>
            <div>开: ${item.open.toFixed(1)}</div>
            <div>收: <span style="color:${changeColor};font-weight:600">${item.close.toFixed(1)}</span></div>
            <div>高: ${item.high.toFixed(1)}</div>
            <div>低: ${item.low.toFixed(1)}</div>
            <div style="color:${changeColor}">涨跌: ${change >= 0 ? '+' : ''}${change.toFixed(2)}</div>
        `;

        const tooltipX = x + 16 > W - 140 ? x - 150 : x + 16;
        tooltip.style.left = tooltipX + 'px';
        tooltip.style.top = (my - 20) + 'px';
    };

    canvas.onmouseleave = () => {
        if (tooltip) { tooltip.remove(); tooltip = null; }
        chartCtx = drawChartBase();
    };
}

// ============ 通用 SSE 流式读取 ============
async function readSSEStream(resp, container, onDone) {
    container.innerHTML = '<div class="analysis-rendered"><span class="typing-cursor"></span></div>';
    const rendered = container.querySelector('.analysis-rendered');
    let fullText = '';

    try {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6).trim();
                    if (dataStr === '[DONE]') continue;
                    try {
                        const parsed = JSON.parse(dataStr);
                        if (parsed.content) {
                            fullText += parsed.content;
                            rendered.innerHTML = renderMarkdown(fullText) +
                                '<span class="typing-cursor"></span>';
                            container.scrollTop = container.scrollHeight;
                        }
                    } catch (e) { /* skip */ }
                }
            }
        }
    } catch (e) {
        console.error('SSE读取失败:', e);
        if (!fullText) {
            container.innerHTML = `
                <div class="analysis-placeholder" style="min-height:80px">
                    <p style="color:var(--red);font-size:14px">请求失败，请检查网络后重试</p>
                </div>
            `;
            if (onDone) onDone(fullText);
            return fullText;
        }
    }

    rendered.innerHTML = renderMarkdown(fullText);
    if (onDone) onDone(fullText);
    return fullText;
}

// ============ AI 新闻摘要 ============
let isSummarizing = false;

async function fetchSummary() {
    if (isSummarizing) return;
    isSummarizing = true;

    const container = document.getElementById('summaryContent');
    const btn = document.getElementById('btnRefreshSummary');
    if (btn) btn.classList.add('loading');

    try {
        const resp = await fetch('/api/summary');
        await readSSEStream(resp, container);
        document.getElementById('summaryTime').textContent =
            '更新于 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        console.error('摘要请求失败:', e);
    }

    isSummarizing = false;
    if (btn) btn.classList.remove('loading');
}

// ============ AI 深度分析 ============
async function generateAnalysis() {
    if (state.isAnalyzing) return;
    state.isAnalyzing = true;

    const btn = document.getElementById('btnAnalysis');
    const container = document.getElementById('analysisContent');
    btn.disabled = true;
    btn.classList.add('loading');
    btn.innerHTML = `
        <div class="loading-spinner" style="width:16px;height:16px;border-width:2px"></div>
        正在分析中...
    `;

    try {
        const resp = await fetch('/api/analysis');
        await readSSEStream(resp, container);
    } catch (e) {
        console.error('分析失败:', e);
    }

    state.isAnalyzing = false;
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        重新分析
    `;
}

// ============ 自定义提问 ============
let isChatting = false;

async function sendChat() {
    const input = document.getElementById('chatInput');
    const prompt = input.value.trim();
    if (!prompt || isChatting) return;

    isChatting = true;
    const btn = document.getElementById('btnChat');
    const container = document.getElementById('analysisContent');
    btn.disabled = true;
    btn.classList.add('loading');
    input.disabled = true;

    try {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt }),
        });
        await readSSEStream(resp, container);
        input.value = '';
    } catch (e) {
        console.error('提问失败:', e);
    }

    isChatting = false;
    btn.disabled = false;
    btn.classList.remove('loading');
    input.disabled = false;
    input.focus();
}

// 回车发送
document.getElementById('chatInput')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
    }
});

// ============ 刷新 ============
async function refreshAll() {
    if (state.isRefreshing) return;
    state.isRefreshing = true;

    const btn = document.querySelector('.btn-refresh');
    btn.classList.add('loading');

    try {
        await fetch('/api/refresh', { method: 'POST' });
        await Promise.all([
            fetchStockData(),
            fetchKlineData(),
            fetchNewsData(),
        ]);
        document.getElementById('updateTime').textContent =
            '更新于 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        // 刷新后重新获取评级
        fetchRating();
    } catch (e) {
        console.error('刷新失败:', e);
    }

    btn.classList.remove('loading');
    state.isRefreshing = false;
}

// ============ 图表Tab切换 ============
// 时间范围切换
document.querySelectorAll('#rangeTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('#rangeTabs .tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        state.chartRange = tab.dataset.range;
        fetchKlineData();
    });
});

// K线周期切换
document.querySelectorAll('#periodTabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('#periodTabs .tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        state.chartPeriod = tab.dataset.period;
        fetchKlineData();
    });
});

// 窗口缩放重绘
let resizeTimer;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => renderChart(), 200);
});

// ============ 初始化 ============
async function init() {
    document.getElementById('updateTime').textContent = '加载中...';

    await Promise.all([
        fetchStockData(),
        fetchKlineData(),
        fetchNewsData(),
    ]);

    document.getElementById('updateTime').textContent =
        '更新于 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

    // 初始化加载新闻摘要和AI评级（不阻塞主流程）
    fetchSummary();
    fetchRating();

    // 自动定时刷新数据 (5分钟)
    setInterval(async () => {
        await Promise.all([
            fetchStockData(),
            fetchKlineData(),
            fetchNewsData(),
        ]);
        document.getElementById('updateTime').textContent =
            '更新于 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }, 5 * 60 * 1000);

    // 每小时自动刷新新闻摘要
    setInterval(() => {
        fetchSummary();
    }, 60 * 60 * 1000);
}

init();
