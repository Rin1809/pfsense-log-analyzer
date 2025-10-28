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
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
import glob

# --- Khai báo hằng số (Dùng làm giá trị mặc định/fallback) ---
CONFIG_FILE = "config.ini"
PROMPT_TEMPLATE_FILE = "prompt_template.md"
SUMMARY_PROMPT_TEMPLATE_FILE = "summary_prompt_template.md"
EMAIL_TEMPLATE_FILE = "email_template.html"
SUMMARY_EMAIL_TEMPLATE_FILE = "summary_email_template.html"
LOGO_FILE = "logo_novaon.png"


LOGGING_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)

# --- Các hàm quản lý trạng thá
def get_last_run_timestamp(firewall_id):
    """Đọc timestamp từ file state dành riêng cho một firewall."""
    state_file = f".last_run_timestamp_{firewall_id}"
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            try:
                return datetime.fromisoformat(f.read().strip())
            except ValueError:
                return None
    return None

def save_last_run_timestamp(timestamp, firewall_id):
    """Lưu timestamp vào file state dành riêng cho một firewall."""
    state_file = f".last_run_timestamp_{firewall_id}"
    with open(state_file, 'w') as f:
        f.write(timestamp.isoformat())

def get_summary_count(firewall_id):
    """Lấy số đếm báo cáo đã chạy cho một firewall."""
    summary_count_file = f".summary_report_count_{firewall_id}"
    if not os.path.exists(summary_count_file):
        return 0
    try:
        with open(summary_count_file, 'r') as f:
            return int(f.read().strip())
    except (ValueError, FileNotFoundError):
        return 0

def save_summary_count(count, firewall_id):
    """Lưu số đếm cho một firewall."""
    summary_count_file = f".summary_report_count_{firewall_id}"
    try:
        with open(summary_count_file, 'w') as f:
            f.write(str(count))
        logging.info(f"[{firewall_id}] Đã cập nhật file đếm: {summary_count_file} = {count}")
    except Exception as e:
        logging.error(f"[{firewall_id}] Lỗi khi lưu file đếm: {e}")

# --- Các hàm lõi 

