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
STATE_FILE = ".last_run_timestamp" 
PROMPT_TEMPLATE_FILE = "prompt_template.md"
EMAIL_TEMPLATE_FILE = "email_template.html"

# Cau hinh logging
LOGGING_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)

# --- Ham chuc nang ---

def get_last_run_timestamp():
    # doc timestamp tu file state
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return datetime.fromisoformat(f.read().strip())
            except ValueError:
                return None
    return None

def save_last_run_timestamp(timestamp):
    # luu timestamp vao file state
    with open(STATE_FILE, 'w') as f:
        f.write(timestamp.isoformat())

def read_new_log_entries(file_path, hours, timezone_str):
    """
    Doc cac dong log moi ke tu lan chay cuoi cung.
    Neu la lan dau tien, se doc log trong khoang `hours` gio.
    """
    logging.info(f"Bat dau doc log tu '{file_path}'.")
    
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        last_run_time = get_last_run_timestamp()
        if last_run_time:
            # chuyen doi last_run_time sang cung timezone
            time_cutoff = last_run_time.astimezone(tz)
            logging.info(f"Doc log ke tu lan chay cuoi: {time_cutoff.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            time_cutoff = now - timedelta(hours=hours)
            logging.info(f"Lan chay dau tien. Doc log trong vong {hours} gio qua.")

        new_entries = []
        latest_log_time = time_cutoff
        current_year = now.year

        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    log_time_str = line[:15]
                    log_datetime_naive = datetime.strptime(f"{current_year} {log_time_str}", "%Y %b %d %H:%M:%S")
                    log_datetime_aware = tz.localize(log_datetime_naive)
                    
                    if log_datetime_aware > now:
                        log_datetime_aware = log_datetime_aware.replace(year=current_year - 1)
                    
                    if log_datetime_aware > time_cutoff:
                        new_entries.append(line)
                        if log_datetime_aware > latest_log_time:
                            latest_log_time = log_datetime_aware
                except ValueError:
                    continue
        
        # luu timestamp moi nhat cho lan chay tiep theo
        # Chi luu neu co log moi de tranh file state bi ghi de boi timestamp cu
        if new_entries:
            save_last_run_timestamp(latest_log_time)
        
        logging.info(f"Tim thay {len(new_entries)} dong log moi.")
        return "".join(new_entries)

    except FileNotFoundError:
        logging.error(f"Loi: Khong tim thay file log tai '{file_path}'.")
        return None
    except pytz.UnknownTimeZoneError:
        logging.error(f"Loi: Mui gio khong hop le '{timezone_str}'.")
        return None
    except Exception as e:
        logging.error(f"Da xay ra loi khong mong muon khi doc file: {e}")
        return None

def analyze_logs_with_gemini(logs_content, api_key):
    """
    Gui log den Gemini AI de phan tich su dung template.
    """
    if not logs_content or not logs_content.strip():
        logging.warning("Noi dung log trong, bo qua phan tich.")
        return "Không có sự kiện nào đáng chú ý trong khoảng thời gian được chọn."

    try:
        with open(PROMPT_TEMPLATE_FILE, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        logging.error(f"Loi: Khong tim thay file template '{PROMPT_TEMPLATE_FILE}'.")
        return f"Lỗi hệ thống: Không tìm thấy file '{PROMPT_TEMPLATE_FILE}'."

    genai.configure(api_key=api_key)
    prompt = prompt_template.format(logs_content=logs_content)

    try:
        logging.info("Gui log den Gemini de phan tich (timeout 120 giay)...")
        model = genai.GenerativeModel('gemini-2.5-flash') 
        request_options = {"timeout": 120}
        response = model.generate_content(prompt, request_options=request_options)
        logging.info("Nhan phan tich tu Gemini thanh cong.")
        return response.text
    except google_exceptions.DeadlineExceeded:
        logging.error("Loi: Yeu cau den Gemini bi het thoi gian cho (timeout).")
        return "Không thể nhận phân tích từ Gemini do hết thời gian chờ."
    except Exception as e:
        logging.error(f"Loi khi giao tiep voi Gemini: {e}")
        return f"Đã xảy ra lỗi khi phân tích log với Gemini: {e}"

def send_email(subject, body_html, config):
    """
    Gui email bao cao.
    """
    sender_email = config.get('Email', 'SenderEmail')
    sender_password = config.get('Email', 'SenderPassword')
    recipient_emails_str = config.get('Email', 'RecipientEmails')
    recipient_emails_list = [email.strip() for email in recipient_emails_str.split(',')]
    
    logging.info(f"Dang chuan bi gui email den {recipient_emails_str}...")
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_emails_str
    msg['Subject'] = subject

    msg.attach(MIMEText(body_html, 'html'))

    try:
        server = smtplib.SMTP(config.get('Email', 'SMTPServer'), config.getint('Email', 'SMTPPort'))
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_emails_list, msg.as_string())
        server.quit()
        logging.info("Email da duoc gui thanh cong!")
    except smtplib.SMTPAuthenticationError:
        logging.error("Loi xac thuc SMTP. Vui long kiem tra lai email va mat khau.")
    except Exception as e:
        logging.error(f"Loi khi gui email: {e}")

def run_analysis_cycle():
    """
    Ham thuc hien mot chu ky phan tich hoan chinh.
    """
    logging.info("Bat dau chu ky phan tich log pfSense.")

    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"Loi: File cau hinh '{CONFIG_FILE}' khong ton tai.")
        return
    config.read(CONFIG_FILE)

    try:
        log_file = config.get('Syslog', 'LogFile')
        hours = config.getint('Syslog', 'HoursToAnalyze')
        timezone = config.get('System', 'TimeZone')
        gemini_api_key = config.get('Gemini', 'APIKey')
        hostname = config.get('System', 'PFSenseHostname')
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        logging.error(f"Loi doc file cau hinh: {e}. Vui long kiem tra lai file '{CONFIG_FILE}'.")
        return

    if not gemini_api_key or gemini_api_key == "YOUR_API_KEY_HERE":
        logging.error("Loi: 'APIKey' trong file config.ini chua duoc thiet lap.")
        return

    logs_content = read_new_log_entries(log_file, hours, timezone)
    if logs_content is None:
        logging.error("Khong the tiep tuc do khong doc duoc file log.")
        return

    analysis_result = analyze_logs_with_gemini(logs_content, gemini_api_key)

    email_subject = f"Báo cáo Log pfSense [{hostname}] - {datetime.now(pytz.timezone(timezone)).strftime('%Y-%m-%d %H:%M')}"
    
    try:
        with open(EMAIL_TEMPLATE_FILE, 'r', encoding='utf-8') as f:
            email_template = f.read()
        
        analysis_html = markdown.markdown(analysis_result)
        
        email_body_html = email_template.format(
            hostname=hostname,
            hours=hours,
            analysis_result=analysis_html
        )
        send_email(email_subject, email_body_html, config)
    except FileNotFoundError:
        logging.error(f"Loi: Khong tim thay file email template '{EMAIL_TEMPLATE_FILE}'. Email se khong duoc gui.")
    except Exception as e:
        logging.error(f"Loi khi tao noi dung email: {e}")

    logging.info("Hoan tat chu ky phan tich.")


def main():
    """
    Ham chinh dieu khien vong lap chay lien tuc cua script.
    """
    while True:
        run_analysis_cycle()
        
        interval_seconds = 86400  # mac dinh 24h
        try:
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE)
            interval_seconds = config.getint('System', 'RunIntervalSeconds')
        except Exception as e:
            logging.error(f"Khong the doc 'RunIntervalSeconds' tu config.ini: {e}. Su dung gia tri mac dinh la {interval_seconds} giay.")
            
        logging.info(f"Chu ky tiep theo se bat dau sau {interval_seconds} giay. Tam nghi...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    main()