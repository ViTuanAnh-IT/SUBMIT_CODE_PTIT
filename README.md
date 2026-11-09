# DLab Auto Submit

Tool tự động nộp bài trên DLab (`code.ptit.edu.vn`) bằng Python + Playwright.

Tool quét bảng bài tập của lớp, lọc ra các bài **chưa AC**, tìm file code tương ứng theo mã đề trong một repo GitHub, rồi tự upload và nộp. Kết quả mỗi lần nộp được lưu lại để lần chạy sau không nộp lặp.

## Cách hoạt động

1. Đọc cây thư mục repo GitHub qua API, tìm file có chứa mã bài (ví dụ `DSA02035`).
2. Mở DLab bằng profile trình duyệt đã đăng nhập sẵn.
3. Quét bảng bài tập của lớp qua **tất cả các trang** (bắt đầu từ trang 1) và phân loại từng dòng:
   - `row-solved` (✔): đã AC, bỏ qua
   - `row-attempted` (✘): đã làm sai, tính là chưa AC
   - không có class: chưa làm
4. Với mỗi bài chưa AC có code trong repo: tải file về, mở thẳng `https://code.ptit.edu.vn/submit/<MÃ>`, upload file, chọn C/C++, bấm **Nộp bài**.
5. Đọc kết quả ở bảng "Lịch sử nộp của tôi", in ra terminal và ghi vào `state.json`.

## Yêu cầu

- Python 3.9+ (nhớ tick "Add Python to PATH" khi cài)
- Chromium do Playwright cài

## Cài đặt

```bash
pip install playwright requests
playwright install chromium
```

## Cấu hình

Mở `dlab_autosubmit.py`, sửa phần `CẤU HÌNH` ở đầu file:

```python
REPO_URL = "https://github.com/user/repo"   # dán thẳng link repo
BRANCH = ""                                  # để trống = tự lấy nhánh mặc định
COURSES_URL = "https://code.ptit.edu.vn/courses"
SUBMIT_URL = "https://code.ptit.edu.vn/submit/{code}"
LANGUAGE_LABEL = "C/C++"
DELAY_BETWEEN = 5                            # giây nghỉ giữa các lần nộp
WAIT_RESULT = 40                             # giây tối đa chờ chấm bài
```

`REPO_URL` nhận nhiều dạng link: `https://github.com/user/repo`, `.../repo.git`, `.../repo/tree/ten-nhanh`, `git@github.com:user/repo.git` hoặc `user/repo`. Bạn cũng có thể truyền link khi chạy bằng `--repo`.

## Sử dụng

```bash
# Chỉ liệt kê bài chưa AC và có trong repo, KHÔNG nộp (nên chạy đầu tiên)
python dlab_autosubmit.py --dry-run

# Nộp thử tối đa 3 bài
python dlab_autosubmit.py --limit 3

# Nộp tất cả bài chưa AC
python dlab_autosubmit.py

# Bỏ qua bài đã làm sai (✘), chỉ nộp bài chưa làm lần nào
python dlab_autosubmit.py --skip-attempted

# Chỉ các bài có mã bắt đầu bằng DSA02
python dlab_autosubmit.py --prefix DSA02

# Dùng repo khác mà không sửa file
python dlab_autosubmit.py --repo https://github.com/user/repo

# Nộp bài chỉ định thủ công
python dlab_autosubmit.py DSA02035 DSA03001

# Nộp lại cả bài tool từng nộp nhưng chưa AC
python dlab_autosubmit.py --retry-failed
```

Lần chạy đầu: trình duyệt mở ra, bạn tự đăng nhập DLab và chọn đúng lớp, sau đó quay lại terminal bấm **Enter**. Phiên đăng nhập được lưu trong `profile/` nên lần sau không phải đăng nhập lại.

### Ví dụ kết quả `--dry-run`

```
[*] Repo: user/repo
    Bảng có 4 trang
    Trang 1: 100 dòng | AC 40 | làm sai 3 | chưa làm 57 | CHELLO -> DSA03012
    ...
    TỔNG: đã AC 120 | chưa AC 210 (trong đó làm sai 15)
[*] Có code trong repo: 180 | Không có: 30
```

## Cấu trúc thư mục

| Thư mục / file | Mô tả |
|---|---|
| `dlab_autosubmit.py` | Script chính |
| `inspect_rows.py` | Script chẩn đoán, lưu HTML của bảng vào `dump.txt` khi DLab đổi giao diện |
| `downloaded/` | File code tải tạm từ GitHub (tự tạo, có thể xóa) |
| `profile/` | Hồ sơ trình duyệt, chứa phiên đăng nhập. **Không commit lên GitHub** |
| `state.json` | Kết quả các bài tool đã nộp, dùng để không nộp lặp |
| `debug.png` | Ảnh chụp trang khi tool gặp timeout (nếu có) |

## Xử lý lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `404` khi đọc repo | Kiểm tra lại link repo; repo phải là public |
| `Không thấy file trong repo` | Repo không có file chứa mã bài đó, tool tự bỏ qua |
| Chỉ đọc được một vài trang | Xem dòng `Trang n: ...` trong `--dry-run`; nếu thiếu trang, chạy `inspect_rows.py` và kiểm tra phần phân trang |
| Nhận diện sai bài đã AC | DLab có thể đổi class `row-solved` / `row-attempted`; chạy `inspect_rows.py` để xem HTML mới |
| Lỗi ở bước nộp (timeout) | Xem `debug.png`; selector ô upload, nút "Nộp bài" hoặc bảng lịch sử có thể đã đổi |
| Kết quả hiện `?` | Bài chấm lâu, tăng `WAIT_RESULT` hoặc xem kết quả trực tiếp trên DLab |
| Tool nộp lại bài đã nộp | Xóa nhầm `state.json`; hoặc bài đó chưa AC và bạn đang dùng `--retry-failed` |

### Dùng `inspect_rows.py`

```bash
python inspect_rows.py https://code.ptit.edu.vn/courses
```

Chọn đúng lớp, chờ bảng hiện ra, bấm Enter. Nội dung `dump.txt` cho biết cấu trúc HTML hiện tại của bảng và phân trang.

## Lưu ý

- Không chia sẻ thư mục `profile/`, vì nó chứa phiên đăng nhập tài khoản của bạn. File `.gitignore` đã loại sẵn `profile/`, `downloaded/`, `state.json`, `dump.txt` và `debug.png`.
- Lần đầu nên chạy `--dry-run` rồi `--limit 3`, kiểm tra trên DLab xem bài có lên AC thật không, rồi mới chạy toàn bộ.
- Đừng giảm `DELAY_BETWEEN` quá thấp để tránh bị hệ thống chặn do nộp dồn dập.
- Tool chỉ nên dùng với code của chính bạn và cho mục đích học tập. Hãy đọc hiểu code trước khi nộp, và tuân thủ quy định của lớp/trường về làm bài.