def read_new_log_entries(file_path, hours, timezone_str, firewall_id):
    """Đọc các dòng log mới từ một file log cụ thể."""
    logging.info(f"[{firewall_id}] Bắt đầu đọc log từ '{file_path}'.")
    try:
        tz = pytz.timezone(timezone_str)
        end_time = datetime.now(tz)
        last_run_time = get_last_run_timestamp(firewall_id)

        if last_run_time:
            start_time = last_run_time.astimezone(tz)
            logging.info(f"[{firewall_id}] Đọc log kể từ lần chạy cuối: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            start_time = end_time - timedelta(hours=hours)
            logging.info(f"[{firewall_id}] Lần chạy đầu tiên. Đọc log trong vòng {hours} giờ qua.")

        new_entries = []
        latest_log_time = start_time
        current_year = end_time.year
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
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
            save_last_run_timestamp(latest_log_time, firewall_id)

        logging.info(f"[{firewall_id}] Tìm thấy {len(new_entries)} dòng log mới.")
        return ("".join(new_entries), start_time, end_time)

    except FileNotFoundError:
        logging.error(f"[{firewall_id}] Lỗi: Không tìm thấy file log tại '{file_path}'.")
        return (None, None, None)
    except Exception as e:
        logging.error(f"[{firewall_id}] Lỗi không mong muốn khi đọc file: {e}")
        return (None, None, None)

def analyze_logs_with_gemini(firewall_id, content, bonus_context, api_key, prompt_file):
    """Gửi yêu cầu phân tích tới Gemini."""
    if not content or not content.strip():
        logging.warning(f"[{firewall_id}] Nội dung trống, bỏ qua phân tích.")
        return "Không có dữ liệu nào để phân tích trong khoảng thời gian được chọn."

    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except FileNotFoundError:
        logging.error(f"[{firewall_id}] Lỗi: Không tìm thấy file template '{prompt_file}'.")
        return f"Lỗi hệ thống: Không tìm thấy file '{prompt_file}'."

    genai.configure(api_key=api_key)
    
    # Logic để xác định cách format prompt dựa trên tên file
    is_summary_prompt = 'summary' in os.path.basename(prompt_file).lower()
    if is_summary_prompt:
        prompt = prompt_template.format(reports_content=content, bonus_context=bonus_context)
    else:
        prompt = prompt_template.format(logs_content=content, bonus_context=bonus_context)

    try:
        logging.info(f"[{firewall_id}] Gửi yêu cầu đến Gemini (prompt: {prompt_file}, timeout 180 giây)...")
        model = genai.GenerativeModel('gemini-2.5-flash')
        request_options = {"timeout": 180}
        response = model.generate_content(prompt, request_options=request_options)
        logging.info(f"[{firewall_id}] Nhận phân tích từ Gemini thành công.")
        return response.text
    except google_exceptions.DeadlineExceeded:
        logging.error(f"[{firewall_id}] Lỗi: Yêu cầu đến Gemini bị hết thời gian chờ (timeout).")
        return "Không thể nhận phân tích từ Gemini do hết thời gian chờ."
    except Exception as e:
        logging.error(f"[{firewall_id}] Lỗi khi giao tiếp với Gemini: {e}")
        return f"Đã xảy ra lỗi khi phân tích log với Gemini: {e}"

def send_email(firewall_id, subject, body_html, config, recipient_emails_str, attachment_paths=None):
    """Gửi email báo cáo, hỗ trợ đính kèm file."""
    sender_email = config.get('Email', 'SenderEmail')
    sender_password = config.get('Email', 'SenderPassword')
    recipient_emails_list = [email.strip() for email in recipient_emails_str.split(',')]
    
    logging.info(f"[{firewall_id}] Chuẩn bị gửi email đến {recipient_emails_str}...")
    
    msg = MIMEMultipart('mixed')
    msg['From'] = sender_email
    msg['To'] = recipient_emails_str
    msg['Subject'] = subject

    msg_related = MIMEMultipart('related')
    
    network_diagram_path = config.get('Attachments', 'NetworkDiagram', fallback=None)
    if network_diagram_path and os.path.exists(network_diagram_path):
        body_html = body_html.replace('style="display: none;"', '')
    
    msg_related.attach(MIMEText(body_html, 'html'))
    
    # Nhúng logo và sơ đồ mạng
    try:
        with open(LOGO_FILE, 'rb') as f:
            img_logo = MIMEImage(f.read())
            img_logo.add_header('Content-ID', '<logo_novaon>')
            msg_related.attach(img_logo)
    except FileNotFoundError:
        logging.warning(f"[{firewall_id}] Không tìm thấy file logo '{LOGO_FILE}'.")
    
    if network_diagram_path and os.path.exists(network_diagram_path):
        try:
            with open(network_diagram_path, 'rb') as f:
                img_diagram = MIMEImage(f.read())
                img_diagram.add_header('Content-ID', '<network_diagram>')
                msg_related.attach(img_diagram)
        except Exception as e:
            logging.error(f"[{firewall_id}] Lỗi khi nhúng sơ đồ mạng: {e}")

    msg.attach(msg_related)
    
    if attachment_paths:
        for file_path in attachment_paths:
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'rb') as attachment:
                        part = MIMEApplication(attachment.read(), Name=os.path.basename(file_path))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
                    msg.attach(part)
                    logging.info(f"[{firewall_id}] Đã đính kèm file: '{file_path}'")
                except Exception as e:
                    logging.error(f"[{firewall_id}] Lỗi khi đính kèm file '{file_path}': {e}")
            else:
                logging.warning(f"[{firewall_id}] File đính kèm '{file_path}' không tồn tại.")

    # Gửi email
    try:
        server = smtplib.SMTP(config.get('Email', 'SMTPServer'), config.getint('Email', 'SMTPPort'))
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_emails_list, msg.as_string())
        server.quit()
        logging.info(f"[{firewall_id}] Email đã được gửi thành công!")
    except Exception as e:
        logging.error(f"[{firewall_id}] Lỗi khi gửi email: {e}")

