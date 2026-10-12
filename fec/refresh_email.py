# -*- coding: utf-8 -*-
"""Best-effort completion email for weekly_refresh.py."""
import datetime
import os
import smtplib
import ssl
from email.message import EmailMessage

_HOST='smtp.mail.me.com'
_PORT=587
_TO='pgoff22@icloud.com'
_SKIPPED=('EMAIL REPORT SKIPPED - create fec\\icloud_smtp.txt: line1 apple-id, '
          'line2 app-specific password from appleid.apple.com, line3 from-address')

def _log(log_path,msg):
    line='[%s] %s'%(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),msg)
    try: print(line)
    except BaseException: pass
    try:
        with open(log_path,'a',encoding='utf-8') as f: f.write(line+'\n')
    except BaseException: pass

def _credentials():
    path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'icloud_smtp.txt')
    try:
        with open(path,encoding='utf-8-sig') as f: lines=f.read().splitlines()
    except (OSError,UnicodeError):
        return None
    if len(lines)!=3:
        return None
    apple_id,password,from_address=(line.strip() for line in lines)
    if not apple_id or not password or not from_address or '@' not in apple_id or '@' not in from_address:
        return None
    return apple_id,password,from_address

def _run_lines(log_path):
    with open(log_path,encoding='utf-8',errors='replace') as f: lines=f.read().splitlines()
    marker='===== WEEKLY REFRESH START ====='
    for i in range(len(lines)-1,-1,-1):
        if marker in lines[i]:
            return lines[i:]
    return []

def _duration(started_at,finished_at):
    seconds=max(0,int((finished_at-started_at).total_seconds()))
    hours,seconds=divmod(seconds,3600)
    minutes,seconds=divmod(seconds,60)
    return '%02d:%02d:%02d'%(hours,minutes,seconds)

def send_report(log_path,started_at,status_word) -> bool:
    """Send the current run's log and return True; never raise on failure."""
    try:
        credentials=_credentials()
        if credentials is None:
            _log(log_path,_SKIPPED)
            return False
        apple_id,password,from_address=credentials
        tz=getattr(started_at,'tzinfo',None)
        finished_at=datetime.datetime.now(tz) if tz is not None else datetime.datetime.now()
        status=str(status_word).upper()
        if status not in ('SUCCESS','FAILED'): status='FAILED'
        lines=_run_lines(log_path)
        rank_line=next((line for line in reversed(lines) if 'rank movement' in line.lower()),None)
        failures_line=next((line for line in reversed(lines) if 'FAILURES' in line),None)
        detail=' | '.join(line for line in (rank_line,failures_line) if line)
        if not detail: detail='No rank movement or FAILURES line reported'
        body=('Status: %s\nDuration: %s\n%s\n\n%s' %
              (status,_duration(started_at,finished_at),detail,'\n'.join(lines)))
        msg=EmailMessage()
        msg['Subject']='Moneyball Weekly Refresh - %s - %s'%(status,finished_at.strftime('%Y-%m-%d %H:%M'))
        msg['From']=from_address
        msg['To']=_TO
        msg.set_content(body)
        with smtplib.SMTP(_HOST,_PORT,timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(apple_id,password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        _log(log_path,'EMAIL REPORT FAILED - %s'%type(e).__name__)
        return False
