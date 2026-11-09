#!/usr/bin/env python3
"""
DLab (code.ptit.edu.vn) auto-submit tool - chế độ TỰ ĐỘNG.

Cài đặt:
    pip install playwright requests
    playwright install chromium

Dùng:
    python dlab_autosubmit.py --dry-run          # chỉ liệt kê, KHÔNG nộp
    python dlab_autosubmit.py --limit 3          # nộp thử tối đa 3 bài
    python dlab_autosubmit.py                    # nộp tất cả bài chưa AC
    python dlab_autosubmit.py --skip-attempted   # bỏ qua bài đã làm sai (dấu ✘)
    python dlab_autosubmit.py --prefix DSA02     # chỉ các bài có mã bắt đầu bằng DSA02
    python dlab_autosubmit.py --repo <link repo> # ghi đè REPO_URL
    python dlab_autosubmit.py DSA02035           # chỉ định bài thủ công

Lần đầu: tự đăng nhập, chọn đúng lớp, rồi bấm Enter ở terminal.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ============================ CẤU HÌNH ============================
REPO_URL = ""   # dán thẳng link repo (hoặc dùng --repo)
BRANCH = ""                     # để trống = tự lấy nhánh mặc định của repo
REPO = ""                       # tự tách ra từ REPO_URL, không cần sửa

COURSES_URL = "https://code.ptit.edu.vn/courses"          # trang danh sách bài của lớp
SUBMIT_URL = "https://code.ptit.edu.vn/submit/{code}"     # trang làm bài của từng mã

LANGUAGE_LABEL = "C/C++"
CODE_EXT = (".cpp", ".c", ".py", ".java")
DELAY_BETWEEN = 5               # giây nghỉ giữa các lần nộp
WAIT_RESULT = 40                # giây tối đa chờ chấm bài
PROFILE_DIR = Path("./profile")
CACHE_DIR = Path("./downloaded")
STATE_FILE = Path("./state.json")   # ghi nhớ bài đã nộp / đã AC giữa các lần chạy
AC_PATTERN = re.compile(r"\bAC\b|accept|đúng|đạt|100\s*/\s*100", re.I)
# ==================================================================

# Mỗi dòng bài tập: class "row-solved" = đã AC, "row-attempted" = làm sai (✘), không có = chưa làm
JS_ROWS = """
() => {
  const out = [];
  document.querySelectorAll('tr.ant-table-row').forEach(tr => {
    const a = tr.querySelector('a[href^="/submit/"]');
    if (!a) return;
    const code = a.getAttribute('href').split('/').pop();
    const cls = tr.className || '';
    out.push({
      code: code,
      solved: cls.includes('row-solved'),
      attempted: cls.includes('row-attempted')
    });
  });
  return out;
}
"""

JS_TOTAL_PAGES = """
() => {
  const nums = [...document.querySelectorAll('li.ant-pagination-item')]
    .map(li => parseInt(li.getAttribute('title'))).filter(n => !isNaN(n));
  return nums.length ? Math.max(...nums) : 1;
}
"""


# ---------------------------- GitHub ----------------------------
def parse_repo(link):
    """Nhận link repo (hoặc 'owner/repo'), trả về (owner/repo, branch hoặc '')."""
    link = link.strip()
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s?#]+)(?:/tree/([^/?#\s]+))?", link)
    if m:
        owner, name, branch = m.group(1), m.group(2), m.group(3) or ""
    else:
        m = re.fullmatch(r"([\w.-]+)/([\w.-]+)", link)
        if not m:
            sys.exit(f"Link repo không hợp lệ: {link}")
        owner, name, branch = m.group(1), m.group(2), ""
    if name.endswith(".git"):
        name = name[:-4]
    return f"{owner}/{name}", branch


def github_tree():
    global BRANCH
    if not BRANCH:
        info = requests.get(f"https://api.github.com/repos/{REPO}", timeout=30)
        info.raise_for_status()
        BRANCH = info.json()["default_branch"]
        print(f"[*] Nhánh mặc định: {BRANCH}")
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
    raw = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{quote(path)}"
    r = requests.get(raw, timeout=30)
    r.raise_for_status()
    out = CACHE_DIR / Path(path).name
    out.write_bytes(r.content)
    return out


# ---------------------------- State ----------------------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------- DLab: quét bảng ----------------------------
def goto_courses(page):
    page.goto(COURSES_URL)
    page.wait_for_load_state("networkidle")
    page.locator("tr.ant-table-row").first.wait_for(timeout=30000)


def read_rows(page):
    return page.evaluate(JS_ROWS)


def go_to_page(page, n):
    """Chuyển tới trang n của bảng và chờ nội dung đổi. Trả về True nếu thành công."""
    li = page.locator(f"li.ant-pagination-item-{n}")
    if li.count() == 0:
        return False
    cls = li.first.get_attribute("class") or ""
    if "ant-pagination-item-active" in cls:
        return True                      # đang ở đúng trang rồi
    before = read_rows(page)
    first_before = before[0]["code"] if before else None
    li.first.click()
    for _ in range(40):                  # tối đa ~12 giây
        page.wait_for_timeout(300)
        cur = read_rows(page)
        if cur and cur[0]["code"] != first_before:
            page.wait_for_timeout(300)
            return True
    return False


def collect_pending(page):
    """Quét toàn bộ các trang, trả về (pending, solved, attempted)."""
    goto_courses(page)
    total = page.evaluate(JS_TOTAL_PAGES)
    print(f"    Bảng có {total} trang")

    pending, solved, attempted, seen = [], [], set(), set()
    for n in range(1, total + 1):
        if not go_to_page(page, n):
            print(f"    [!] Không chuyển được sang trang {n}, bỏ qua trang này.")
            continue
        rows = read_rows(page)
        a = s = u = 0
        for r in rows:
            code = r["code"].upper()
            if code in seen:
                continue
            seen.add(code)
            if r["solved"]:
                solved.append(code); s += 1
            else:
                pending.append(code)
                if r["attempted"]:
                    attempted.add(code); a += 1
                else:
                    u += 1
        print(f"    Trang {n}: {len(rows)} dòng | AC {s} | làm sai {a} | chưa làm {u}"
              f" | {rows[0]['code']} -> {rows[-1]['code']}" if rows else f"    Trang {n}: 0 dòng")
    return pending, solved, attempted


# ---------------------------- DLab: nộp bài ----------------------------
def open_problem(page, code):
    page.goto(SUBMIT_URL.format(code=code))
    page.wait_for_load_state("networkidle")
    page.locator("input[type=file]").first.wait_for(state="attached", timeout=20000)


def first_history_row(page):
    row = page.locator("tr.ant-table-row").first
    return row.inner_text().strip() if row.count() else ""


def submit(page, file_path):
    before = first_history_row(page)      # dòng lịch sử hiện tại, để nhận ra dòng mới
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
        txt = first_history_row(page)
        if txt and txt != before and \
                not re.search(r"(đang|pending|queue|chờ|running)", txt, re.I):
            result = " | ".join(x.strip() for x in re.split(r"\s*\n\s*|\t", txt) if x.strip())
            break
    return result


# ---------------------------- main ----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="(tuỳ chọn) mã bài chỉ định thủ công")
    ap.add_argument("--repo", default="", help="link repo GitHub (ghi đè REPO_URL)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ liệt kê, không nộp")
    ap.add_argument("--limit", type=int, default=0, help="số bài tối đa sẽ nộp")
    ap.add_argument("--prefix", default="", help="chỉ lấy bài có mã bắt đầu bằng chuỗi này")
    ap.add_argument("--skip-attempted", action="store_true",
                    help="bỏ qua bài đã làm sai (dấu ✘) trên DLab")
    ap.add_argument("--retry-failed", action="store_true",
                    help="nộp lại cả bài tool từng nộp nhưng chưa AC")
    args = ap.parse_args()

    global REPO, BRANCH
    REPO, link_branch = parse_repo(args.repo or REPO_URL)
    if link_branch:
        BRANCH = link_branch
    print(f"[*] Repo: {REPO}")

    print("[*] Đang đọc repo GitHub...")
    paths = github_tree()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(COURSES_URL)
        input("Đăng nhập nếu cần và chọn đúng lớp (06 - INT1306), rồi bấm Enter ở đây... ")

        if args.codes:
            codes = [c.strip().upper() for c in args.codes]
            attempted = set()
        else:
            print("[*] Đang quét bảng bài tập...")
            pending, solved, attempted = collect_pending(page)
            print(f"    TỔNG: đã AC {len(solved)} | chưa AC {len(pending)} "
                  f"(trong đó làm sai {len(attempted)})")
            codes = pending

        if args.skip_attempted:
            codes = [c for c in codes if c not in attempted]
        if args.prefix:
            codes = [c for c in codes if c.startswith(args.prefix.upper())]

        # bỏ qua bài tool đã nộp ở các lần chạy trước (state.json)
        state = load_state()
        skipped = [c for c in codes if c in state and (state[c].get("ac") or not args.retry_failed)]
        codes = [c for c in codes if c not in skipped]
        if skipped:
            print(f"[*] Bỏ qua {len(skipped)} bài tool đã nộp trước đó (state.json)")

        # chỉ giữ bài có trong repo
        todo, missing = [], []
        for c in codes:
            path = find_file(paths, c)
            if path:
                todo.append((c, path))
            else:
                missing.append(c)
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
                res = submit(page, local)
                ac = bool(AC_PATTERN.search(res))
                print(f"  [=] Kết quả: {res}  ->  {'AC' if ac else 'CHƯA AC'}")
                state[code] = {"ac": ac, "result": res, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                save_state(state)
            except PWTimeout as e:
                page.screenshot(path="debug.png", full_page=True)
                print(f"  [!] Timeout (đã lưu debug.png): {e}")
            except Exception as e:
                print(f"  [!] Lỗi: {e}")
            time.sleep(DELAY_BETWEEN)

        ctx.close()


if __name__ == "__main__":
    main()
