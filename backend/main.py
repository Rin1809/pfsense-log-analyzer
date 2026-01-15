
import os
import smtplib
import logging
import configparser
import markdown
import pytz
import time
import json
import re
import glob
import concurrent.futures
from datetime import datetime

from modules import state_manager
from modules import log_reader
from modules import gemini_analyzer
from modules import email_service
from modules import report_generator
from modules import context_loader
from modules import utils

CONFIG_FILE = "config.ini"
SYSTEM_SETTINGS_FILE = "system_settings.ini"

# // Default fallback
DEFAULT_CHUNK_SIZE = 6000

LOGGING_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)

def get_smtp_config_for_stage(system_settings, host_config, host_section, stage_config=None):
    host_profile = host_config.get(host_section, 'smtp_profile', fallback='').strip()
    system_default = system_settings.get('System', 'active_smtp_profile', fallback=None)
    profile_to_use = host_profile if host_profile else system_default

    if not profile_to_use: return None
    email_sec = f"Email_{profile_to_use}"
    if not system_settings.has_section(email_sec): return None
    return dict(system_settings.items(email_sec))

def get_attachments(config, host_section, system_settings):
    if not system_settings.getboolean('System', 'attach_context_files', fallback=False):
        return []
    
    standard_keys = ['syshostname', 'logfile', 'hourstoanalyze', 'timezone', 'run_interval_seconds', 'geminiapikey', 'networkdiagram', 'enabled', 'smtp_profile', 'pipeline_config', 'chunk_size']
    attachments = []
    for key in config.options(host_section):
        if key not in standard_keys and not key.startswith('context_file_'):
             val = config.get(host_section, key).strip()
             if val and os.path.isfile(val): attachments.append(val)
        if key.startswith('context_file_'):
             val = config.get(host_section, key).strip()
             if val and os.path.isfile(val): attachments.append(val)
             
    return attachments

def resolve_api_key_with_alias(raw_key, system_settings):
    if not raw_key: return ("", "Empty")
    
    clean_key = raw_key.strip()
    if not clean_key: return ("", "Empty")

    if clean_key.startswith('profile:'):
        profile_name = clean_key.split(':', 1)[1].strip()
        if system_settings.has_section('Gemini_Keys'):
            real_key = system_settings.get('Gemini_Keys', profile_name, fallback=clean_key).strip()
            return (real_key, f"Profile: {profile_name}")
        return (clean_key, f"Unknown Profile: {profile_name}")
    
    masked = clean_key[:4] + "..." + clean_key[-4:] if len(clean_key) > 8 else "Raw Key"
    return (clean_key, f"Key: {masked}")

# --- WORKER FUNCTION (Executes inside thread) ---
def process_chunk_worker(worker_config, chunk_content, host_section, bonus_context_text, binary_files, system_settings, prompt_dir, test_mode=False):
    """
    Worker function to process a log chunk with internal Retry Logic (3 times).
    """
    worker_name = worker_config.get('name', 'Worker')
    model_name = worker_config.get('model')
    prompt_file_name = worker_config.get('prompt_file')
    raw_key = worker_config.get('gemini_api_key')
    
    api_key, key_alias = resolve_api_key_with_alias(raw_key, system_settings)
    prompt_file = os.path.join(prompt_dir, prompt_file_name)
    
    logging.info(f"[{host_section}] Worker '{worker_name}' processing {len(chunk_content)} chars...")
    
    # // RETRY LOGIC (3 Times)
    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
            result = gemini_analyzer.analyze_with_gemini(
                f"{host_section}_{worker_name}", 
                chunk_content, 
                bonus_context_text, 
                api_key, 
                prompt_file, 
                model_name,
                key_alias=key_alias,
                test_mode=test_mode,
                context_file_paths=binary_files
            )
            
            # // Check for fatal errors in string response
            if "Gemini blocked response" in result or "Fatal Gemini Error" in result:
                raise Exception(f"AI Error: {result}")
            
            return {
                "worker": worker_name,
                "result": result,
                "status": "success"
            }
        except Exception as e:
            last_error = e
            logging.warning(f"[{host_section}] Worker '{worker_name}' failed attempt {attempt+1}/{max_retries}: {e}")
            time.sleep(2) # Backoff nhe

    logging.error(f"[{host_section}] Worker '{worker_name}' FAILED after {max_retries} attempts.")
    return {
        "worker": worker_name,
        "result": f"Worker Failed: {str(last_error)}",
        "status": "failed"
    }