def read_bonus_context_files(config, firewall_section):
    """Đọc tất cả các file bối cảnh được định nghĩa trong section của firewall."""
    context_parts = []
    
    standard_keys = ['pfsensehostname', 'logfile', 'hourstoanalyze', 'timezone', 
                     'reportdirectory', 'recipientemails', 'summary_enabled', 
                     'reports_per_summary', 'summary_recipient_emails',
                     'prompt_file', 'summary_prompt_file']
    context_keys = [key for key in config.options(firewall_section) if key not in standard_keys]

    if not context_keys:
        return "Không có thông tin bối cảnh bổ sung nào được cung cấp."
        
    for key in context_keys:
        file_path = config.get(firewall_section, key).strip()
        if os.path.exists(file_path):
            try:
                logging.info(f"[{firewall_section}] Đang đọc file bối cảnh: '{file_path}'")
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    file_name = os.path.basename(file_path)
                    context_parts.append(f"--- START OF FILE: {file_name} ---\n{content}\n--- END OF FILE: {file_name} ---")
            except Exception as e:
                logging.error(f"[{firewall_section}] Lỗi khi đọc file bối cảnh '{file_path}': {e}")
        else:
            logging.warning(f"[{firewall_section}] File bối cảnh '{file_path}' không tồn tại. Bỏ qua.")
            
    return "\n\n".join(context_parts) if context_parts else "Không có thông tin bối cảnh bổ sung nào được cung cấp."

def save_structured_report(firewall_id, report_data, timezone_str, base_report_dir, is_summary=False):
    """Lưu dữ liệu thô ra file JSON, có tổ chức theo thư mục."""
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        date_folder = now.strftime('%Y-%m-%d')
        time_filename = now.strftime('%H-%M-%S') + '.json'
        
        report_folder_path = os.path.join(base_report_dir, "summary", date_folder) if is_summary else os.path.join(base_report_dir, date_folder)
        os.makedirs(report_folder_path, exist_ok=True)
        
        report_file_path = os.path.join(report_folder_path, time_filename)

        with open(report_file_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=4)
        logging.info(f"[{firewall_id}] Đã lưu báo cáo JSON vào: '{report_file_path}'")
            
    except Exception as e:
        logging.error(f"[{firewall_id}] Lỗi khi lưu file JSON: {e}")

# --- hàm chu kỳ ---

def run_analysis_cycle(config, firewall_section):
    """Chạy một chu kỳ phân tích định kỳ cho một firewall cụ thể."""
    logging.info(f"[{firewall_section}] Bắt đầu chu kỳ phân tích log.")
    
    log_file = config.get(firewall_section, 'LogFile')
    hours = config.getint(firewall_section, 'HoursToAnalyze')
    hostname = config.get(firewall_section, 'PFSenseHostname')
    timezone = config.get(firewall_section, 'TimeZone')
    report_dir = config.get(firewall_section, 'ReportDirectory')
    recipient_emails = config.get(firewall_section, 'RecipientEmails')
    
    # Lấy đường dẫn prompt từ config, nếu không có thì dùng mặc định 
    prompt_file = config.get(firewall_section, 'prompt_file', fallback=PROMPT_TEMPLATE_FILE)
    
    gemini_api_key = config.get('Gemini', 'APIKey')
        
    if not gemini_api_key or "YOUR_API_KEY" in gemini_api_key:
        logging.error(f"[{firewall_section}] Lỗi: 'APIKey' chưa được thiết lập. Bỏ qua.")
        return
    
    logs_content, start_time, end_time = read_new_log_entries(log_file, hours, timezone, firewall_section)
    if logs_content is None:
        logging.error(f"[{firewall_section}] Không thể tiếp tục do lỗi đọc file log.")
        return

    bonus_context = read_bonus_context_files(config, firewall_section)
    #Truyền đường dẫn prompt đã lấy được vào hàm phân tích
    analysis_raw = analyze_logs_with_gemini(firewall_section, logs_content, bonus_context, gemini_api_key, prompt_file)

    summary_data = {"total_blocked_events": "N/A", "top_blocked_source_ip": "N/A", "alerts_count": "N/A"}
    analysis_markdown = analysis_raw
    try:
        json_match = re.search(r'```json\n(.*?)\n```', analysis_raw, re.DOTALL)
        if json_match:
            summary_data = json.loads(json_match.group(1))
            analysis_markdown = analysis_raw.replace(json_match.group(0), "").strip()
    except Exception as e:
        logging.warning(f"[{firewall_section}] Không thể trích xuất JSON: {e}")

    report_data = {
        "hostname": hostname, "analysis_start_time": start_time.isoformat(), "analysis_end_time": end_time.isoformat(),
        "report_generated_time": datetime.now(pytz.timezone(timezone)).isoformat(),
        "summary_stats": summary_data, "analysis_details_markdown": analysis_markdown
    }
    save_structured_report(firewall_section, report_data, timezone, report_dir)

    email_subject = f"Báo cáo Log pfSense [{hostname}] - {datetime.now(pytz.timezone(timezone)).strftime('%Y-%m-%d %H:%M')}"
    try:
        with open(EMAIL_TEMPLATE_FILE, 'r', encoding='utf-8') as f: email_template = f.read()
        analysis_html = markdown.markdown(analysis_markdown)
        email_body = email_template.format(
            hostname=hostname, analysis_result=analysis_html,
            total_blocked=summary_data.get("total_blocked_events", "N/A"),
            top_ip=summary_data.get("top_blocked_source_ip", "N/A"),
            critical_alerts=summary_data.get("alerts_count", "N/A"),
            start_time=start_time.strftime('%H:%M:%S %d-%m-%Y'),
            end_time=end_time.strftime('%H:%M:%S %d-%m-%Y')
        )
        
        attachments_to_send = []
        if config.getboolean('Attachments', 'AttachContextFiles', fallback=False):
            standard_keys = ['pfsensehostname', 'logfile', 'hourstoanalyze', 'timezone', 'reportdirectory', 'recipientemails', 'summary_enabled', 'reports_per_summary', 'summary_recipient_emails', 'prompt_file', 'summary_prompt_file']
            context_keys = [key for key in config.options(firewall_section) if key not in standard_keys]
            attachments_to_send = [config.get(firewall_section, key) for key in context_keys]

        send_email(firewall_section, email_subject, email_body, config, recipient_emails, attachment_paths=attachments_to_send)
    except Exception as e:
        logging.error(f"[{firewall_section}] Lỗi khi tạo/gửi email: {e}")

    logging.info(f"[{firewall_section}] Hoàn tất chu kỳ phân tích.")

