#!/usr/bin/env python3
"""Report Audit Tool for AI Berkshire.

数据抽检工具：从研究报告中抽取15%的财务数据点，与可靠信源比对，
通过则准出，不通过则打回并说明原因。

Zero external dependencies — uses only Python stdlib.
Requires Python >= 3.9.

工作流程（三步）：
  Step 1 — 提取数据点，随机抽样15%：
    python3 tools/report_audit.py extract --report reports/xxx.md

  Step 2 — Claude 对抽检清单中的每个数据点，从可靠信源（macrotrends/
            stockanalysis/aastocks/eastmoney）取数，填入 fetched_value

  Step 3 — 输入核验结果，输出准出/打回判决：
    python3 tools/report_audit.py verdict --results '[...]'

  一步完成（仅提取+打印抽检清单，不做网络验证）：
    python3 tools/report_audit.py extract --report reports/xxx.md --dry-run
"""

import argparse
import contextlib
import json
import math
import os
import re
import sys
from decimal import Decimal, Context, ROUND_HALF_EVEN
from random import Random
if __package__:
    from .financial_rigor import exact
else:
    from financial_rigor import exact

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)

# ---------------------------------------------------------------------------
# 数据点提取：从 Markdown 报告中识别财务数字
# ---------------------------------------------------------------------------

# 匹配模式：数字 + 单位，前面有上下文标签
# 例：收入：1,239亿元、PE 18.8x、毛利率 56%、市值 ~$5,670亿
#
# 注意：所有数字捕获组必须带上可选符号位 _SIGN，否则 "-1.72%" 会被抓成 "1.72"，
# 导致核验时报告值与信源值符号相反、偏差 200%，产生假打回。
# 符号位涵盖 ASCII 正负号、Unicode 减号(U+2212)、en-dash(U+2013)、全角正负号。
_SIGN = r'[+\-−–－＋]?'

_PATTERNS = [
    # 百分比
    (r'(' + _SIGN + r'[\d,，\.]+)\s*%',                        '%',    'percent'),
    # 亿元/亿美元/亿港元
    (r'(' + _SIGN + r'[\d,，\.]+)\s*亿(元|美元|港元|RMB|USD|HKD)?', '亿',    'hundred_million'),
    # 倍数 PE/PB/PS
    (r'(' + _SIGN + r'[\d,，\.]+)\s*[xX倍]',                   'x',    'multiple'),
    # 万亿
    (r'(' + _SIGN + r'[\d,，\.]+)\s*万亿',                      '万亿', 'trillion'),
    # 美元绝对值（B/T）
    (r'\$\s*(' + _SIGN + r'[\d,，\.]+)\s*([BMT亿])',             '$',    'usd_abs'),
    # 纯整数（如市值、收入、用户数等，出现在表格 | 里）
    (r'\|\s*[~约]?\$?(' + _SIGN + r'[\d,，\.]+)\s*\|',          '',     'table_num'),
]

_LABEL_RE = re.compile(
    r'(?P<label>[^\|\n：:]{2,25})[：:\s]+[~约]?\$?(?P<num>' + _SIGN + r'[\d,，\.]+)'
    r'\s*(?P<unit>亿[元美港]?元?|万亿|[xX倍]|%|[BMT])?'
)

_TABLE_ROW_RE = re.compile(
    r'\|\s*(?P<label>[^|]{1,40})\s*\|\s*[~约]?\$?(?P<num>' + _SIGN + r'[\d,，\.]+)'
    r'\s*(?P<unit>亿[元美港]?元?|万亿|[xX倍]|%|[BMT])?\s*\|'
)


def _clean_num(s: str) -> float:
    """把带逗号、中文逗号、各类正负号的数字字符串转为 float。

    支持 ASCII '-'/'+'、Unicode 减号 '−'(U+2212)、en-dash '–'(U+2013)、
    全角 '－'(U+FF0D)/'＋'(U+FF0B)——报告中这些符号都可能被用作正负号。
    """
    s = s.replace(',', '').replace('，', '').strip()
    # 归一化各类符号为 ASCII
    for ch in ('−', '–', '－'):
        s = s.replace(ch, '-')
    s = s.replace('＋', '+')
    try:
        return float(s)
    except ValueError:
        return None


