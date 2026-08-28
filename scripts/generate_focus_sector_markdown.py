from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-08-19"
DETAIL = pd.read_csv(ROOT / "data/processed/market" / f"focus-sector-detail-{DATE}.csv")
SUMMARY = pd.read_csv(ROOT / "data/processed/market" / f"focus-sector-summary-{DATE}.csv")
MARKET_REPORT = ROOT / "reports" / f"market-{DATE}.md"
NEWS_PATH = ROOT / "data/processed/news" / "news_20260819.jsonl"
OUT = ROOT / "reports" / f"focus-sectors-{DATE}.md"


def pct(x):
    return "—" if pd.isna(x) else f"{x:+.2f}%"

def money_yi(x):
    return f"{x / 1e8:.1f}亿元"

def ratio(x):
    return "—" if pd.isna(x) else f"{x:.2f}倍"

def row_for(group):
    return SUMMARY[SUMMARY["group"] == group].iloc[0]

def details(group):
    return DETAIL[DETAIL["group"] == group].sort_values("day_return_pct")

def direction(group):
    r = row_for(group)
    d = r["equal_weight_day_return_pct"]
    r5 = r["equal_weight_return_5d_pct"]
    r20 = r["equal_weight_return_20d_pct"]
    if group == "银行": return "震荡偏强（防御占优）"
    if d <= -6: return "短线偏弱，高波动回撤"
    if d <= -3: return "短线偏弱，等待止跌"
    if r20 > 8 and d < 0: return "中期偏强、短线回撤"
    if r20 < 0: return "中短期偏弱"
    return "震荡"

# Extract selected research/news entries from the already collected news dataset.
news = []
if NEWS_PATH.exists():
    for line in NEWS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            news.append(json.loads(line))
        except json.JSONDecodeError:
            pass

def find_news(words):
    for item in news:
        text = (item.get("title", "") + " " + item.get("summary", ""))
        if all(w in text for w in words):
            return item
    return None

selected = [
    find_news(["A股板块轮动行情持续"]),
    find_news(["碳酸锂库存去化加速"]),
]
selected = [x for x in selected if x]

report_link = f"[{OUT}]({OUT.as_uri()})"
lines = []
lines += [
    f"# A股重点关注板块分析报告（{DATE}）",
    "",
    f"- **报告文件绝对路径/Markdown链接**：{report_link}",
    "",
    "> **数据状态：2026年8月19日收盘后数据；报告生成/数据补采时间：2026年8月20日。**",
    "> 本报告覆盖电子、银行、电力设备、通信、医药生物、非银金融、有色金属七个重点关注方向。报告用于研究跟踪，不构成投资建议。",
    "",
    "## 一、核心结论",
    "",
    "1. **市场整体处于放量下跌和风险偏好收缩状态**：上证指数下跌2.40%，深证成指下跌5.01%，创业板指下跌6.26%；两市成交额约2.51万亿元，涨跌家数约为420:4760。成长与高β方向承压明显。",
    "2. **银行是七个重点方向中唯一上涨板块**：同花顺“银行”行业指数上涨1.46%，成交量约为前5日均量的1.55倍，体现防御性资金切换；但指数仍接近20日高位，追涨性价比需观察。",
    "3. **电子和通信短线最弱**：按组内等权口径，电子下跌7.10%，通信下跌6.48%；两者20日仍有约7.9%—8.1%的正收益，说明更像高位/高β资产集中获利回吐，而不是中期趋势已完全反转。",
    "4. **电力设备和有色金属属于“中期有趋势、短线遇回撤”**：电力设备20日等权上涨9.34%，有色金属上涨12.35%，但8月19日分别下跌5.12%和3.73%，不宜把中期强势直接等同于次日必然反弹。",
    "5. **医药生物相对抗跌，但属于缩量防御**：医药生物下跌2.98%，组内6个细分全部下跌，平均量比约0.86倍；相对成长科技更稳，但暂未形成强势普涨结构。",
    "6. **非银金融中保险相对最强，但整体中期仍弱**：保险当日上涨0.20%，证券下跌1.61%，多元金融下跌2.08%；非银金融组内等权20日收益为-2.58%，需要等待证券放量企稳才能确认修复。",
    "",
    "## 二、七大重点板块总览",
    "",
    "> 说明：除银行外，其余大类是把同花顺行业指数细分板块进行**组内等权近似汇总**，不是交易所或同花顺发布的独立一级行业指数。金额为组内细分指数成交额相加，不能直接理解为对应大类所有股票的完整成交额。",
    "",
    "| 大类 | 细分数量 | 8/19日表现 | 近5日 | 近20日 | 相对MA20 | 量比 | 组内涨跌 | 短线判断 |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---|",
]
for _, r in SUMMARY.sort_values("equal_weight_day_return_pct", ascending=False).iterrows():
    lines.append(f"| {r['group']} | {int(r['constituent_count'])} | {pct(r['equal_weight_day_return_pct'])} | {pct(r['equal_weight_return_5d_pct'])} | {pct(r['equal_weight_return_20d_pct'])} | {pct(r['equal_weight_vs_ma20_pct'])} | {ratio(r['avg_volume_ratio_5d'])} | {int(r['positive_subsectors'])}涨/{int(r['negative_subsectors'])}跌 | {direction(r['group'])} |")