def run_summary_analysis_cycle(config, firewall_section):
    """Chạy một chu kỳ phân tích TỔNG HỢP cho một firewall."""
    logging.info(f"[{firewall_section}] Bắt đầu chu kỳ phân tích TỔNG HỢP.")
    
    reports_per_summary = config.getint(firewall_section, 'reports_per_summary')
    report_dir = config.get(firewall_section, 'ReportDirectory')
    timezone = config.get(firewall_section, 'TimeZone')
    hostname = config.get(firewall_section, 'PFSenseHostname')
    recipient_emails = config.get(firewall_section, 'summary_recipient_emails')
    gemini_api_key = config.get('Gemini', 'APIKey')

    # Lấy đường dẫn summary prompt từ config
    summary_prompt_file = config.get(firewall_section, 'summary_prompt_file', fallback=SUMMARY_PROMPT_TEMPLATE_FILE)

    report_files_pattern = os.path.join(report_dir, "*", "*.json")
    all_reports = sorted(glob.glob(report_files_pattern), key=os.path.getmtime, reverse=True)
    
    reports_to_summarize = [r for r in all_reports if "summary" not in r][:reports_per_summary]
    if not reports_to_summarize:
        logging.warning(f"[{firewall_section}] Không tìm thấy file báo cáo nào để tổng hợp.")
        return

    logging.info(f"[{firewall_section}] Sẽ tổng hợp từ {len(reports_to_summarize)} báo cáo: {reports_to_summarize}")

    combined_analysis, start_time, end_time = [], None, None
    for report_path in reversed(reports_to_summarize):
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                combined_analysis.append(f"--- BÁO CÁO TỪ {data['analysis_start_time']} ĐẾN {data['analysis_end_time']} ---\n\n{data['analysis_details_markdown']}")
                
                s_time = datetime.fromisoformat(data['analysis_start_time'])
                e_time = datetime.fromisoformat(data['analysis_end_time'])
                if start_time is None or s_time < start_time: start_time = s_time
                if end_time is None or e_time > end_time: end_time = e_time
        except Exception as e:
            logging.error(f"[{firewall_section}] Lỗi khi đọc file '{report_path}': {e}")

    if not combined_analysis:
        logging.error(f"[{firewall_section}] Không thể đọc nội dung từ bất kỳ file nào.")
        return

    reports_content = "\n\n".join(combined_analysis)
    bonus_context = read_bonus_context_files(config, firewall_section)
    # đường dẫn summary prompt 
    summary_raw = analyze_logs_with_gemini(firewall_section, reports_content, bonus_context, gemini_api_key, summary_prompt_file)

    summary_data = {"total_blocked_events_period": "N/A", "most_frequent_issue": "N/A", "total_alerts_period": "N/A"}
    analysis_markdown = summary_raw
    try:
        json_match = re.search(r'```json\n(.*?)\n```', summary_raw, re.DOTALL)
        if json_match:
            summary_data = json.loads(json_match.group(1))
            analysis_markdown = summary_raw.replace(json_match.group(0), "").strip()
    except Exception as e:
        logging.warning(f"[{firewall_section}] Không thể trích xuất JSON tổng hợp: {e}")

    report_data = {
        "hostname": hostname, "analysis_start_time": start_time.isoformat() if start_time else "N/A",
        "analysis_end_time": end_time.isoformat() if end_time else "N/A",
        "report_generated_time": datetime.now(pytz.timezone(timezone)).isoformat(),
        "summary_stats": summary_data, "analysis_details_markdown": analysis_markdown,
        "summarized_files": reports_to_summarize
    }
    save_structured_report(firewall_section, report_data, timezone, report_dir, is_summary=True)

    email_subject = f"Báo cáo TỔNG HỢP Log pfSense [{hostname}] - {datetime.now(pytz.timezone(timezone)).strftime('%Y-%m-%d')}"
    try:
        with open(SUMMARY_EMAIL_TEMPLATE_FILE, 'r', encoding='utf-8') as f: email_template = f.read()
        analysis_html = markdown.markdown(analysis_markdown)
        email_body = email_template.format(
            hostname=hostname, analysis_result=analysis_html,
            total_blocked=summary_data.get("total_blocked_events_period", "N/A"),
            top_issue=summary_data.get("most_frequent_issue", "N/A"),
            critical_alerts=summary_data.get("total_alerts_period", "N/A"),
            start_time=start_time.strftime('%H:%M:%S %d-%m-%Y') if start_time else "N/A",
            end_time=end_time.strftime('%H:%M:%S %d-%m-%Y') if end_time else "N/A"
        )
        send_email(firewall_section, email_subject, email_body, config, recipient_emails, attachment_paths=reports_to_summarize)
    except Exception as e:
        logging.error(f"[{firewall_section}] Lỗi khi tạo/gửi email tổng hợp: {e}")

    logging.info(f"[{firewall_section}] Hoàn tất chu kỳ TỔNG HỢP.")


