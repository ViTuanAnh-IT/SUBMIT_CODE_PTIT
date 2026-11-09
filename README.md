# DLab Auto Submit

Tool tự động nộp bài trên DLab (PTIT) bằng Python + Playwright. Tool quét bảng bài tập của lớp, lọc ra các bài **chưa có màu xanh** (chưa AC), tìm file code tương ứng theo mã đề trong một repo GitHub, rồi tự upload và nộp.

## Cách hoạt động

1. Đọc cây thư mục repo GitHub qua API, tìm file có chứa mã bài (ví dụ `DSA02035`).
2. Mở DLab bằng profile trình duyệt đã đăng nhập sẵn.
3. Quét bảng bài tập của lớp (qua tất cả các trang), lọc ra bài chưa xanh.
4. Với mỗi bài có code trong repo: tải file về, mở trang làm bài, upload file, chọn C/C++, bấm **Nộp bài**.
5. Đọc kết quả ở bảng "Lịch sử nộp của tôi" và in ra terminal.

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
REPO = "ten-user/ten-repo"            # repo chứa code
BRANCH = "main"                       # hoặc "master"
DLAB_URL = "https://..."              # địa chỉ trang DLab
LANGUAGE_LABEL = "C/C++"
DELAY_BETWEEN = 5                     # giây nghỉ giữa các lần nộp
WAIT_RESULT = 40                      # giây tối đa chờ chấm bài
```

## Sử dụng

```bash
# Chỉ liệt kê bài chưa xanh và có trong repo, KHÔNG nộp (nên chạy trước)
python dlab_autosubmit.py --dry-run

# Nộp thử tối đa 3 bài
python dlab_autosubmit.py --limit 3

# Nộp tất cả bài chưa xanh
python dlab_autosubmit.py

# Chỉ các bài có mã bắt đầu bằng DSA02
python dlab_autosubmit.py --prefix DSA02

# Nộp bài chỉ định thủ công
python dlab_autosubmit.py DSA02035 DSA03001
```

Lần chạy đầu: trình duyệt mở ra, bạn tự đăng nhập DLab và chọn đúng lớp, sau đó quay lại terminal bấm **Enter**. Phiên đăng nhập được lưu để lần sau không phải đăng nhập lại.

## Cấu trúc thư mục

| Thư mục / file | Mô tả |
|---|---|
| `dlab_autosubmit.py` | Script chính |
| `downloaded/` | File code tải tạm từ GitHub (tự tạo, có thể xóa) |
| `profile/` | Hồ sơ trình duyệt, chứa phiên đăng nhập. **Không commit lên GitHub** |
| `debug.png` | Ảnh chụp trang khi tool gặp lỗi (nếu có) |

## Xử lý lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `404` khi đọc repo | Sai `BRANCH`, đổi `main` ↔ `master` |
| `Không thấy file trong repo` | Repo không có file chứa mã bài đó |
| `Timeout` khi chờ bảng | DLab dùng bảng Ant Design, tool đọc các dòng `tr.ant-table-row`. Kiểm tra đã chọn đúng lớp và `DLAB_URL` đúng |
| Nhận diện sai bài đã xanh | Chỉnh hàm `is_green` hoặc cách nhận diện trong `JS_ROWS` |
| Kết quả hiện `?` | Bài chấm lâu, tăng `WAIT_RESULT` hoặc xem kết quả trên DLab |

## Lưu ý

- Không chia sẻ thư mục `profile/`, vì nó chứa phiên đăng nhập tài khoản của bạn.
- Đừng giảm `DELAY_BETWEEN` quá thấp để tránh bị hệ thống chặn do nộp dồn dập.
- Tool chỉ nên dùng với code của chính bạn và cho mục đích học tập. Hãy đọc hiểu code trước khi nộp, và tuân thủ quy định của lớp/trường về làm bài.
