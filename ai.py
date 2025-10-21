#!/usr/bin/env python3

import os
import smtplib
import logging
import configparser
import markdown
import pytz
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

# --- Khai bao ---
CONFIG_FILE = "config.ini"

# Cấu hình logging
LOGGING_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)

# --- Các hàm chức năng ---

def read_recent_log_entries(file_path, hours, timezone_str):
    """
    Đọc và lọc các dòng log trong khoảng thời gian gần đây nhất.
    """
    logging.info(f"Đọc log từ '{file_path}' trong vòng {hours} giờ qua.")
    
    try:
        # lay thoi gian hien tai theo mui gio
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        time_cutoff = now - timedelta(hours=hours)
        
        recent_entries = []
        current_year = now.year

        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    # pfSense log timestamp format: "b M d H:M:S" (e.g., "Oct 17 08:50:00")
                    log_time_str = line[:15]
                    # them nam hien tai vao
                    log_datetime_naive = datetime.strptime(f"{current_year} {log_time_str}", "%Y %b %d %H:%M:%S")
                    log_datetime_aware = tz.localize(log_datetime_naive)
                    
                    # xu ly truong hop log cuoi nam, script chay dau nam
                    if log_datetime_aware > now:
                        log_datetime_aware = log_datetime_aware.replace(year=current_year - 1)
                    
                    if log_datetime_aware >= time_cutoff:
                        recent_entries.append(line)
                except ValueError:
                    # bo qua dong ko co timestamp
                    continue
        
        logging.info(f"Tìm thấy {len(recent_entries)} dòng log phù hợp.")
        return "".join(recent_entries)

    except FileNotFoundError:
        logging.error(f"Lỗi: Không tìm thấy file log tại '{file_path}'.")
        return None
    except pytz.UnknownTimeZoneError:
        logging.error(f"Lỗi: Múi giờ không hợp lệ '{timezone_str}'.")
        return None
    except Exception as e:
        logging.error(f"Đã xảy ra lỗi không mong muốn khi đọc file: {e}")
        return None

def analyze_logs_with_gemini(logs_content, api_key):
    """
    Gửi log đến Gemini AI để phân tích.
    """
    if not logs_content or logs_content.strip() == "":
        logging.warning("Nội dung log trống, bỏ qua phân tích.")
        return "Không có sự kiện nào đáng chú ý trong khoảng thời gian được chọn."

    genai.configure(api_key=api_key)

    prompt = f"""
    Bạn là một chuyên gia phân tích an ninh mạng. Hãy phân tích dữ liệu log của pfSense dưới đây và tạo một báo cáo chi tiết.
    Báo cáo cần có các phần sau:
    1.  **Tóm tắt tổng quan**: Những phát hiện quan trọng nhất.
    2.  **Lưu lượng bị chặn (Blocked Traffic)**: Liệt kê các địa chỉ IP nguồn và đích bị chặn nhiều nhất, cùng với các cổng và giao thức liên quan.
    3.  **Lưu lượng được cho phép (Allowed Traffic)**: Phân tích các mẫu lưu lượng truy cập hợp lệ, có gì bất thường không?
    4.  **Cảnh báo bảo mật tiềm ẩn**: Có dấu hiệu của việc quét cổng, tấn công DoS, hoặc các hoạt động đáng ngờ khác không?
    5.  **Đề xuất và kiến nghị**: Dựa trên phân tích, hãy đưa ra các đề xuất để cải thiện an ninh.

    Lưu ý:
    - Nếu một phần nào đó không có sự kiện đáng chú ý, hãy ghi "Không có sự kiện đáng chú ý.".
    - Trình bày báo cáo bằng tiếng Việt.

    --- DỮ LIỆU LOG ---
    {logs_content}
    --- KẾT THÚC DỮ LIỆU LOG ---
    """

    try:
        logging.info("Gửi log đến Gemini để phân tích (timeout 120 giây)...")
        model = genai.GenerativeModel('gemini-2.5-flash')
        request_options = {"timeout": 120}
        response = model.generate_content(prompt, request_options=request_options)
        logging.info("Nhận phân tích từ Gemini thành công.")
        return response.text
    except google_exceptions.DeadlineExceeded:
        logging.error("Lỗi: Yêu cầu đến Gemini bị hết thời gian chờ (timeout).")
        return "Không thể nhận phân tích từ Gemini do hết thời gian chờ."
    except Exception as e:
        logging.error(f"Lỗi khi giao tiếp với Gemini: {e}")
        return f"Đã xảy ra lỗi khi phân tích log với Gemini: {e}"