def main():
    while True:
        config = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(CONFIG_FILE):
            logging.error(f"Lỗi: File cấu hình '{CONFIG_FILE}' không tồn tại. Thoát.")
            return
        config.read(CONFIG_FILE)

        firewall_sections = [s for s in config.sections() if s.startswith('Firewall_')]
        if not firewall_sections:
            logging.warning("Không tìm thấy section firewall nào (ví dụ: [Firewall_...]) trong config.ini. Sẽ không có gì được thực thi.")
        else:
            logging.info(f"Phát hiện {len(firewall_sections)} firewall để xử lý: {firewall_sections}")

        for section in firewall_sections:
            logging.info(f"--- BẮT ĐẦU XỬ LÝ CHO FIREWALL: {section} ---")
            try:
                run_analysis_cycle(config, section)
                
                if config.getboolean(section, 'summary_enabled', fallback=False):
                    reports_per_summary = config.getint(section, 'reports_per_summary')
                    current_count = get_summary_count(section) + 1
                    
                    logging.info(f"[{section}] Đếm báo cáo tổng hợp: {current_count}/{reports_per_summary}")
                    
                    if current_count >= reports_per_summary:
                        logging.info(f"[{section}] Đạt ngưỡng, bắt đầu tạo báo cáo tổng hợp.")
                        run_summary_analysis_cycle(config, section)
                        save_summary_count(0, section)
                    else:
                        save_summary_count(current_count, section)
                else:
                    if os.path.exists(f".summary_report_count_{section}"):
                        save_summary_count(0, section)

            except Exception as e:
                logging.error(f"Lỗi nghiêm trọng khi xử lý firewall '{section}': {e}", exc_info=True)
            logging.info(f"--- KẾT THÚC XỬ LÝ CHO FIREWALL: {section} ---")
        
        interval_seconds = config.getint('System', 'RunIntervalSeconds', fallback=3600)
        logging.info(f"Tất cả các firewall đã được xử lý. Chu kỳ tiếp theo sẽ bắt đầu sau {interval_seconds} giây.")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    main()