def _is_valid_label(label: str) -> bool:
    """判断标签是否是有意义的财务字段名，过滤噪声。"""
    label = label.strip()
    # 太短
    if len(label) < 2:
        return False
    # 纯数字或纯年份
    if re.fullmatch(r'[\d\s年季度Q]+', label):
        return False
    # 以符号/markdown标记开头
    if re.match(r'^[+\-\*#\|~\$>_`]', label):
        return False
    # 含有 markdown 粗体/代码标记
    if '**' in label or '`' in label or '__' in label:
        return False
    # 标签含有纯增速符号（如 +56%、-13% 单独作标签）
    if re.fullmatch(r'[+\-]?\d+(\.\d+)?%', label):
        return False
    # 常见无意义标签
    _SKIP = {'来源', 'sources', 'source', '说明', '注意', '备注', '数据来源',
             'n/a', '—', '-', '/', '合计', 'total', '单位', '趋势'}
    if label.lower() in _SKIP:
        return False
    return True


# 两列表格行：| 标签 | 数值 unit |（专为财务报告的 KV 表设计）
_KV_TABLE_RE = re.compile(
    r'^\|\s*(?P<label>[^|*\n]{2,40}?)\s*\|\s*[~约]?\$?(?P<num>' + _SIGN + r'[\d,，\.]+)\s*'
    r'(?P<unit>亿[元美港]?元?|万亿|[xX倍]|%|[BMT亿])?\s*[\|（\(]'
)

# 带标签的 KV 行：标签：数值 单位
_KV_LABEL_RE = re.compile(
    r'(?P<label>[\u4e00-\u9fa5A-Za-z][^\|\n：:*]{1,30})[：:]\s*[~约]?\$?'
    r'(?P<num>' + _SIGN + r'[\d,，\.]+)\s*(?P<unit>亿[元美港]?元?|万亿|[xX倍]|%|[BMT])?'
)


def _parse_md_tables(lines: list) -> list:
    """解析 Markdown 中所有表格，返回 (row_label, col_header, value, unit, lineno, raw) 列表。"""
    results = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 检测表头行（含 | 且不是分隔行）
        if '|' in line and not re.match(r'^\|[\-\s\|:]+\|$', line):
            headers_raw = [h.strip().strip('*_').strip() for h in line.split('|')]
            headers_raw = [h for h in headers_raw if h]
            # 下一行应是分隔行
            if i + 1 < len(lines) and re.match(r'^\|[\-\s\|:]+\|$', lines[i+1].strip()):
                i += 2  # 跳过分隔行
                # 读数据行
                while i < len(lines):
                    dline = lines[i].strip()
                    if not dline or not dline.startswith('|'):
                        break
                    cells = [c.strip().strip('*_~').strip() for c in dline.split('|')]
                    cells = [c for c in cells if c != '']
                    if len(cells) < 2:
                        i += 1
                        continue
                    row_label = cells[0]
                    for col_idx, cell in enumerate(cells[1:], start=1):
                        col_header = headers_raw[col_idx] if col_idx < len(headers_raw) else f'列{col_idx}'
                        # 提取 cell 中的数字+单位
                        m = re.search(
                            r'[~约]?\$?(' + _SIGN + r'[\d,，\.]+)\s*'
                            r'(亿[元美港]?元?|万亿|[xX倍]|%|[BMT])?',
                            cell
                        )
                        if m:
                            val = _clean_num(m.group(1))
                            unit = (m.group(2) or '').strip()
                            if val is not None and abs(val) < 1e15:
                                results.append((row_label, col_header, val, unit, i + 1, dline))
                    i += 1
                continue
        i += 1
    return results