lines += [
    "",
    "### 板块强弱排序（8月19日）",
    "",
    "```text",
    "银行       +1.46%  防御资金占优",
    "非银金融   -1.16%  相对抗跌，但中期仍弱",
    "医药生物   -2.98%  缩量防御",
    "有色金属   -3.73%  中期强势回撤",
    "电力设备   -5.12%  高景气方向集中调整",
    "通信       -6.48%  高β科技承压",
    "电子       -7.10%  七大方向最弱",
    "```",
    "",
    "## 三、各重点板块细分分析",
    "",
]

for group in ["电子", "银行", "电力设备", "通信", "医药生物", "非银金融", "有色金属"]:
    r = row_for(group)
    lines.append(f"### {group}")
    lines.append("")
    lines.append(f"**组内概览**：8月19日组内等权变动 {pct(r['equal_weight_day_return_pct'])}，近5日 {pct(r['equal_weight_return_5d_pct'])}，近20日 {pct(r['equal_weight_return_20d_pct'])}；相对20日均线 {pct(r['equal_weight_vs_ma20_pct'])}，平均量比 {ratio(r['avg_volume_ratio_5d'])}。")
    lines.append("")
    lines.append("| 细分板块 | 当日 | 近5日 | 近20日 | 相对MA20 | 量比 | 成交额 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for _, x in details(group).iterrows():
        lines.append(f"| {x['symbol']} | {pct(x['day_return_pct'])} | {pct(x['return_5d_pct'])} | {pct(x['return_20d_pct'])} | {pct(x['vs_ma20_pct'])} | {ratio(x['volume_ratio_5d'])} | {money_yi(x['amount'])} |")
    lines.append("")
    if group == "电子":
        lines.append("**解读**：电子六个细分全部下跌，电子化学品跌幅最大（-8.32%），元件和半导体分别下跌7.68%和7.44%；半导体成交额约4019.4亿元，是组内最活跃方向。由于近20日多数细分仍为正收益、且收盘仍在MA20上方，当前更像高位快速回撤，但需要观察是否出现缩量止跌和龙头重新站回短期均线。")
    elif group == "银行":
        lines.append("**解读**：银行是唯一上涨方向，成交量明显放大，且收盘在MA20上方；但距离20日高点仍有空间，说明防御切换尚未等于全面突破。后续要看银行上涨能否与证券同步，否则可能只是指数稳定器，而不是增量行情主线。")
    elif group == "电力设备":
        lines.append("**解读**：六个细分全部下跌，电机跌幅最大（-6.84%），其他电源设备跌幅为-5.89%；风电设备量比约1.69倍，调整伴随较明显换手。电池、光伏和电网设备20日仍保持正收益，但电网设备、电机已低于MA20，短线先按回撤和分化处理。")
    elif group == "通信":
        lines.append("**解读**：通信设备下跌7.31%，明显弱于通信服务的-5.65%；两者量比均低于1，说明当日主要表现为缩量下跌而非极端放量杀跌。中期收益仍为正，但若后续不能快速收复8月19日实体区间，光通信/算力等高β细分仍有继续震荡风险。")
    elif group == "医药生物":
        lines.append("**解读**：医药生物六个细分全部下跌，但跌幅显著小于电子、通信和电力设备；医疗服务跌幅最大（-4.93%），中药相对抗跌（-1.50%）。整体平均量比约0.86倍，偏向缩量防御，暂时缺少放量普涨确认。")
    elif group == "非银金融":
        lines.append("**解读**：保险当日微涨0.20%，证券和多元金融分别下跌1.61%和2.08%；但保险近20日下跌7.42%，说明当日相对强不代表中期趋势已经修复。证券仍低于MA20约2.97%，需要成交额放大并重新站回MA20，才有更强的右侧信号。")
    elif group == "有色金属":
        lines.append("**解读**：贵金属相对最强，当日仅跌1.06%，近20日上涨17.79%，且高于MA20约7.13%；小金属和金属新材料跌幅较大。能源金属近5日下跌6.01%，但近20日仍上涨13.36%。有色板块的核心矛盾是中期商品/资源逻辑与短期获利回吐并存，不能只看单日反弹。")
    lines.append("")

lines += [
    "## 四、资金面、量价与风格判断",
    "",
    "### 1. 风险偏好",
    "",
    "8月19日上证相对抗跌，但创业板指、中证500和中证1000跌幅更大，市场呈现明显的**大盘价值相对占优、成长小盘承压**。七大重点方向中，银行上涨而电子、通信、电力设备大幅回撤，进一步印证资金在高β资产下跌时向低波动和权重方向寻找承接。",
    "",
    "### 2. 量价关系",
    "",
    "- 电子、电力设备和有色金属的组内量比大致在1.02—1.17倍，属于有换手的回撤；应观察后续是缩量止跌还是继续放量破位。",
    "- 通信量比约0.92倍、医药生物约0.86倍，偏缩量下跌；这对短线止跌有利，但不能单独证明反转。",
    "- 银行量比约1.55倍且上涨，防御切换信号最清晰；若后续量能快速萎缩并跌回MA20，需防止冲高回落。",
    "",
    "### 3. 资金来源限制",
    "",
    "本报告没有把同花顺行业指数成交额直接等同于“主力净流入”。当前已采集的8月19日大盘报告显示，上证指数资金流接口原始主力净流入字段约为-815.9亿元，但该字段不能外推为全市场精确主力资金总额；北向资金和融资余额没有形成同口径、可审计的8月19日净变化，因此不做确定性结论。",
    "",
    "## 五、消息与产业线索",
    "",
]
if selected:
    for item in selected:
        title = item.get("title", "").replace("\n", " ")
        url = item.get("url") or item.get("canonical_url")
        summary = (item.get("summary") or "").replace("\n", " ")[:420]
        lines.append(f"- [{title}]({url})：{summary}")
else:
    lines.append("- 本地新闻标准化文件未找到可用条目，消息面不纳入确定性结论。")
lines += [
    "",
    "**消息面归纳**：已采集新闻强调当前市场板块轮动较快、核心主线尚未形成，科技硬件方向可关注光互联、电源/液冷、AI硬件和CXO；有色方向的锂供需、库存去化和下游旺季预期仍是中期观察变量。以上属于搜索/网页研究信息，不替代交易所公告、公司披露或授权行情。",
    "",
    "## 六、下一交易日观察计划（基于8月19日收盘截面）",
    "",
    "| 板块 | 观察方向 | 条件触发 | 失效/风险条件 | 模型判断* |",
    "|---|---|---|---|---:|",
    "| 银行 | 防御强度能否延续 | 维持MA20上方且成交量不快速萎缩；最好证券同步转强 | 高开低走、跌回MA20 | 震荡偏强 60% |",
    "| 非银金融 | 证券是否止跌 | 证券指数放量收复MA20 | 证券继续低于MA20且保险独强 | 震荡 50% |",
    "| 医药生物 | 防御轮动承接 | 中药/化学制药先止跌，量能温和回升 | 医疗服务继续放量下跌 | 震荡偏强 52% |",
    "| 有色金属 | 中期趋势与短线回撤的平衡 | 贵金属守住MA20上方，能源金属不再创新低 | 小金属/能源金属放量破位 | 震荡偏弱 55% |",
    "| 电力设备 | 高景气方向是否止跌 | 电池、光伏、电网设备至少两项缩量止跌 | 电机/风电继续放量破位 | 震荡偏弱 56% |",
    "| 通信 | 高β科技承压是否缓解 | 通信设备重新站回短期均线且量价齐升 | 通信设备继续弱于通信服务 | 偏弱 60% |",
    "| 电子 | 回撤后是否形成承接 | 半导体/元件缩量止跌，电子指数不再刷新低点 | 电子化学品、半导体继续放量下杀 | 偏弱 62% |",
    "",
    "* 概率是基于8月19日收盘数据、近5/20日收益、MA20位置和量比的研究型估计，不是收益承诺，也不是对8月20日实际收盘结果的回填。",
    "",
    "## 七、重点观察清单",
    "",
    "### 偏防御/相对强势",
    "- 银行：关注指数能否稳在MA20上方，避免只因单日上涨追高。",
    "- 医药生物：优先观察中药、化学制药的止跌质量，等待量能回升。",
    "- 贵金属：中期强度最突出，但短线距离MA20较远，控制追涨风险。",
    "",
    "### 高弹性/等待确认",
    "- 电子：半导体、元件、电子化学品是决定板块是否止跌的核心观察项。",
    "- 通信：通信设备强弱决定光通信/算力链的短线风险偏好。",
    "- 电力设备：电池、光伏、电网设备需至少两个细分同步修复，才算板块性机会。",
    "- 有色能源金属：看锂价、库存、排产和供给扰动是否能从产业逻辑传导到股票成交。",
    "",
    "## 八、数据来源、口径与限制",
    "",
    f"- 大盘指数与市场广度：{MARKET_REPORT}；数据截止2026年8月19日收盘。",
    f"- 重点行业指数：同花顺行业指数历史数据，通过AKShare接口采集；原始数据：{ROOT / 'data/raw/market' / f'focus-sectors-{DATE}.json'}。",
    f"- 处理后汇总：{ROOT / 'data/processed/market' / f'focus-sector-summary-{DATE}.csv'}；明细：{ROOT / 'data/processed/market' / f'focus-sector-detail-{DATE}.csv'}。",
    f"- 新闻：{NEWS_PATH}，由Tavily搜索结果标准化而来；检索数据不是实时行情。",
    "- Tushare Pro已尝试作为优先源，但当前Token无SW行业日线接口权限，因此本次行业指数降级使用同花顺/AKShare研究型接口；该降级已写入原始数据元数据。",
    "- 指数成交量、成交额和行业指数点位属于数据供应商口径，可能与交易所最终披露存在小幅差异；异常或缺失数据不应直接用于交易执行。",
    "",
    "## 风险提示",
    "",
    "板块分析容易受到指数权重、行业分类、涨跌停制度、公告披露和盘中情绪影响。报告中的“偏强/偏弱”和概率是条件判断；若出现重大政策、公司公告、海外市场剧烈波动或流动性变化，以上判断可能失效。",
]
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"报告已生成: {OUT}")
print(f"Markdown绝对路径链接: {report_link}")
