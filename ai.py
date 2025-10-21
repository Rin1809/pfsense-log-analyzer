#!/usr/bin/env python3

import os
import smtplib
import logging
import configparser
import markdown
import pytz
import time
import json
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email import encoders
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

# --- Khai bao ---
CONFIG_FILE = "config.ini"
STATE_FILE = ".last_run_timestamp"
PROMPT_TEMPLATE_FILE = "prompt_template.md"
EMAIL_TEMPLATE_FILE = "email_template.html"
LOGO_FILE = "logo_novaon.png"

# Cau hinh logging
LOGGING_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)



def get_last_run_timestamp():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return datetime.fromisoformat(f.read().strip())
            except ValueError:
                return None
    return None

def save_last_run_timestamp(timestamp):
    with open(STATE_FILE, 'w') as f:
        f.write(timestamp.isoformat())

def read_new_log_entries(file_path, hours, timezone_str):
    logging.info(f"Bat dau doc log tu '{file_path}'.")
    try:
        tz = pytz.timezone(timezone_str)
        end_time = datetime.now(tz)
        last_run_time = get_last_run_timestamp()
        if last_run_time:
            start_time = last_run_time.astimezone(tz)
            logging.info(f"Doc log ke tu lan chay cuoi: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            start_time = end_time - timedelta(hours=hours)
            logging.info(f"Lan chay dau tien. Doc log trong vong {hours} gio qua.")
        new_entries = []
        latest_log_time = start_time
        current_year = end_time.year
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    log_time_str = line[:15]
                    log_datetime_naive = datetime.strptime(f"{current_year} {log_time_str}", "%Y %b %d %H:%M:%S")
                    log_datetime_aware = tz.localize(log_datetime_naive)
                    if log_datetime_aware > end_time:
                        log_datetime_aware = log_datetime_aware.replace(year=current_year - 1)
                    if log_datetime_aware > start_time:
                        new_entries.append(line)
                        if log_datetime_aware > latest_log_time:
                            latest_log_time = log_datetime_aware
                except ValueError:
                    continue
        if new_entries:
            save_last_run_timestamp(latest_log_time)
        logging.info(f"Tim thay {len(new_entries)} dong log moi.")
        return ("".join(new_entries), start_time, end_time)
    except FileNotFoundError:
        logging.error(f"Loi: Khong tim thay file log tai '{file_path}'.")
        return (None, None, None)
    except Exception as e:
        logging.error(f"Da xay ra loi khong mong muon khi doc file: {e}")
        return (None, None, None)

def analyze_logs_with_gemini(logs_content, bonus_context, api_key):
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
    prompt = prompt_template.format(logs_content=logs_content, bonus_context=bonus_context)
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
    # Gui email bao cao, co ho tro dinh kem file
    sender_email = config.get('Email', 'SenderEmail')
    sender_password = config.get('Email', 'SenderPassword')
    recipient_emails_str = config.get('Email', 'RecipientEmails')
    recipient_emails_list = [email.strip() for email in recipient_emails_str.split(',')]
    
    logging.info(f"Dang chuan bi gui email den {recipient_emails_str}...")
    
    msg = MIMEMultipart('mixed')
    msg['From'] = sender_email
    msg['To'] = recipient_emails_str
    msg['Subject'] = subject

    msg_related = MIMEMultipart('related')
    
    network_diagram_path = config.get('Attachments', 'NetworkDiagram', fallback=None)
    if network_diagram_path and os.path.exists(network_diagram_path):
        body_html = body_html.replace('style="display: none;"', '')
    
    msg_related.attach(MIMEText(body_html, 'html'))
    
    try:
        with open(LOGO_FILE, 'rb') as f:
            img_logo = MIMEImage(f.read())
            img_logo.add_header('Content-ID', '<logo_novaon>')
            msg_related.attach(img_logo)
            logging.info(f"Da nhung logo '{LOGO_FILE}' vao email.")
    except FileNotFoundError:
        logging.warning(f"Khong tim thay file logo tai '{LOGO_FILE}'.")
    
    if network_diagram_path and os.path.exists(network_diagram_path):
        try:
            with open(network_diagram_path, 'rb') as f:
                img_diagram = MIMEImage(f.read())
                img_diagram.add_header('Content-ID', '<network_diagram>')
                msg_related.attach(img_diagram)
                logging.info(f"Da nhung so do mang '{network_diagram_path}' vao email.")
        except Exception as e:
            logging.error(f"Loi khi nhung so do mang: {e}")
    else:
        if network_diagram_path:
            logging.warning(f"Khong tim thay file so do mang tai '{network_diagram_path}'.")

    msg.attach(msg_related)

    if config.getboolean('Attachments', 'AttachContextFiles', fallback=False):
        if config.has_section('Bonus_Context'):
            for key, file_path in config.items('Bonus_Context'):
                if os.path.exists(file_path):
                    try:
                        with open(file_path, 'rb') as attachment:
                            part = MIMEApplication(attachment.read(), Name=os.path.basename(file_path))
                        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
                        msg.attach(part)
                        logging.info(f"Da dinh kem file context '{file_path}' vao email.")
                    except Exception as e:
                        logging.error(f"Loi khi dinh kem file '{file_path}': {e}")
                else:
                    logging.warning(f"File context de dinh kem '{file_path}' khong ton tai.")

    # Gui email
    try:
        server = smtplib.SMTP(config.get('Email', 'SMTPServer'), config.getint('Email', 'SMTPPort'))
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_emails_list, msg.as_string())
        server.quit()
        logging.info("Email da duoc gui thanh cong!")
    except Exception as e:
        logging.error(f"Loi khi gui email: {e}")