def extract_data_points(md_text: str) -> list:
    """从 Markdown 报告中提取所有可识别的财务数据点。

    覆盖三类结构：
      1. 多列 Markdown 表格（最主要的来源）：(行标签 + 列标题) → 数值
      2. 带冒号的 KV 行：标签：数值 单位
      3. 加粗数字行：**数值** 单位

    返回 list of dict：
      {id, label, reported_value, unit, raw_text, line_number}
    """
    points = []
    seen = set()

    def _add(label, val, unit, lineno, raw):
        label = re.sub(r'[\*_`]+', '', label).strip()
        if not _is_valid_label(label):
            return
        if val is None or abs(val) > 1e15:
            return
        # 过滤纯年份/季度
        if re.fullmatch(r'(20\d{2}|Q[1-4]|\d{4}\s*Q[1-4])', label.strip()):
            return
        key = f"{label}|{round(val,4)}|{unit}"
        if key in seen:
            return
        seen.add(key)
        points.append({
            'id': len(points) + 1,
            'label': label,
            'reported_value': val,
            'unit': unit,
            'raw_text': raw[:120],
            'line_number': lineno,
        })

    lines = md_text.split('\n')
    in_code = False

    # --- 1. 多列表格 ---
    for row_label, col_header, val, unit, lineno, raw in _parse_md_tables(lines):
        # 跳过无意义行标签
        if not _is_valid_label(row_label):
            continue
        # 跳过无意义列标题（YoY增速列单独标注，不作为待核验数据）
        if col_header.upper() in ('YOY', 'YOY增速', '增速', '同比', '变化', '趋势', '说明', '备注'):
            continue
        # label = "行标签 · 列标题"（若列标题是行标签的补充）
        if col_header and col_header != row_label:
            label = f"{row_label} · {col_header}"
        else:
            label = row_label
        _add(label, val, unit, lineno, raw)

    # --- 2. KV 冒号行 ---
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            continue
        if in_code or stripped.startswith('> ') or re.match(r'^#{1,6}\s', stripped):
            continue
        if '|' in stripped:
            continue  # 表格已在上面处理

        for m in _KV_LABEL_RE.finditer(stripped):
            label = m.group('label')
            val = _clean_num(m.group('num'))
            unit = (m.group('unit') or '').strip()
            _add(label, val, unit, lineno, stripped)

    return points


def sample_points(points: list, ratio: float = 0.15, seed: int = None) -> list:
    """随机抽取 ratio 比例的数据点，最少 3 个，最多 30 个。"""
    n = max(3, min(30, math.ceil(len(points) * ratio)))
    n = min(n, len(points))
    rng = Random(seed)
    sampled = rng.sample(points, n)
    # 按行号排序，方便人工比对
    return sorted(sampled, key=lambda p: p['line_number'])


# ---------------------------------------------------------------------------
# 准出/打回判决
# ---------------------------------------------------------------------------

_TOLERANCE = Decimal("0.01")  # 1%; values must already share currency/period/basis.


def _pct_diff(reported, fetched):
    """Absolute relative deviation, including a defined zero-reference policy."""
    reported, fetched = exact(reported), exact(fetched)
    if reported == 0:
        return Decimal(0) if fetched == 0 else Decimal("Infinity")
    return _CTX.divide(abs(reported - fetched), abs(reported))


