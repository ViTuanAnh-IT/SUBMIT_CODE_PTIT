#!/usr/bin/env python3
"""
DLab (code.ptit.edu.vn) auto-submit tool - chế độ TỰ ĐỘNG (bản tối ưu tốc độ).

Cài đặt:
    pip install playwright requests
    playwright install chromium

Dùng:
    python dlab_autosubmit.py --dry-run          # chỉ liệt kê, KHÔNG nộp
    python dlab_autosubmit.py --limit 3          # nộp thử tối đa 3 bài
    python dlab_autosubmit.py                    # nộp tất cả bài chưa AC (chế độ nhanh)
    python dlab_autosubmit.py --wait-each        # chờ chấm xong từng bài (chậm hơn, thấy kết quả ngay)
    python dlab_autosubmit.py --delay 1          # nghỉ 1 giây giữa các bài
    python dlab_autosubmit.py --class INT1359    # dừng nếu lớp đang chọn không phải INT1359
    python dlab_autosubmit.py --skip-attempted   # bỏ qua bài đã làm sai (dấu ✘)
    python dlab_autosubmit.py --prefix DSA02     # chỉ các bài có mã bắt đầu bằng DSA02
    python dlab_autosubmit.py --repo <link repo> # ghi đè REPO_URL
    python dlab_autosubmit.py DSA02035           # chỉ định bài thủ công

Chế độ nhanh (mặc định): nộp liên tiếp, không đứng chờ chấm từng bài. Server chấm song song
trong lúc tool nộp bài kế tiếp; cuối cùng tool quét lại bảng bài tập để biết bài nào đã AC.

Lần đầu: tự đăng nhập, chọn đúng lớp, rồi bấm Enter ở terminal.
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ============================ CẤU HÌNH ============================
REPO_URL = "https://github.com/TuNguyen0304/full-codeptit/tree/main/dsa-cpp"   # dán thẳng link repo (hoặc dùng --repo) //ở đây tôi đang gắn sẵn link repo của DSA
BRANCH = ""                     # để trống = tự lấy nhánh mặc định của repo
REPO = ""                       # tự tách ra từ REPO_URL, không cần sửa

COURSES_URL = "https://code.ptit.edu.vn/courses"          # trang danh sách bài của lớp
SUBMIT_URL = "https://code.ptit.edu.vn/submit/{code}"     # trang làm bài của từng mã

LANGUAGE_LABEL = "C/C++"
CODE_EXT = (".cpp", ".c", ".py", ".java")
DELAY_BETWEEN = 2               # giây nghỉ giữa các lần nộp (đừng đặt quá thấp)
WAIT_RESULT = 40                # giây tối đa chờ chấm bài (chỉ dùng với --wait-each)
GRADE_WAITS = (5, 8, 12)        # chế độ nhanh: các lần chờ rồi kiểm tra lại bảng (giây)
BLOCK_ASSETS = False            # True = chặn ảnh/font/video (có thể làm trang DLab bị trắng/treo; dùng --block-assets để thử)
DOWNLOAD_WORKERS = 8            # số luồng tải code song song từ GitHub
PROFILE_DIR = Path("./profile")
CACHE_DIR = Path("./downloaded")
STATE_FILE = Path("./state.json")   # tự đổi thành state_<MÃ LỚP>.json theo lớp đang chọn
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
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(code_l) + r"(?![a-z0-9])", re.I)
    hits = [p for p in paths if p.lower().endswith(CODE_EXT) and pat.search(p)]
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


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------- DLab: quét bảng ----------------------------
def goto_courses(page):
    page.goto(COURSES_URL, wait_until="domcontentloaded")
    page.locator("tr.ant-table-row").first.wait_for(timeout=30000)


def get_class_label(page):
    """Đọc tên lớp đang chọn ở ô dropdown 'Lớp học của tôi'."""
    pat = re.compile(r"INT\s*_?\d+", re.I)
    candidates = [
        page.locator(".ant-select").filter(has_text=pat),
        page.locator(".ant-select-selection-item").filter(has_text=pat),
        page.locator(".ant-select-selection-placeholder").filter(has_text=pat),
    ]
    for loc in candidates:
        try:
            if loc.count():
                txt = loc.first.inner_text().strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def class_slug(label):
    m = re.search(r"INT\s*_?\d+", label, re.I)
    if m:
        return re.sub(r"\W+", "", m.group(0)).upper()
    return re.sub(r"\W+", "_", label)[:30] or "default"


def read_rows(page):
    return page.evaluate(JS_ROWS)


def go_to_page(page, n):
    """Chuyển tới trang n của bảng và chờ nội dung đổi. Trả về True nếu thành công."""
    li = page.locator(f"li.ant-pagination-item-{n}")
    if li.count() == 0:
        return n == 1                    # lớp ít bài: không có phân trang, chỉ có trang 1
    cls = li.first.get_attribute("class") or ""
    if "ant-pagination-item-active" in cls:
        return True                      # đang ở đúng trang rồi
    before = read_rows(page)
    first_before = before[0]["code"] if before else None
    li.first.click()
    for _ in range(80):                  # tối đa ~12 giây
        page.wait_for_timeout(150)
        cur = read_rows(page)
        if cur and cur[0]["code"] != first_before:
            return True
    return False


def collect_pending(page, quiet=False):
    """Quét toàn bộ các trang, trả về (pending, solved, attempted)."""
    if "/courses" not in page.url:
        goto_courses(page)
    page.locator("tr.ant-table-row").first.wait_for(timeout=30000)
    total = page.evaluate(JS_TOTAL_PAGES)
    if not quiet:
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
        if not quiet:
            if rows:
                print(f"    Trang {n}: {len(rows)} dòng | AC {s} | làm sai {a} | chưa làm {u}"
                      f" | {rows[0]['code']} -> {rows[-1]['code']}")
            else:
                print(f"    Trang {n}: 0 dòng")
    return pending, solved, attempted


# ---------------------------- DLab: nộp bài ----------------------------
def open_problem(page, code):
    page.goto(SUBMIT_URL.format(code=code), wait_until="domcontentloaded")
    page.locator("input[type=file]").first.wait_for(state="attached", timeout=20000)
    try:
        head = page.get_by_text(re.compile(r"Làm bài lớp")).first.inner_text(timeout=1500).strip()
        print(f"  [i] {head}")
    except Exception:
        pass


def first_history_row(page):
    row = page.locator("tr.ant-table-row").first
    return row.inner_text().strip() if row.count() else ""


def submit(page, file_path, wait_result):
    """Nộp bài. wait_result=False: chỉ chờ dòng lịch sử mới xuất hiện rồi đi tiếp."""
    before = first_history_row(page)      # dòng lịch sử hiện tại, để nhận ra dòng mới
    page.locator("input[type=file]").first.set_input_files(str(file_path))
    try:
        page.locator("select").last.select_option(label=LANGUAGE_LABEL, timeout=1500)
    except Exception:
        pass
    page.get_by_role("button", name=re.compile("Nộp bài")).click()

    if not wait_result:
        deadline = time.time() + 15
        while time.time() < deadline:
            page.wait_for_timeout(400)
            txt = first_history_row(page)
            if txt and txt != before:
                return "đã nộp, chờ chấm"
        return "đã bấm nộp (chưa thấy dòng lịch sử mới)"

    deadline = time.time() + WAIT_RESULT
    result = "?"
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        txt = first_history_row(page)
        if txt and txt != before and \
                not re.search(r"(đang|pending|queue|chờ|running)", txt, re.I):
            result = " | ".join(x.strip() for x in re.split(r"\s*\n\s*|\t", txt) if x.strip())
            break
    return result


def verify_submitted(page, submitted, label, state):
    """Chế độ nhanh: quét lại bảng bài tập để biết bài nào đã AC."""
    remaining = set(submitted)
    for wait in GRADE_WAITS:
        print(f"[*] Chờ {wait}s cho hệ thống chấm rồi kiểm tra lại bảng...")
        time.sleep(wait)
        try:
            goto_courses(page)
            now_label = get_class_label(page)
            if label and now_label and now_label != label:
                print(f"[!] Sau khi tải lại, lớp đang hiển thị là '{now_label}' (khác '{label}'). "
                      "Bỏ qua bước xác nhận, hãy kiểm tra trên DLab.")
                return
            _, solved, _ = collect_pending(page, quiet=True)
        except Exception as e:
            print(f"[!] Không quét lại được bảng: {e}")
            return
        solved = set(solved)
        for c in list(remaining):
            if c in solved:
                state[c] = {"ac": True, "result": "AC (xác nhận từ bảng bài tập)", "time": now()}
                remaining.discard(c)
        save_state(state)
        if not remaining:
            break
    for c in remaining:
        state[c] = {"ac": False, "result": "chưa thấy AC sau khi chấm", "time": now()}
    save_state(state)
    ok = len(submitted) - len(remaining)
    print(f"\n[*] Xác nhận: AC {ok}/{len(submitted)} bài vừa nộp")
    if remaining:
        print("    Chưa AC:", ", ".join(sorted(remaining)))


# ---------------------------- main ----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="(tuỳ chọn) mã bài chỉ định thủ công")
    ap.add_argument("--repo", default="", help="link repo GitHub (ghi đè REPO_URL)")
    ap.add_argument("--class", dest="class_name", default="",
                    help="mã lớp bắt buộc phải đang chọn, ví dụ INT1359 (sai lớp thì dừng)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ liệt kê, không nộp")
    ap.add_argument("--limit", type=int, default=0, help="số bài tối đa sẽ nộp")
    ap.add_argument("--prefix", default="", help="chỉ lấy bài có mã bắt đầu bằng chuỗi này")
    ap.add_argument("--skip-attempted", action="store_true",
                    help="bỏ qua bài đã làm sai (dấu ✘) trên DLab")
    ap.add_argument("--retry-failed", action="store_true",
                    help="nộp lại cả bài tool từng nộp nhưng chưa AC")
    ap.add_argument("--wait-each", action="store_true",
                    help="chờ chấm xong từng bài rồi mới nộp bài kế (chậm hơn)")
    ap.add_argument("--block-assets", action="store_true",
                    help="thử chặn ảnh/font/video cho nhanh (nếu trang bị trắng thì đừng dùng)")
    ap.add_argument("--delay", type=float, default=DELAY_BETWEEN,
                    help=f"giây nghỉ giữa các lần nộp (mặc định {DELAY_BETWEEN})")
    args = ap.parse_args()

    global REPO, BRANCH, STATE_FILE
    REPO, link_branch = parse_repo(args.repo or REPO_URL)
    if link_branch:
        BRANCH = link_branch
    print(f"[*] Repo: {REPO}")

    print("[*] Đang đọc repo GitHub...")
    paths = github_tree()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        if BLOCK_ASSETS or args.block_assets:
            ctx.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font")
                      else route.continue_())
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(COURSES_URL, wait_until="domcontentloaded")
        input("Đăng nhập nếu cần và CHỌN ĐÚNG LỚP muốn nộp ở ô 'Lớp học của tôi', rồi bấm Enter ở đây... ")

        label = get_class_label(page)
        print(f"[*] Lớp đang chọn: {label or '(không đọc được)'}")
        if args.class_name and args.class_name.lower() not in label.lower():
            ctx.close()
            sys.exit(f"[!] Lớp đang chọn không chứa '{args.class_name}'. Dừng để tránh nộp nhầm lớp.")
        STATE_FILE = Path(f"./state_{class_slug(label)}.json")
        print(f"[*] File trạng thái: {STATE_FILE}")

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

        # bỏ qua bài đã AC / đã thử mà chưa AC trong các lần chạy trước (state)
        state = load_state()
        skipped = [c for c in codes if c in state and
                   (state[c].get("ac") is True or
                    (state[c].get("ac") is False and not args.retry_failed))]
        codes = [c for c in codes if c not in skipped]
        if skipped:
            print(f"[*] Bỏ qua {len(skipped)} bài tool đã nộp trước đó ({STATE_FILE.name})")

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
        if not todo:
            print("[*] Không có bài nào cần nộp.")
            ctx.close()
            return

        mode = "chờ chấm từng bài" if args.wait_each else "nhanh (nộp liên tiếp, xác nhận ở cuối)"
        print(f"[*] Chế độ: {mode} | nghỉ {args.delay}s giữa các bài")

        # tải code song song ở nền, trong lúc trình duyệt đang nộp bài
        executor = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)
        futures = {c: executor.submit(download, path) for c, path in todo}

        submitted = []
        for i, (code, path) in enumerate(todo, 1):
            print(f"\n=== [{i}/{len(todo)}] {code} ===")
            print(f"  [+] {path}")
            try:
                local = futures[code].result()
                open_problem(page, code)
                res = submit(page, local, args.wait_each)
                if args.wait_each:
                    ac = bool(AC_PATTERN.search(res))
                    print(f"  [=] Kết quả: {res}  ->  {'AC' if ac else 'CHƯA AC'}")
                    state[code] = {"ac": ac, "result": res, "time": now()}
                else:
                    print(f"  [=] {res}")
                    state[code] = {"ac": None, "result": res, "time": now()}
                    submitted.append(code)
                save_state(state)
            except PWTimeout as e:
                page.screenshot(path="debug.png", full_page=True)
                print(f"  [!] Timeout (đã lưu debug.png): {e}")
            except Exception as e:
                print(f"  [!] Lỗi: {e}")
            if i < len(todo):
                time.sleep(args.delay)

        executor.shutdown(wait=False)

        if submitted:
            verify_submitted(page, submitted, label, state)

        ctx.close()


if __name__ == "__main__":
    main()