def read_bonus_context_files(config):
    context_parts = []
    if not config.has_section('Bonus_Context'):
        return "Không có thông tin bối cảnh bổ sung nào được cung cấp."
    for key, file_path in config.items('Bonus_Context'):
        file_path = file_path.strip()
        if os.path.exists(file_path):
            try:
                logging.info(f"Dang doc file boi canh: '{file_path}'")
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    file_name = os.path.basename(file_path)
                    context_parts.append(f"--- START OF FILE: {file_name} ---\n{content}\n--- END OF FILE: {file_name} ---")
            except Exception as e:
                logging.error(f"Loi khi doc file boi canh '{file_path}': {e}")
        else:
            logging.warning(f"File boi canh '{file_path}' khong ton tai. Bo qua.")
    return "\n\n".join(context_parts) if context_parts else "Không có thông tin bối cảnh bổ sung nào được cung cấp."

def run_analysis_cycle():
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
    
    logs_content, start_time_obj, end_time_obj = read_new_log_entries(log_file, hours, timezone)
    if logs_content is None:
        logging.error("Khong the tiep tuc do khong doc duoc file log.")
        return

    bonus_context_content = read_bonus_context_files(config)
    analysis_result_raw = analyze_logs_with_gemini(logs_content, bonus_context_content, gemini_api_key)

    summary_data = {"total_blocked_events": "N/A", "top_blocked_source_ip": "N/A", "alerts_count": "N/A"}
    analysis_markdown = analysis_result_raw

    try:
        json_match = re.search(r'```json\n(.*?)\n```', analysis_result_raw, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
            summary_data = json.loads(json_str)
            analysis_markdown = analysis_result_raw.replace(json_match.group(0), "").strip()
    except Exception as e:
        logging.warning(f"Khong the phan tich JSON tom tat tu AI: {e}")

    email_subject = f"Báo cáo Log pfSense [{hostname}] - {datetime.now(pytz.timezone(timezone)).strftime('%Y-%m-%d %H:%M')}"
    
    try:
        with open(EMAIL_TEMPLATE_FILE, 'r', encoding='utf-8') as f:
            email_template = f.read()
        
        analysis_html = markdown.markdown(analysis_markdown)
        time_format = '%H:%M:%S %d-%m-%Y'
        start_time_str = start_time_obj.strftime(time_format)
        end_time_str = end_time_obj.strftime(time_format)

        email_body_html = email_template.format(
            hostname=hostname,
            analysis_result=analysis_html,
            total_blocked=summary_data.get("total_blocked_events", "N/A"),
            top_ip=summary_data.get("top_blocked_source_ip", "N/A"),
            critical_alerts=summary_data.get("alerts_count", "N/A"),
            start_time=start_time_str,
            end_time=end_time_str
        )
        send_email(email_subject, email_body_html, config)
    except FileNotFoundError:
        logging.error(f"Loi: Khong tim thay file email template '{EMAIL_TEMPLATE_FILE}'. Email se khong duoc gui.")
    except Exception as e:
        logging.error(f"Loi khi tao noi dung email: {e}")

    logging.info("Hoan tat chu ky phan tich.")

def main():
    while True:
        run_analysis_cycle()
        interval_seconds = 86400
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