def render_verdict(results: list, report_name: str = "") -> dict:
    """Require every supplied item to have two distinct named, matching sources.

    PASS means numeric checks on the supplied sample passed, not that sources
    were fetched, independently authenticated, or the entire report audited.
    FAIL takes precedence over INCOMPLETE when a known mismatch exists.
    """
    if not isinstance(results, list):
        raise ValueError("核验结果必须是 JSON 数组")
    print("报告数据抽检 — " + (report_name or "未命名报告"))
    fail_items, incomplete_items = [], []
    pass_count = 0
    seen_ids = set()
    for index, item in enumerate(results, 1):
        if not isinstance(item, dict):
            incomplete_items.append({"id": index, "reason": "数据项必须是对象"})
            continue
        label = str(item.get("label", "?"))
        item_id = item.get("id")
        missing, mismatches = [], []
        if type(item_id) is not int or item_id in seen_ids:
            missing.append("缺少有效且唯一的整数 id")
        else:
            seen_ids.add(item_id)
        try:
            reported = exact(item.get("reported_value"))
        except ValueError:
            reported = None
            missing.append("报告值缺失或非有限数值")
        names = []
        for suffix in ("", "2"):
            name = item.get("fetched_source" + suffix, "")
            name = name.strip() if isinstance(name, str) else ""
            if not name:
                missing.append("缺少来源名称" + suffix)
            names.append(name.casefold())
            try:
                fetched = exact(item.get("fetched_value" + suffix))
            except ValueError:
                missing.append("核验值缺失或非有限数值" + suffix)
                continue
            if reported is not None:
                diff = _pct_diff(reported, fetched)
                if diff > _TOLERANCE:
                    mismatches.append(f"{name or '未命名来源'}: 偏差 {diff * 100:.2f}% > 1%")
        if names[0] and names[0] == names[1]:
            missing.append("两来源名称重复，不能视为独立核验")
        detail = {"id": item_id, "label": label,
                  "line_number": item.get("line_number", 0),
                  "reason": "; ".join(mismatches + missing)}
        if mismatches:
            fail_items.append(detail)
            print(f"❌ {label}: {detail['reason']}")
        elif missing:
            incomplete_items.append(detail)
            print(f"⬜ {label}: {detail['reason']}")
        else:
            pass_count += 1
            print(f"✅ {label}: 两个具名来源数值均在 1% 容差内")

    verdict = ("FAIL" if fail_items else
               "INCOMPLETE" if not results or incomplete_items else "PASS")
    messages = {
        "PASS": "【数值核验通过】所提供样本全部通过；来源真实性、独立性、口径与全文仍需复核。",
        "FAIL": "【打回】存在数值不一致，修正并重新核验前不得发布。",
        "INCOMPLETE": "【核验不完整】样本为空或证据不足，不得按已核验报告发布。",
    }
    print(messages[verdict])
    return {"verdict": verdict, "pass_count": pass_count, "warn_count": 0,
            "fail_count": len(fail_items), "incomplete_count": len(incomplete_items),
            "total": len(results), "fail_items": fail_items, "warn_items": [],
            "incomplete_items": incomplete_items}


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def _force_utf8_stdio():
    """把 stdout/stderr 强制切到 UTF-8。

    Windows 控制台默认 GBK，报告里的 €、→、★ 等字符会让 print(json.dumps(...))
    抛 UnicodeEncodeError 直接崩溃。errors='replace' 保证极端字符也不中断流程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass  # 非 TextIOWrapper（如被重定向到管道对象）时忽略


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description='Report Audit Tool — 研究报告数据抽检工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
工作流程：

  Step 1 — 提取数据点并随机抽样 15%，输出抽检清单：
    python3 tools/report_audit.py extract --report reports/腾讯/腾讯-research-20260408.md

  Step 2 — Claude 对清单中每个数据点，从可靠信源取数，
            填入 fetched_value / fetched_source / fetched_value2 / fetched_source2

  Step 3 — 输入核验结果，输出准出/打回判决：
    python3 tools/report_audit.py verdict --results '[
      {"id":1,"label":"营业收入","reported_value":7518,"unit":"亿","fetched_value":7518,"fetched_source":"macrotrends","fetched_value2":7500,"fetched_source2":"stockanalysis"},
      ...
    ]'

  一步预览（只打印抽检清单，不核验）：
    python3 tools/report_audit.py extract --report reports/xxx.md --dry-run

  指定抽样比例（默认0.15）：
    python3 tools/report_audit.py extract --report reports/xxx.md --ratio 0.20

  固定随机种子（复现同一批样本）：
    python3 tools/report_audit.py extract --report reports/xxx.md --seed 42
        """)

    sub = parser.add_subparsers(dest='command')

    # extract
    ext = sub.add_parser('extract', help='从报告提取数据点并随机抽样')
    ext.add_argument('--report', required=True, help='报告文件路径（Markdown）')
    ext.add_argument('--ratio', type=float, default=0.15, help='抽样比例，默认 0.15')
    ext.add_argument('--seed', type=int, default=None, help='随机种子（可选，用于复现）')
    ext.add_argument('--dry-run', action='store_true', help='只打印，不输出 JSON')

    # verdict
    vrd = sub.add_parser('verdict', help='根据核验结果输出准出/打回判决')
    vrd.add_argument('--results', required=True, help='JSON 数组，含 fetched_value 等字段')
    vrd.add_argument('--report', default='', help='报告名称（可选，用于显示）')
    vrd.add_argument('--output-json', action='store_true', help='将判决结果以 JSON 输出到 stdout')

    args = parser.parse_args()

    if args.command == 'extract':
        if not os.path.exists(args.report):
            print(f'❌ 文件不存在: {args.report}', file=sys.stderr)
            sys.exit(1)

        with open(args.report, 'r', encoding='utf-8') as f:
            text = f.read()

        all_points = extract_data_points(text)
        sampled = sample_points(all_points, ratio=args.ratio, seed=args.seed)

        print('=' * 70)
        print(f'报告数据抽检清单')
        print(f'文件：{args.report}')
        print(f'总提取数据点：{len(all_points)}  |  抽样比例：{args.ratio:.0%}  |  抽检数量：{len(sampled)}')
        if args.seed is not None:
            print(f'随机种子：{args.seed}（可用于复现同一批样本）')
        print('=' * 70)
        print()
        print(f'{"ID":>3}  {"行号":>5}  {"数据标签":<35}  {"报告值":>12}  {"单位"}')
        print(f'{"─"*3}  {"─"*5}  {"─"*35}  {"─"*12}  {"─"*6}')
        for p in sampled:
            print(f'{p["id"]:>3}  {p["line_number"]:>5}  {p["label"][:35]:<35}  {p["reported_value"]:>12.2f}  {p["unit"]}')
        print()
        print('↑ 请对上述每个数据点，从以下信源取数，填入 fetched_value：')
        print('  美股：macrotrends.net（主）+ stockanalysis.com（副）')
        print('  港股：aastocks.com（主）+ macrotrends ADR（副）')
        print('  A股： eastmoney.com（主）+ cninfo.com.cn（副）')
        print()

        if not args.dry_run:
            # 输出可填写的 JSON 模板
            template = []
            for p in sampled:
                template.append({
                    'id': p['id'],
                    'label': p['label'],
                    'reported_value': p['reported_value'],
                    'unit': p['unit'],
                    'line_number': p['line_number'],
                    'raw_text': p['raw_text'],
                    'fetched_value': None,       # ← 填入主来源核验值
                    'fetched_source': '',        # ← 填入主来源名称
                    'fetched_value2': None,      # ← 填入副来源核验值（必填）
                    'fetched_source2': '',       # ← 填入副来源名称（必填）
                })
            print('抽检清单 JSON（填入 fetched_value 后，传给 verdict 命令）：')
            print()
            print(json.dumps(template, ensure_ascii=False, indent=2))

    elif args.command == 'verdict':
        try:
            results = json.loads(args.results, parse_float=Decimal)
            if not isinstance(results, list):
                raise ValueError("核验结果必须是 JSON 数组")
        except ValueError as e:
            print(f'❌ JSON 解析失败: {e}', file=sys.stderr)
            sys.exit(1)

        report_name = args.report or ''
        with contextlib.redirect_stdout(sys.stderr if args.output_json else sys.stdout):
            outcome = render_verdict(results, report_name=report_name)

        if args.output_json:
            print(json.dumps(outcome, ensure_ascii=False, indent=2))

        # 非零退出码表示打回，方便 CI/脚本判断
        sys.exit(0 if outcome['verdict'] == 'PASS' else 1)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
