#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把推送正文渲染成「推送存档图」PNG（方糖模板视觉，方案A）。

输入二选一：
  1) 环境变量 PAYLOAD = JSON 字符串（GitHub Actions repository_dispatch 传入）
  2) 命令行第一个参数 = payload.json 路径（本地验证用）

payload: { "title", "body", "date", "ts", "source" }
输出：push_card_latest.png（CARD_OUT 可覆盖）
依赖：playwright + chromium
"""
import html
import json
import os
import re
import sys

WIDTH = 440

SEG_PREFIX = '@@SEG:'
CAT_MARK = '@@CAT@@'
ETF_CATS = ('上证50', '沪深300', '中证500', '科创50', '创业板',
            '中证1000', '国证2000', '北证50')


def esc(s):
    return html.escape(str(s), quote=False)


def bold_money(text, section):
    """按方糖口径给关键数值加粗（金山 body 原文无 **）。方糖原文已有 ** 则跳过重复加粗。"""
    if '**' in text:
        return text
    if section == 'margin':
        text = re.sub(r'(维保：)(-?\d[\d.]*)', r'\1**\2**', text, count=1)
        text = re.sub(r'(成交占比：)(-?\d[\d.]*%)', r'\1**\2**', text, count=1)
        text = re.sub(r'(余额比：)(-?\d[\d.]*%)', r'\1**\2**', text, count=1)
    elif section == 'emotion':
        text = re.sub(r'(短线情绪值：)(-?\d[\d.]*)', r'\1**\2**', text, count=1)
        text = re.sub(r'(轮动速度 )(-?\d[\d.]*)', r'\1**\2**', text, count=1)
        text = re.sub(r'(短线仓位建议 )(-?\d[\d.]*)(%)', r'\1**\2**\3', text, count=1)
        text = re.sub(r'(中长线仓位 )(-?\d[\d.]*)(%)', r'\1**\2**\3', text, count=1)
    elif section == 'etf':
        for kw in ('昨', '前', '中位数'):
            text = re.sub(r'(%s)(-?\d[\d.]*)' % kw, r'\1**\2**', text, count=1)
    return text


def md_inline(s):
    out, i = [], 0
    for m in re.finditer(r'\*\*(.+?)\*\*', s):
        out.append(esc(s[i:m.start()]))
        out.append('<strong>' + esc(m.group(1)) + '</strong>')
        i = m.end()
    out.append(esc(s[i:]))
    return ''.join(out)


# ---------- 解析：统一成方糖分段 ----------
def _match_seg(line):
    """返回 (段名, 是否丢弃该行)。"""
    if line.startswith('## 📊 数据同步') or line.startswith('📊 全局数据'):
        return 'header', True
    if line.startswith('### 🏦 席位') or line.startswith('🏦 席位净持仓'):
        return 'seat', True
    if line.startswith('### 📈 两融') or line.startswith('📈 两融'):
        return 'margin', True
    if line.startswith('### 🎭 市场情绪') or line.startswith('🎭 市场情绪'):
        return 'emotion', True
    if line.startswith('### 📋 Choice') or line.startswith('📋 Choice'):
        return 'choice', True
    if line.startswith('### 📊 沽购比') or line.startswith('📊 沽购比'):
        return 'gouguo', True
    if line.startswith('## 💰 宽基ETF申赎资金') or line.startswith('📈 宽基ETF净申购'):
        return 'etf', True
    if line.startswith('### 📊 合计净申购'):
        return 'etf', False  # 保留标记行供重建
    if line.startswith('### 📈 各宽基ETF净申购'):
        return 'etf', False
    if line.startswith('### 📁 文件导出') or line.startswith('📌 本次更新'):
        return 'note', True
    return None, False


def parse_sections(body):
    secs, cur = {}, None
    for raw in body.split('\n'):
        line = raw.rstrip()
        seg, drop = _match_seg(line)
        if seg:
            cur = seg
            secs.setdefault(cur, [])
            if line.startswith('### 📊 合计净申购'):
                secs[cur].append(SEG_PREFIX + 'total')
            elif line.startswith('### 📈 各宽基ETF净申购'):
                secs[cur].append(CAT_MARK)
            elif not drop:
                secs[cur].append(line)
            continue
        if line.startswith('（数据来源：') or line.startswith('来源：') or line.strip() == '---':
            continue
        if cur:
            secs.setdefault(cur, []).append(line)
    return secs


def line_html(raw, section, etf_phase=None):
    s = raw.strip()
    if not s or s.startswith(CAT_MARK):
        return ''
    if s.startswith(SEG_PREFIX):
        return ''
    if s.startswith('融资：') or s.startswith('融券：'):
        return ''  # 方糖两融段无融资/融券行
    if s.startswith('- '):
        s = s[2:]
    is_total = s.startswith('合计：')
    if is_total:
        s = s[3:]
    is_cat = s.startswith(ETF_CATS) and '：' in s[:8]
    s = bold_money(s, section)
    inner = md_inline(s)
    if section == 'etf' and is_total:
        return '<p class="etf-total">' + inner + '</p>'
    if section == 'etf' and is_cat:
        return '<p class="etf-cat">' + inner + '</p>'
    if raw.startswith('  ') or re.match(r'^（\d+个交易日', s) or s.startswith('⏱'):
        return '<p class="sub">' + inner + '</p>'
    if s.startswith('📊 ') and '个交易日' in s:
        return '<p class="sub">' + inner + '</p>'
    return '<p class="line">' + inner + '</p>'


def build_html(payload):
    body = payload.get('body') or ''
    date = payload.get('date') or ''
    ts = payload.get('ts') or ''
    secs = parse_sections(body)
    if not ts:
        for h in secs.get('header') or []:
            if h.startswith('⏱'):
                ts = h[1:].strip()
                break

    blocks = ['<h1>📊 数据同步</h1>', '<p class="ts">' + esc(ts or date) + '</p>']

    order = ['seat', 'margin', 'emotion', 'choice', 'gouguo', 'etf']
    for key in order:
        lines = secs.get(key) or []
        if not lines:
            continue
        names = {'seat': '🏦 席位', 'margin': '📈 两融', 'emotion': '🎭 市场情绪',
                 'choice': '📋 Choice', 'gouguo': '📊 沽购比', 'etf': '💰 宽基ETF申赎资金'}
        d = ''
        for l in lines[:2]:
            m = re.search(r'（(\d{4}-\d{2}-\d{2})）', l)
            if m:
                d = m.group(1)
                break
        title = names[key] if key == 'etf' else names[key] + ('（' + d + '）' if d else '')
        blocks.append('<h2 class="' + ('sec-big' if key == 'etf' else 'sec') + '">' + esc(title) + '</h2>')

        if key == 'etf':
            blocks.append('<h3>📊 合计净申购' + ('（' + d + '）' if d else '') + '</h3>')
            cat_opened = False
            for l in lines:
                sraw = l.strip()
                if sraw == CAT_MARK or sraw.startswith(SEG_PREFIX):
                    continue
                if sraw.startswith('⏱'):
                    ts = sraw[1:].strip()  # ETF 段内的「更新时间」做主时间戳
                    continue
                s2 = sraw.lstrip('- ')
                is_cat = s2.startswith(ETF_CATS) and '：' in s2[:8]
                if is_cat and not cat_opened:
                    blocks.append('<h3>📈 各宽基ETF净申购（亿）</h3>')
                    cat_opened = True
                h = line_html(l, key)
                if h:
                    blocks.append(h)
        else:
            for l in lines:
                h = line_html(l, key)
                if h:
                    blocks.append(h)

    blocks.append('<p class="hr"></p>')
    blocks.append('<p class="src">来源：上交所/深交所 ETF份额数据 → 金山文档</p>')
    blocks.append('<p class="src">来源：东方财富API / Choice EDB → 金山文档</p>')
    content = '\n'.join(blocks)

    return '''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
* { box-sizing: border-box; }
body { margin:0; padding:16px; width:%dpx; background:#f6f7f9;
  font-family:-apple-system,"Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  color:#1a1d24; -webkit-font-smoothing:antialiased; }
.card { background:#fff; border-radius:14px; padding:18px 18px 20px;
  box-shadow:0 2px 10px rgba(0,0,0,.06); }
h1 { font-size:19px; margin:0 0 6px; font-weight:700; letter-spacing:.3px; }
.ts { font-size:11px; color:#98a0ad; margin:0 0 16px; }
h2 { font-size:14px; margin:16px 0 8px; font-weight:700; color:#2b5fa8;
  border-left:3px solid #6ea8fe; padding-left:8px; }
h2.sec-big { color:#b5651d; border-left-color:#f0a868; }
h3 { font-size:12px; margin:12px 0 6px; font-weight:700; color:#3c414b; }
p { margin:3px 0; font-size:12px; line-height:1.55; }
p.sub { font-size:11px; color:#7b8493; padding-left:6px; }
p.etf-total { font-weight:600; }
p.src { font-size:10px; color:#9aa2ae; margin-top:8px; }
p.hr { border-top:1px solid #eef0f3; margin:14px 0 8px; }
strong { font-weight:700; color:#10131a; }
</style></head><body><div class="card">%s</div></body></html>''' % (WIDTH, content)


def main():
    if os.environ.get('PAYLOAD'):
        payload = json.loads(os.environ['PAYLOAD'])
    elif len(sys.argv) > 1:
        with open(sys.argv[1], encoding='utf-8') as f:
            payload = json.load(f)
    else:
        raise SystemExit('需要 PAYLOAD 环境变量或 payload.json 路径')
    out = os.environ.get('CARD_OUT') or 'push_card_latest.png'
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': WIDTH, 'height': 900}, device_scale_factor=2)
        page.set_content(build_html(payload), wait_until='load')
        page.screenshot(path=out, full_page=True)
        browser.close()
    print('[存档图] 已生成 %s' % out)


if __name__ == '__main__':
    main()