def send_email(subject, body, config):
    """
    Gửi email báo cáo.
    """
    sender_email = config.get('Email', 'SenderEmail')
    sender_password = config.get('Email', 'SenderPassword')
    # ho tro nhieu nguoi nhan, cach nhau boi dau phay
    recipient_emails_str = config.get('Email', 'RecipientEmails')
    recipient_emails_list = [email.strip() for email in recipient_emails_str.split(',')]
    
    logging.info(f"Đang chuẩn bị gửi email đến {recipient_emails_str}...")
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_emails_str
    msg['Subject'] = subject

    # chuyen doi markdown sang html
    html_body = markdown.markdown(body)
    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP(config.get('Email', 'SMTPServer'), config.getint('Email', 'SMTPPort'))
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_emails_list, msg.as_string())
        server.quit()
        logging.info("Email đã được gửi thành công!")
    except smtplib.SMTPAuthenticationError:
        logging.error("Lỗi xác thực SMTP. Vui lòng kiểm tra lại email và mật khẩu.")
    except Exception as e:
        logging.error(f"Lỗi khi gửi email: {e}")

def run_analysis_cycle():
    """
    Hàm thực hiện một chu kỳ phân tích hoàn chỉnh.
    """
    logging.info("Bắt đầu chu kỳ phân tích log pfSense.")

    # 1. Load config
    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"Lỗi: File cấu hình '{CONFIG_FILE}' không tồn tại.")
        return
    config.read(CONFIG_FILE)

    # 2. Đọc các thông số cấu hình
    try:
        log_file = config.get('Syslog', 'LogFile')
        hours = config.getint('Syslog', 'HoursToAnalyze')
        timezone = config.get('System', 'TimeZone')
        gemini_api_key = config.get('Gemini', 'APIKey')
        hostname = config.get('System', 'PFSenseHostname')
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        logging.error(f"Lỗi đọc file cấu hình: {e}. Vui lòng kiểm tra lại file '{CONFIG_FILE}'.")
        return

    if not gemini_api_key or gemini_api_key == "YOUR_API_KEY_HERE":
        logging.error("Lỗi: 'APIKey' trong file config.ini chưa được thiết lập.")
        return

    # 3. Đọc log
    logs_content = read_recent_log_entries(log_file, hours, timezone)
    if logs_content is None:
        logging.error("Không thể tiếp tục do không đọc được file log.")
        return

    # 4. Phân tích log
    analysis_result = analyze_logs_with_gemini(logs_content, gemini_api_key)

    # 5. Tao noi dung email
    email_subject = f"Báo cáo Log pfSense [{hostname}] - {datetime.now(pytz.timezone(timezone)).strftime('%Y-%m-%d %H:%M')}"
    email_body = f"""
# Báo cáo phân tích Log pfSense - {hostname}

Xin chào,

Dưới đây là báo cáo phân tích tự động các log trong **{hours}** giờ vừa qua.

---

{analysis_result}

---

*Đây là email được gửi tự động từ hệ thống giám sát.*
"""
    send_email(email_subject, email_body, config)
    logging.info("Hoàn tất chu kỳ phân tích.")


def main():
    """
    Hàm chính điều khiển vòng lặp chạy liên tục của script.
    """
    while True:
        run_analysis_cycle()
        
        
        interval_seconds = 3600  
        try:
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE)
            interval_seconds = config.getint('System', 'RunIntervalSeconds')
        except Exception as e:
            logging.error(f"Không thể đọc 'RunIntervalSeconds' từ config.ini: {e}. Sử dụng giá trị mặc định là 86400 giây.")
            
        logging.info(f"Chu kỳ tiếp theo sẽ bắt đầu sau {interval_seconds} giây. Tạm nghỉ...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    main()