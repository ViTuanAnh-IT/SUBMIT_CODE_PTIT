#!/usr/bin/env python3
"""
DLab (PTIT) auto-submit tool - chế độ TỰ ĐỘNG.

Cài đặt:
    pip install playwright requests
    playwright install chromium

Dùng:
    python dlab_autosubmit.py --dry-run        # chỉ liệt kê bài chưa xanh + có trong repo, KHÔNG nộp
    python dlab_autosubmit.py                  # tự quét bảng, nộp tất cả bài chưa xanh
    python dlab_autosubmit.py --limit 5        # chỉ nộp tối đa 5 bài
    python dlab_autosubmit.py --prefix DSA02   # chỉ các bài có mã bắt đầu bằng DSA02
    python dlab_autosubmit.py DSA02035         # vẫn có thể chỉ định bài thủ công

Lần đầu: tự đăng nhập DLab, chọn đúng lớp, rồi bấm Enter ở terminal.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ============================ CẤU HÌNH ============================
REPO = "Link repo của nhưng bài cần submit"
BRANCH = "main"                 # đổi thành master nếu cần
DLAB_URL = "https://dlab.ptit.edu.vn"   # <-- SỬA thành URL thật của DLab
LANGUAGE_LABEL = "C/C++"
CODE_EXT = (".cpp", ".c", ".py", ".java")
DELAY_BETWEEN = 5
WAIT_RESULT = 40
MAX_PAGES = 10                  # số trang tối đa của bảng bài tập
PROFILE_DIR = Path("./profile")
CACHE_DIR = Path("./downloaded")
# ==================================================================

JS_ROWS = """
() => {
  const out = [];
  document.querySelectorAll('tr.ant-table-row').forEach(tr => {
    const tds = [...tr.querySelectorAll('td')];
    if (tds.length < 3) return;
    const texts = tds.map(td => td.innerText.trim());
    const code = texts.find(t => /^[A-Za-z][A-Za-z0-9_]{3,}$/.test(t));
    if (!code) return;
    const bg = el => getComputedStyle(el).backgroundColor;
    out.push({code: code, bgs: [bg(tr), ...tds.map(bg)]});
  });
  return out;
}
"""


# ---------------------------- GitHub ----------------------------
def github_tree():
    url = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return [t["path"] for t in r.json()["tree"] if t["type"] == "blob"]


def find_file(paths, code):
    code_l = code.lower()
    hits = [p for p in paths if code_l in p.lower() and p.lower().endswith(CODE_EXT)]
    hits.sort(key=lambda p: (not Path(p).name.lower().startswith(code_l), len(p)))
    return hits[0] if hits else None


def download(path):
    CACHE_DIR.mkdir(exist_ok=True)
    raw = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{requests.utils.quote(path)}"
    r = requests.get(raw, timeout=30)
    r.raise_for_status()
    out = CACHE_DIR / Path(path).name
    out.write_bytes(r.content)
    return out


# ---------------------------- DLab ----------------------------
def is_green(css_color):
    m = re.findall(r"[\d.]+", css_color or "")
    if len(m) < 3:
        return False
    r, g, b = float(m[0]), float(m[1]), float(m[2])
    a = float(m[3]) if len(m) > 3 else 1.0
    if a < 0.05:
        return False
    return g > r + 10 and g > b + 10


def goto_dashboard(page):
    page.goto(DLAB_URL)
    page.wait_for_load_state("networkidle")
    page.locator("tr.ant-table-row").first.wait_for(timeout=30000)


def collect_pending(page):
    """Quét toàn bộ các trang của bảng, trả về (pending, done)."""
    goto_dashboard(page)
    pending, done, seen = [], [], set()
    for n in range(1, MAX_PAGES + 1):
        page.wait_for_timeout(800)
        rows = page.evaluate(JS_ROWS)
        new = 0
        for r in rows:
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            new += 1
            (done if any(is_green(c) for c in r["bgs"]) else pending).append(r["code"].upper())
        if new == 0:
            break
        # sang trang kế tiếp
        nxt = page.locator(f"li.ant-pagination-item-{n + 1}")
        if nxt.count() == 0:
            break
        nxt.first.click()
    return pending, done


def open_problem(page, code):
    goto_dashboard(page)
    box = page.get_by_placeholder(re.compile("Tìm mã", re.I))
    box.wait_for(timeout=15000)
    box.click()
    box.fill("")
    box.type(code, delay=60)
    page.wait_for_timeout(1500)
    try:
        page.locator("tr.ant-table-row").get_by_text(code, exact=False).first.click(timeout=10000)
    except Exception:
        page.screenshot(path="debug.png", full_page=True)
        print("  [!] Đã lưu debug.png để kiểm tra trang lúc lỗi")
        raise
    page.wait_for_load_state("networkidle")


def submit(page, file_path):
    page.locator("input[type=file]").first.set_input_files(str(file_path))
    try:
        page.locator("select").last.select_option(label=LANGUAGE_LABEL)
    except Exception:
        pass
    page.get_by_role("button", name=re.compile("Nộp bài")).click()

    deadline = time.time() + WAIT_RESULT
    result = "?"
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        row = page.locator("tr.ant-table-row").first
        txt = row.inner_text() if row.count() else ""
        if txt and "Chưa có bài nộp" not in txt and \
                not re.search(r"(đang|pending|queue|chờ|running)", txt, re.I):
            result = " | ".join(x.strip() for x in txt.split("\t") if x.strip())
            break
    return result


# ---------------------------- main ----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="(tuỳ chọn) mã bài chỉ định thủ công")
    ap.add_argument("--dry-run", action="store_true", help="chỉ liệt kê, không nộp")
    ap.add_argument("--limit", type=int, default=0, help="số bài tối đa sẽ nộp")
    ap.add_argument("--prefix", default="", help="chỉ lấy bài có mã bắt đầu bằng chuỗi này")
    args = ap.parse_args()

    print("[*] Đang đọc repo GitHub...")
    paths = github_tree()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(DLAB_URL)
        input("Đăng nhập DLab nếu cần (chọn lớp 06 - INT1306), rồi bấm Enter ở đây... ")

        if args.codes:
            codes = [c.strip().upper() for c in args.codes]
        else:
            print("[*] Đang quét bảng bài tập...")
            pending, done = collect_pending(page)
            print(f"    Đã xanh: {len(done)} bài | Chưa xanh: {len(pending)} bài")
            codes = pending

        if args.prefix:
            codes = [c for c in codes if c.startswith(args.prefix.upper())]

        # chỉ giữ bài có trong repo
        todo, missing = [], []
        for c in codes:
            path = find_file(paths, c)
            (todo if path else missing).append((c, path) if path else c)
        print(f"[*] Có code trong repo: {len(todo)} | Không có: {len(missing)}")
        if missing:
            print("    Không có trong repo:", ", ".join(missing))

        if args.limit:
            todo = todo[:args.limit]

        if args.dry_run:
            for c, path in todo:
                print(f"    - {c}  ->  {path}")
            ctx.close()
            return

        for i, (code, path) in enumerate(todo, 1):
            print(f"\n=== [{i}/{len(todo)}] {code} ===")
            print(f"  [+] {path}")
            try:
                local = download(path)
                open_problem(page, code)
                print(f"  [=] Kết quả: {submit(page, local)}")
            except PWTimeout as e:
                print(f"  [!] Timeout: {e}")
            except Exception as e:
                print(f"  [!] Lỗi: {e}")
            time.sleep(DELAY_BETWEEN)

        ctx.close()


if __name__ == "__main__":
    main()