# --- PIPELINE EXECUTION ---

def run_pipeline_stage_0(host_config, host_section, stage_config, main_raw_api_key, system_settings, test_mode=False):
    stage_name = stage_config.get('name', 'Periodic')
    substages = stage_config.get('substages', [])
    summary_conf = stage_config.get('summary_conf') or {}
    
    reduce_name = summary_conf.get('name') 
    if not reduce_name: reduce_name = f"{stage_name}_Reduce"

    logging.info(f"[{host_section}] >>> Running Stage 0: {stage_name}")

    log_file = host_config.get(host_section, 'LogFile')
    hours = host_config.getint(host_section, 'HoursToAnalyze', fallback=24)
    hostname = host_config.get(host_section, 'SysHostname')
    timezone = host_config.get(host_section, 'TimeZone', fallback='UTC')
    
    try:
        chunk_size = host_config.getint(host_section, 'chunk_size')
    except (configparser.NoOptionError, ValueError):
        chunk_size = host_config.getint(host_section, 'ChunkSize', fallback=DEFAULT_CHUNK_SIZE)
    
    logging.info(f"[{host_section}] Using Chunk Size: {chunk_size}")
    
    report_dir = system_settings.get('System', 'report_directory', fallback='reports')
    prompt_dir = system_settings.get('System', 'prompt_directory', fallback='prompts')
    logo_path = system_settings.get('System', 'logo_path', fallback=None)

    total_workers_available = 1 + len(substages)
    total_capacity_lines = chunk_size * total_workers_available
    
    read_result = log_reader.read_new_log_entries(log_file, hours, timezone, host_section, test_mode, custom_limit=total_capacity_lines)
    
    if not read_result or read_result[0] is None:
        logging.error(f"[{host_section}] Log read failed. Aborting.")
        return False

    full_log_content, start_time, end_time, log_count, candidate_timestamp = read_result

    if log_count == 0:
        logging.info(f"[{host_section}] No new logs. Advancing timestamp.")
        state_manager.save_last_run_timestamp(candidate_timestamp, host_section, test_mode)
        return True

    bonus_context_text, binary_files = context_loader.read_bonus_context_files(host_config, host_section)

    log_lines = full_log_content.splitlines()
    chunks = [log_lines[i:i + chunk_size] for i in range(0, len(log_lines), chunk_size)]
    
    logging.info(f"[{host_section}] Total Logs: {log_count} lines. Available Workers: {total_workers_available}. Split into {len(chunks)} chunks.")

    active_workers_payload = []

    stage_specific_key_raw = stage_config.get('gemini_api_key')
    final_main_key_raw = stage_specific_key_raw if stage_specific_key_raw and stage_specific_key_raw.strip() else main_raw_api_key

    if len(chunks) > 0:
        chunk_str = "\n".join(chunks[0])
        active_workers_payload.append({
            "config": {
                "name": stage_name, 
                "model": stage_config.get('model'),
                "prompt_file": stage_config.get('prompt_file'),
                "gemini_api_key": final_main_key_raw
            },
            "content": chunk_str
        })


    for i in range(1, len(chunks)):
        sub_idx = i - 1
        if sub_idx < len(substages):
            sub_conf = substages[sub_idx]
            if sub_conf.get('enabled', True):
                chunk_str = "\n".join(chunks[i])
                active_workers_payload.append({
                    "config": sub_conf,
                    "content": chunk_str
                })
        else:
            pass 

    if not active_workers_payload:
        logging.warning(f"[{host_section}] No chunks to process.")
        return False

    logging.info(f"[{host_section}] >>> Parallel Execution: {len(active_workers_payload)} tasks.")

    successful_results = []
    failed_workers = []
    
    is_multi_worker_run = len(active_workers_payload) > 1

    def _execute_task(task_payload):
        return process_chunk_worker(
            task_payload['config'],
            task_payload['content'],
            host_section,
            bonus_context_text,
            binary_files,
            system_settings,
            prompt_dir,
            test_mode
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_worker = {
            executor.submit(_execute_task, task): task['config']['name'] 
            for task in active_workers_payload
        }

        for future in concurrent.futures.as_completed(future_to_worker):
            worker_name = future_to_worker[future]
            try:
                data = future.result()
                
                worker_stats = utils.extract_json_from_text(data['result'])
                worker_md = re.sub(r'```json\s*.*?\s*```', '', data['result'], flags=re.DOTALL | re.IGNORECASE).strip()
                
                worker_report_data = {
                    "hostname": hostname,
                    "worker_name": worker_name,
                    "analysis_start_time": start_time.isoformat(),
                    "analysis_end_time": end_time.isoformat(),
                    "report_generated_time": datetime.now(pytz.timezone(timezone)).isoformat(),
                    "summary_stats": worker_stats,
                    "analysis_details_markdown": worker_md,
                    "stage_index": 0,
                    "report_type": worker_name,
                    "raw_log_count": log_count if not is_multi_worker_run else 0 
                }

                report_generator.save_structured_report(host_section, worker_report_data, timezone, report_dir, worker_name)
                
                if data['status'] == 'success':
                    successful_results.append(data)
                    logging.info(f"[{host_section}] Worker '{worker_name}' SUCCESS.")
                else:
                    failed_workers.append(worker_name)
                    logging.error(f"[{host_section}] Worker '{worker_name}' FAILED.")

            except Exception as exc:
                logging.error(f"[{host_section}] Thread execution failed for '{worker_name}': {exc}")
                failed_workers.append(worker_name)

    if not successful_results:
        logging.error(f"[{host_section}] ALL Workers failed. Aborting pipeline.")
        return False

    # --- REDUCE STEP LOGIC ---
    final_markdown = ""
    final_stats = {}
    final_report_type = stage_name 
    
    if len(successful_results) == 1 and not failed_workers and not is_multi_worker_run:
        logging.info(f"[{host_section}] Single chunk. No Reduce needed.")
        raw_text = successful_results[0]['result']
        final_stats = utils.extract_json_from_text(raw_text)
        final_markdown = re.sub(r'```json\s*.*?\s*```', '', raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
        final_report_type = stage_name 
        
    else:
        logging.info(f"[{host_section}] >>> Running Reduce '{reduce_name}' for {len(successful_results)} results...")
        
        combined_inputs = []
        successful_results.sort(key=lambda x: x['worker'] != stage_name) 

        for res in successful_results:
            combined_inputs.append(f"--- ANALYSIS PART FROM {res['worker']} ---\n{res['result']}")
        
        if failed_workers:
             combined_inputs.append(f"--- WARNING ---\nThe following workers failed to process their chunks: {failed_workers}. This report is based on partial data.")

        full_combined_text = "\n\n".join(combined_inputs)

        reduce_model = summary_conf.get('model') or stage_config.get('model')
        reduce_prompt_file_name = summary_conf.get('prompt_file') or 'summary_prompt_template.md'
        reduce_prompt_file = os.path.join(prompt_dir, reduce_prompt_file_name)
        
        reduce_key_raw = summary_conf.get('gemini_api_key')
        if not reduce_key_raw: reduce_key_raw = main_raw_api_key
        
        reduce_api_key, reduce_alias = resolve_api_key_with_alias(reduce_key_raw, system_settings)

        # // RETRY LOGIC FOR REDUCE
        reduce_result = "Fatal Gemini Error: Init"
        for att in range(3):
            try:
                reduce_result = gemini_analyzer.analyze_with_gemini(
                    f"{host_section}_Reduce",
                    full_combined_text,
                    bonus_context_text,
                    reduce_api_key, 
                    reduce_prompt_file,
                    reduce_model,
                    key_alias=reduce_alias,
                    test_mode=test_mode,
                    context_file_paths=binary_files
                )
                if "Gemini blocked response" in reduce_result or "Fatal Gemini Error" in reduce_result:
                     raise Exception(reduce_result)
                break 
            except Exception as e:
                logging.warning(f"[{host_section}] Reduce failed attempt {att+1}: {e}")
                time.sleep(2)

        if "Gemini blocked response" in reduce_result or "Fatal Gemini Error" in reduce_result:
            logging.error(f"[{host_section}] Reduce Failed.")
            final_stats = {} 
            final_markdown = "## AUTO-GENERATED CONCATENATION (AI REDUCE FAILED)\n\n" + full_combined_text
        else:
            final_stats = utils.extract_json_from_text(reduce_result)
            final_markdown = re.sub(r'```json\s*.*?\s*```', '', reduce_result, flags=re.DOTALL | re.IGNORECASE).strip()

        # SAVE REDUCE REPORT
        final_report_type = reduce_name
        reduce_report_data = {
            "hostname": hostname,
            "analysis_start_time": start_time.isoformat(),
            "analysis_end_time": end_time.isoformat(),
            "report_generated_time": datetime.now(pytz.timezone(timezone)).isoformat(),
            "raw_log_count": log_count, 
            "summary_stats": final_stats,
            "analysis_details_markdown": final_markdown,
            "stage_index": 0,
            "parallel_workers_active": len(active_workers_payload),
            "failed_workers": failed_workers,
            "report_type": reduce_name
        }
        report_generator.save_structured_report(host_section, reduce_report_data, timezone, report_dir, reduce_name)


    # Finalize Timestamp
    state_manager.save_last_run_timestamp(candidate_timestamp, host_section, test_mode)
    
    # --- EMAIL SENDING ---
    recipient_emails = stage_config.get('recipient_emails', '')
    if recipient_emails:
        custom_subject = stage_config.get('email_subject', '').strip()
        email_subject = f"{custom_subject if custom_subject else f'[{final_report_type}]'} - {hostname} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        smtp = get_smtp_config_for_stage(system_settings, host_config, host_section)
        if smtp:
            try:
                # 1. Custom Template
                selected_template = stage_config.get('email_template')
                if selected_template and os.path.exists(selected_template):
                     tpl_path = selected_template
                else:
                     # 2. Default Fallback for Stage 0 (Periodic)
                     tpl_path = os.path.join(prompt_dir, '..', 'email_template.html')
                     if not os.path.exists(tpl_path): tpl_path = 'email_template.html'

                with open(tpl_path, 'r', encoding='utf-8') as f: tpl = f.read()
                
                diag = host_config.get(host_section, 'NetworkDiagram', fallback=None)
                if not diag or not os.path.exists(diag):
                     tpl = tpl.replace('id="network-diagram-card"', 'id="network-diagram-card" style="display: none;"')
                else:
                     tpl = tpl.replace('id="network-diagram-card" style="display: none;"', 'id="network-diagram-card"')
                
                # --- MAPPING GENERIC METRICS ---
                body = tpl.format(
                    hostname=hostname, 
                    analysis_result=markdown.markdown(final_markdown),
                    stat_1_label=final_stats.get("stat_1_label", "Metric 1"),
                    stat_1_value=final_stats.get("stat_1_value", "N/A"),
                    stat_2_label=final_stats.get("stat_2_label", "Metric 2"),
                    stat_2_value=final_stats.get("stat_2_value", "N/A"),
                    stat_3_label=final_stats.get("stat_3_label", "Metric 3"),
                    stat_3_value=final_stats.get("stat_3_value", "N/A"),
                    short_summary=final_stats.get("short_summary", "Không có tóm tắt"),
                    start_time=start_time.strftime('%H:%M %d-%m'), 
                    end_time=end_time.strftime('%H:%M %d-%m'),
                    security_trend=final_stats.get("stat_1_value", "N/A"), # Fallback mapping for Final template
                    key_recommendation=final_stats.get("stat_2_value", "N/A"),
                    total_events=final_stats.get("stat_3_value", "N/A")
                )
                
                atts = get_attachments(host_config, host_section, system_settings)
                email_service.send_email(host_section, email_subject, body, smtp, recipient_emails, diag, atts, logo_path=logo_path)
            except Exception as e: logging.error(f"Email failed: {e}")

    return True

def run_pipeline_stage_n(host_config, host_section, current_stage_idx, stage_config, prev_stage_config, main_raw_api_key, system_settings, test_mode=False, is_last_stage=False):

    stage_name = stage_config.get('name', f'Stage_{current_stage_idx}')
    threshold = int(stage_config.get('trigger_threshold', 10))
    
    logging.info(f"[{host_section}] >>> Checking trigger for Stage {current_stage_idx} ({stage_name}). Need {threshold} reports.")

    report_dir = system_settings.get('System', 'report_directory', fallback='reports')
    host_report_dir = os.path.join(report_dir, host_section)
    
    prev_stage_name = prev_stage_config.get('name', f'Stage_{current_stage_idx-1}')
    prev_stage_slug = report_generator.slugify(prev_stage_name)
    
    possible_folders = [prev_stage_slug]
    
    reduce_name = None 
    
    if current_stage_idx == 1:
        prev_summary_conf = prev_stage_config.get('summary_conf') or {}
        reduce_name = prev_summary_conf.get('name') or f"{prev_stage_name}_Reduce"
        possible_folders.append(report_generator.slugify(reduce_name))

    valid_reports = []
    
    def is_match(json_type, config_name):
        if not json_type or not config_name: return False
        if json_type == config_name: return True
        if json_type == report_generator.slugify(config_name): return True
        return False

    for folder in possible_folders:
        search_pattern = os.path.join(host_report_dir, folder, "*", "*.json")
        found_files = glob.glob(search_pattern, recursive=True)
        for p in found_files:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    r_type = d.get('report_type')
                    
                    # [FIX] Use robust comparison
                    match_prev = is_match(r_type, prev_stage_name)
                    match_reduce = is_match(r_type, reduce_name) if reduce_name else False
                    
                    if match_prev or match_reduce:
                        if p not in valid_reports: valid_reports.append(p)
            except Exception as e: 
                logging.debug(f"[{host_section}] Error checking report {p}: {e}")
                pass
            
    valid_reports.sort(key=os.path.getmtime, reverse=False)

    reports_to_process = valid_reports[:threshold]
    
    if len(reports_to_process) < threshold and not test_mode:
        logging.info(f"[{host_section}] Not enough reports ({len(reports_to_process)}/{threshold}). Waiting.")
        return False
        
    if not reports_to_process: return False

    logging.info(f"[{host_section}] Aggregating {len(reports_to_process)} reports.")

    combined_analysis, start_time, end_time = [], None, None
    for path in reports_to_process:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                combined_analysis.append(f"--- REPORT ({data['analysis_start_time']} -> {data['analysis_end_time']}) ---\n{data['analysis_details_markdown']}")
                st = datetime.fromisoformat(data['analysis_start_time'])
                et = datetime.fromisoformat(data['analysis_end_time'])
                if not start_time or st < start_time: start_time = st
                if not end_time or et > end_time: end_time = et
        except: pass
        
    prompt_file = os.path.join(system_settings.get('System', 'prompt_directory', fallback='prompts'), stage_config.get('prompt_file', 'summary_prompt_template.md'))
    model_name = stage_config.get('model', 'gemini-2.5-flash-lite')
    hostname = host_config.get(host_section, 'SysHostname')
    timezone = host_config.get(host_section, 'TimeZone')
    logo_path = system_settings.get('System', 'logo_path', fallback=None)

    content_to_analyze = "\n\n".join(combined_analysis)

    bonus_context_text, binary_files = context_loader.read_bonus_context_files(host_config, host_section)
    
    stage_key_raw = stage_config.get('gemini_api_key')
    final_key_raw = stage_key_raw if stage_key_raw and stage_key_raw.strip() else main_raw_api_key
    final_api_key, key_alias = resolve_api_key_with_alias(final_key_raw, system_settings)

    result_raw = gemini_analyzer.analyze_with_gemini(
        host_section, content_to_analyze, bonus_context_text, 
        final_api_key, prompt_file, model_name,
        key_alias=key_alias, test_mode=test_mode,
        context_file_paths=binary_files
    )
    
    if "Gemini blocked response" in result_raw or "Fatal Gemini Error" in result_raw:
        logging.error(f"[{host_section}] Stage {current_stage_idx} AI Failed. Not saving.")
        return False

    stats = utils.extract_json_from_text(result_raw)
    result_md = re.sub(r'```json\s*.*?\s*```', '', result_raw, flags=re.DOTALL | re.IGNORECASE).strip()
    
    report_data = {
        "hostname": hostname, 
        "analysis_start_time": start_time.isoformat() if start_time else "N/A",
        "analysis_end_time": end_time.isoformat() if end_time else "N/A",
        "report_generated_time": datetime.now(pytz.timezone(timezone)).isoformat(),
        "summary_stats": stats, 
        "analysis_details_markdown": result_md,
        "source_reports": reports_to_process,
        "stage_index": current_stage_idx,
        "report_type": stage_name
    }
    
    report_generator.save_structured_report(host_section, report_data, timezone, report_dir, stage_name)
    
    recipients = stage_config.get('recipient_emails', '')
    if recipients:
        custom_subject = stage_config.get('email_subject', '').strip()
        email_subject = f"{custom_subject if custom_subject else f'[{stage_name}]'} - {hostname} - {datetime.now().strftime('%Y-%m-%d')}"

        smtp = get_smtp_config_for_stage(system_settings, host_config, host_section)
        if smtp:
            try:
                # --- TEMPLATE SELECTION LOGIC ---
                # 1. Custom Template User Selected
                selected_template = stage_config.get('email_template')
                if selected_template and os.path.exists(selected_template):
                    tpl_path = selected_template
                else:
                    # 2. Auto Select based on Stage
                    backend_root = os.path.dirname(os.path.abspath(__file__))
                    
                    if is_last_stage:
                        # Priority: final_summary > summary > email
                        p1 = os.path.join(backend_root, 'final_summary_email_template.html')
                        p2 = os.path.join(backend_root, 'summary_email_template.html')
                        p3 = os.path.join(backend_root, 'email_template.html')
                        
                        if os.path.exists(p1): tpl_path = p1
                        elif os.path.exists(p2): tpl_path = p2
                        else: tpl_path = p3
                    else:
                        # Priority: summary > email
                        p2 = os.path.join(backend_root, 'summary_email_template.html')
                        p3 = os.path.join(backend_root, 'email_template.html')
                        
                        if os.path.exists(p2): tpl_path = p2
                        else: tpl_path = p3

                with open(tpl_path, 'r', encoding='utf-8') as f: tpl = f.read()
                
                diag = host_config.get(host_section, 'NetworkDiagram', fallback=None)
                if not diag or not os.path.exists(diag):
                     tpl = tpl.replace('id="network-diagram-card"', 'id="network-diagram-card" style="display: none;"')
                else:
                     tpl = tpl.replace('id="network-diagram-card" style="display: none;"', 'id="network-diagram-card"')

                body = tpl.format(
                    hostname=hostname, 
                    analysis_result=markdown.markdown(result_md),
                    stat_1_label=stats.get("stat_1_label", "Metric 1"),
                    stat_1_value=stats.get("stat_1_value", "N/A"),
                    stat_2_label=stats.get("stat_2_label", "Metric 2"),
                    stat_2_value=stats.get("stat_2_value", "N/A"),
                    stat_3_label=stats.get("stat_3_label", "Metric 3"),
                    stat_3_value=stats.get("stat_3_value", "N/A"),
                    short_summary=stats.get("short_summary", "Không có tóm tắt"),
                    start_time=start_time.strftime('%d-%m') if start_time else "?", 
                    end_time=end_time.strftime('%d-%m') if end_time else "?",

                    security_trend=stats.get("stat_1_value", "N/A"),
                    key_recommendation=stats.get("stat_2_value", "N/A"),
                    total_events=stats.get("stat_3_value", "N/A")
                )
                email_service.send_email(host_section, email_subject, body, smtp, recipients, diag, reports_to_process, logo_path=logo_path)
            except Exception as e: logging.error(f"Email error {stage_name}: {e}")


    # Mark processed files to prevent infinite loop
    for r_path in reports_to_process:
        try:
            new_path = r_path + ".processed"
            os.rename(r_path, new_path)
            logging.info(f"[{host_section}] Marked processed: {os.path.basename(r_path)}")
        except Exception as e:
            logging.warning(f"[{host_section}] Failed to mark processed {r_path}: {e}")

    return True

def process_host_pipeline(host_config, host_section, system_settings, test_mode=False):
    pipeline_json = host_config.get(host_section, 'pipeline_config', fallback='[]')
    try: pipeline = json.loads(pipeline_json)
    except: return

    if not pipeline: return
        
    main_raw_api_key = host_config.get(host_section, 'GeminiAPIKey', fallback='')
    if not main_raw_api_key or "YOUR_API_KEY" in main_raw_api_key: return

    now = datetime.now()
    stage0_config = pipeline[0]
    if stage0_config.get('enabled', True):
        run_interval = host_config.getint(host_section, 'run_interval_seconds', fallback=3600)
        last_run = state_manager.get_last_cycle_run_timestamp(host_section, test_mode)
        
        if not last_run or (now - last_run).total_seconds() >= run_interval:
            success = run_pipeline_stage_0(host_config, host_section, stage0_config, main_raw_api_key, system_settings, test_mode)
            if success:
                state_manager.save_last_cycle_run_timestamp(now, host_section, test_mode)
                if len(pipeline) > 1:
                    curr_buff = state_manager.get_stage_buffer_count(host_section, 1, test_mode)
                    state_manager.save_stage_buffer_count(host_section, 1, curr_buff + 1, test_mode)

    total_stages = len(pipeline)
    for i in range(1, total_stages):
        current_stage = pipeline[i]
        if not current_stage.get('enabled', True): continue
        prev_stage = pipeline[i-1]
        threshold = int(current_stage.get('trigger_threshold', 10))
        current_buffer = state_manager.get_stage_buffer_count(host_section, i, test_mode)
        
        if current_buffer >= threshold:
            is_last = (i == total_stages - 1)
            success = run_pipeline_stage_n(host_config, host_section, i, current_stage, prev_stage, main_raw_api_key, system_settings, test_mode, is_last_stage=is_last)
            if success:
                state_manager.save_stage_buffer_count(host_section, i, 0, test_mode)
                if i + 1 < len(pipeline):
                    next_buff = state_manager.get_stage_buffer_count(host_section, i+1, test_mode)
                    state_manager.save_stage_buffer_count(host_section, i+1, next_buff + 1, test_mode)

def main():
    while True:
        try:
            sys_conf = configparser.ConfigParser(interpolation=None); sys_conf.read(SYSTEM_SETTINGS_FILE)
            host_conf = configparser.ConfigParser(interpolation=None); host_conf.read(CONFIG_FILE)
            
            for sec in [s for s in host_conf.sections() if s.startswith(('Firewall_', 'Host_'))]:
                if host_conf.getboolean(sec, 'enabled', fallback=True):
                    process_host_pipeline(host_conf, sec, sys_conf)
            time.sleep(sys_conf.getint('System', 'scheduler_check_interval_seconds', fallback=60))
        except Exception as e:
            logging.error(f"Main Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
