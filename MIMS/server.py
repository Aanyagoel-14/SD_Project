"""MIMS - Mock Interview Management System (Team T065, CS314, LNMIIT).
Local prototype. Python 3.10+, standard library only.
Roles: Student, Interviewer, Coordinator (Placement Cell), Admin.
Core use cases: UC-004 Allocate Interviewer, UC-009 Register for Slot, UC-013 Submit Feedback."""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = Path(os.environ.get('MIMS_DB', str(ROOT / 'mims.sqlite3')))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ROLES = ('Student', 'Interviewer', 'Coordinator', 'Admin')
SIGNUP_ROLES = ('Student', 'Interviewer', 'Coordinator')
DOMAINS = ('Software Engineering', 'Data Science', 'Core Electronics', 'HR & Behavioural', 'Consulting & Case')
BRANCHES = ('CSE', 'CCE', 'ECE', 'ME')
MODES = ('Online', 'Offline')
SCORE_MIN, SCORE_MAX = 1, 10
PASSWORD = re.compile(r'^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,128}$', re.S)
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def today(): return str(dt.datetime.now(IST).date())
def now(): return dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M')
def day(offset): return str(dt.datetime.now(IST).date() + dt.timedelta(days=offset))

class Reject(ValueError):
    """A business-rule rejection with the outcome code from the interface design (Assignment 7, 49.2)."""
    def __init__(self, code, message): super().__init__(message); self.code = code

@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db: yield db
    finally: db.close()

def hash_password(value, salt): return hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 150000).hex()

PROFILE = ('phone', 'roll', 'branch', 'batch', 'cgpa', 'employee_id', 'designation', 'domain', 'experience', 'department')
def add_user(db, name, email, role, pwd='Demo@123', status='Active', **p):
    salt = secrets.token_hex(16)
    return db.execute(f'INSERT INTO users(name,email,role,status,salt,hash,{",".join(PROFILE)}) VALUES(?,?,?,?,?,?,{",".join("?" * len(PROFILE))})',
        (name, email.lower(), role, status, salt, hash_password(pwd, salt), *[p.get(k) for k in PROFILE])).lastrowid

def notify(db, user, kind, message): db.execute('INSERT INTO notifications(user,type,message) VALUES(?,?,?)', (user, kind, message))
def audit(db, actor, action): db.execute('INSERT INTO audit(actor,action) VALUES(?,?)', (actor, action))
def listed(v): return [x for x in str(v or '').split(',') if x]
def registered(db, sid): return db.execute("SELECT COUNT(*) FROM registrations WHERE slot=? AND status='Registered'", (sid,)).fetchone()[0]
def registration_open(s): return s['status'] == 'Published' and today() <= (s['reg_end'] or '') and today() <= s['date']

def eligibility(slot, student):
    """EligibilityCriteria.isSatisfiedBy(student): None when eligible, otherwise the reason (FR-019)."""
    if slot['branches'] and student['branch'] not in listed(slot['branches']): return f"Open to {', '.join(listed(slot['branches']))} students only."
    if slot['min_cgpa'] and (student['cgpa'] or 0) < slot['min_cgpa']: return f"Requires a CGPA of at least {slot['min_cgpa']:g}."
    return None

def clash(db, iid, slot):
    """True when the interviewer already has another non-cancelled slot overlapping this one (UC-004 availability check)."""
    return bool(db.execute("SELECT 1 FROM slots WHERE interviewer=? AND id!=? AND status!='Cancelled' AND date=? AND start<? AND end>?",
                           (iid, slot['id'], slot['date'], slot['end'], slot['start'])).fetchone())

def candidates(db, slot):
    return [dict(r) for r in db.execute("SELECT id,name,designation FROM users WHERE role='Interviewer' AND status='Active' AND domain=? ORDER BY name", (slot['domain'],)) if not clash(db, r['id'], slot)]

def check_completion(db, sid):
    """A slot is Completed once every registered participant has attendance and every present one has feedback."""
    s = db.execute('SELECT * FROM slots WHERE id=?', (sid,)).fetchone()
    if s['status'] != 'Published' or s['date'] > today(): return
    open_items = db.execute("SELECT COUNT(*) FROM registrations WHERE slot=? AND status='Registered' AND (attendance IS NULL OR (attendance='Present' AND score IS NULL))", (sid,)).fetchone()[0]
    if registered(db, sid) and not open_items: db.execute("UPDATE slots SET status='Completed' WHERE id=?", (sid,))

def send_reminders(db):
    """Scheduler: one reminder for each participant and interviewer of a published slot happening today or tomorrow (FR-037)."""
    for r in db.execute("SELECT r.id,r.student,s.id slot,s.date,s.start,s.domain FROM registrations r JOIN slots s ON s.id=r.slot WHERE r.status='Registered' AND r.reminded=0 AND s.status='Published' AND s.date BETWEEN ? AND ?", (day(0), day(1))).fetchall():
        notify(db, r['student'], 'Reminder', f"Reminder: your {r['domain']} mock interview (slot #{r['slot']}) is on {r['date']} at {r['start']}."); db.execute('UPDATE registrations SET reminded=1 WHERE id=?', (r['id'],))
    for s in db.execute("SELECT * FROM slots WHERE status='Published' AND reminded=0 AND interviewer IS NOT NULL AND date BETWEEN ? AND ?", (day(0), day(1))).fetchall():
        notify(db, s['interviewer'], 'Reminder', f"Reminder: you are interviewing for slot #{s['id']} ({s['domain']}) on {s['date']} at {s['start']}."); db.execute('UPDATE slots SET reminded=1 WHERE id=?', (s['id'],))

def slot_fields(data, s=None):
    """Validate the slot attributes of FR-007. Missing venue / closing date are allowed in a Draft (FR-008)."""
    g = lambda k, d='': str(data.get(k, s[k] if s is not None else d) or '').strip()
    date, start, end, domain, mode = g('date'), g('start'), g('end'), g('domain'), g('mode', 'Online')
    try: dt.date.fromisoformat(date); dt.time.fromisoformat(start); dt.time.fromisoformat(end)
    except ValueError: raise ValueError('Enter a valid date, start time and end time.')
    if date < today(): raise ValueError('The interview date cannot be in the past.')
    if start >= end: raise ValueError('The end time must be after the start time.')
    if domain not in DOMAINS: raise ValueError('Choose a domain.')
    if mode not in MODES: raise ValueError('Mode must be Online or Offline.')
    capacity = int(data.get('capacity', s['capacity'] if s is not None else 0) or 0)
    if not 0 <= capacity <= 200: raise ValueError('Capacity must be between 1 and 200.')
    cgpa = float(data.get('min_cgpa', s['min_cgpa'] if s is not None else 0) or 0)
    if not 0 <= cgpa <= 10: raise ValueError('Minimum CGPA must be between 0 and 10.')
    reg_end = g('reg_end')
    if reg_end:
        dt.date.fromisoformat(reg_end)
        if reg_end > date: raise ValueError('Registration must close on or before the interview date.')
    branches = data.get('branches', listed(s['branches']) if s is not None else [])
    if isinstance(branches, str): branches = listed(branches)
    if any(b not in BRANCHES for b in branches): raise ValueError('Unknown branch.')
    return dict(date=date, start=start, end=end, domain=domain, mode=mode, venue=g('venue')[:200], capacity=capacity, reg_end=reg_end, min_cgpa=cgpa, branches=','.join(branches), instructions=g('instructions')[:1000])

def signup_fields(data):
    """Create Account: each user type supplies its own profile fields."""
    role = data.get('role')
    if role not in SIGNUP_ROLES: raise Reject('INVALID_ROLE', 'Choose Student, Interviewer or Placement Cell Coordinator. Administrator accounts cannot be self-registered.')
    first, middle, last = (str(data.get(k, '')).strip()[:50] for k in ('first', 'middle', 'last'))
    email, pwd, phone = str(data.get('email', '')).strip().lower(), str(data.get('password', '')), str(data.get('phone', '')).strip()[:15]
    if not first or not last: raise ValueError('Enter your first and last name.')
    if not EMAIL.match(email): raise ValueError('Enter a valid email address.')
    if not re.fullmatch(r'\+?\d{10,13}', phone): raise ValueError('Enter a valid 10-digit mobile number.')
    if not PASSWORD.match(pwd): raise ValueError('Password must be at least 8 characters and include an uppercase letter, a lowercase letter, a number and a special character.')
    p = dict(phone=phone)
    if role == 'Student':
        p['roll'] = str(data.get('roll', '')).strip().upper()[:20]; p['branch'] = data.get('branch')
        if not p['roll'] or p['branch'] not in BRANCHES: raise ValueError('Enter your roll number and choose your branch.')
        try: p['batch'] = int(data.get('batch')); p['cgpa'] = float(data.get('cgpa'))
        except (TypeError, ValueError): raise ValueError('Enter your batch year and CGPA.')
        if not 2020 <= p['batch'] <= 2040 or not 0 <= p['cgpa'] <= 10: raise ValueError('Enter a valid batch year and a CGPA between 0 and 10.')
    else:
        p['employee_id'] = str(data.get('employee_id', '')).strip().upper()[:20]
        if not p['employee_id']: raise ValueError('Enter your employee ID.')
        if role == 'Interviewer':
            p['designation'] = str(data.get('designation', '')).strip()[:100]; p['domain'] = data.get('domain')
            try: p['experience'] = int(data.get('experience'))
            except (TypeError, ValueError): raise ValueError('Enter your years of experience.')
            if not p['designation'] or p['domain'] not in DOMAINS or not 0 <= p['experience'] <= 60: raise ValueError('Enter your designation, domain of expertise and years of experience.')
        else:
            p['department'] = str(data.get('department', '')).strip()[:100]
            if not p['department']: raise ValueError('Enter your department.')
    return role, ' '.join(x for x in (first, middle, last) if x), email, pwd, p

def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,role TEXT NOT NULL,status TEXT DEFAULT 'Active',salt TEXT NOT NULL,hash TEXT NOT NULL,phone TEXT,roll TEXT,branch TEXT,batch INTEGER,cgpa REAL,employee_id TEXT,designation TEXT,domain TEXT,experience INTEGER,department TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user INTEGER REFERENCES users(id),expires TEXT);
        CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY,date TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,domain TEXT NOT NULL,mode TEXT DEFAULT 'Online',venue TEXT DEFAULT '',capacity INTEGER DEFAULT 0,reg_end TEXT DEFAULT '',min_cgpa REAL DEFAULT 0,branches TEXT DEFAULT '',instructions TEXT DEFAULT '',status TEXT DEFAULT 'Draft',interviewer INTEGER REFERENCES users(id),allocated_by INTEGER REFERENCES users(id),created_by INTEGER REFERENCES users(id),reminded INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS registrations(id INTEGER PRIMARY KEY,student INTEGER REFERENCES users(id),slot INTEGER REFERENCES slots(id),status TEXT DEFAULT 'Registered',attendance TEXT,score INTEGER,comments TEXT,feedback_at TEXT,reminded INTEGER DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_registration ON registrations(student,slot) WHERE status='Registered';
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user INTEGER REFERENCES users(id),type TEXT,message TEXT,unread INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor INTEGER REFERENCES users(id),action TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]: return
        aanya = add_user(db, 'Aanya Goel', 'student@mims.demo', 'Student', roll='24UCS119', branch='CSE', batch=2028, cgpa=8.4, phone='9876500119')
        panth = add_user(db, 'Panth Moradia', 'panth@mims.demo', 'Student', roll='24UCS120', branch='CSE', batch=2028, cgpa=7.9, phone='9876500120')
        add_user(db, 'Arnav Sharma', 'arnav@mims.demo', 'Student', roll='24UCS121', branch='ECE', batch=2028, cgpa=6.8, phone='9876500121')
        akshat = add_user(db, 'Akshat Dwivedi', 'akshat@mims.demo', 'Student', roll='24UCS122', branch='CCE', batch=2028, cgpa=9.1, phone='9876500122')
        sharma = add_user(db, 'Dr. R. Sharma', 'interviewer@mims.demo', 'Interviewer', employee_id='EMP-201', designation='Associate Professor, CSE', domain='Software Engineering', experience=12, phone='9876500201')
        meera = add_user(db, 'Prof. Meera Iyer', 'meera@mims.demo', 'Interviewer', employee_id='EMP-202', designation='Assistant Professor, CSE', domain='Data Science', experience=7, phone='9876500202')
        vikram = add_user(db, 'Mr. Vikram Nair', 'vikram@mims.demo', 'Interviewer', employee_id='EMP-203', designation='HR Manager (industry mentor)', domain='HR & Behavioural', experience=15, phone='9876500203')
        verma = add_user(db, 'Mr. S. Verma', 'coordinator@mims.demo', 'Coordinator', employee_id='EMP-301', department='Placement Cell', phone='9876500301')
        admin = add_user(db, 'System Administrator', 'admin@mims.demo', 'Admin', phone='9876500999')
        add_user(db, 'Ms. Kavya Rao', 'kavya@mims.demo', 'Interviewer', status='Pending', employee_id='EMP-204', designation='Senior Engineer (alumna)', domain='Software Engineering', experience=6, phone='9876500204')
        meet = 'https://meet.google.com/mims-demo'
        def slot(d, start, end, domain, mode, venue, cap, reg_end, status, iv=None, branches='', cgpa=0, instructions=''):
            return db.execute('INSERT INTO slots(date,start,end,domain,mode,venue,capacity,reg_end,min_cgpa,branches,instructions,status,interviewer,allocated_by,created_by,reminded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',
                (d, start, end, domain, mode, venue, cap, reg_end, cgpa, branches, instructions, status, iv, verma if iv else None, verma)).lastrowid
        def reg(student, sid, attendance=None, score=None, comments=None, status='Registered'):
            db.execute('INSERT INTO registrations(student,slot,status,attendance,score,comments,feedback_at,reminded) VALUES(?,?,?,?,?,?,?,1)', (student, sid, status, attendance, score, comments, now() if score else None))
        s1 = slot(day(-7), '10:00', '10:30', 'Software Engineering', 'Online', meet, 3, day(-8), 'Completed', sharma, 'CSE,CCE', 7.0, 'Keep your resume ready. Expect data structures and one system design question.')
        reg(aanya, s1, 'Present', 8, 'Strong fundamentals. Explain time complexity before you start coding.'); reg(panth, s1, 'Present', 7, 'Good approach. Practise edge cases and state your assumptions aloud.'); reg(akshat, s1, 'Absent')
        s2 = slot(day(-2), '14:00', '14:30', 'HR & Behavioural', 'Offline', 'Placement Cell, Room P-104', 4, day(-3), 'Published', vikram, '', 0, 'Formal dress. Bring a printed resume.')
        reg(aanya, s2, 'Present'); reg(akshat, s2, 'Present', 9, 'Confident and well structured. Quantify achievements in your introduction.')
        s3 = slot(day(3), '10:00', '10:30', 'Software Engineering', 'Online', meet, 2, day(2), 'Published', sharma, 'CSE,CCE', 7.0, 'Join five minutes early with your camera on.')
        reg(panth, s3)
        slot(day(4), '10:00', '10:30', 'Data Science', 'Online', meet, 5, day(3), 'Published', meera, '', 0, 'Revise statistics, SQL and one end-to-end ML project.')
        slot(day(5), '15:00', '15:30', 'HR & Behavioural', 'Offline', 'Placement Cell, Room P-104', 5, day(4), 'Published', vikram)
        slot(day(7), '14:00', '14:30', 'Software Engineering', 'Online', meet, 3, day(6), 'Draft', None, 'CSE,CCE', 7.0, 'Second practice round for product-company roles.')
        s7 = slot(day(2), '11:00', '11:30', 'Consulting & Case', 'Offline', 'Seminar Hall 2', 3, day(1), 'Cancelled')
        reg(akshat, s7, status='Cancelled')
        notify(db, aanya, 'Feedback', 'Feedback for your Software Engineering mock interview (slot #1) is available. Score 8/10.')
        notify(db, akshat, 'Cancellation', f'Slot #7 (Consulting & Case) on {day(2)} was cancelled by the placement cell. Your registration was cancelled.')
        notify(db, vikram, 'Feedback', 'Feedback is still pending for Aanya Goel in slot #2.')
        notify(db, verma, 'Allocation', 'Slot #6 is a draft and needs an interviewer before it can be published.')
        notify(db, admin, 'Account', 'Ms. Kavya Rao registered as an Interviewer and is waiting for approval.')
        audit(db, verma, 'Created demo slots #1 to #7')

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(ROOT / 'static'), **k)
    def log_message(self, *a): pass
    def send_json(self, data, status=200, cookie=None):
        blob = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        if cookie: self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', str(len(blob))); self.end_headers(); self.wfile.write(blob)
    def do_GET(self):
        if self.path.startswith('/api/'): self.api('GET')
        else: super().do_GET()
    def do_POST(self): self.api('POST')
    def api(self, method):
        try: self.handle_api(method)
        except PermissionError as e: self.send_json({'error': str(e), 'code': 'FORBIDDEN'}, 403)
        except Reject as e: self.send_json({'error': str(e), 'code': e.code}, 400)
        except (ValueError, KeyError, TypeError) as e: self.send_json({'error': str(e) if isinstance(e, ValueError) else 'A required field is missing.', 'code': 'VALIDATION'}, 400)
        except sqlite3.IntegrityError: self.send_json({'error': 'That record already exists. If you used this email before, log in instead.', 'code': 'CONFLICT'}, 409)
        except Exception as e:
            print(type(e).__name__, e); self.send_json({'error': 'The request could not be completed.', 'code': 'PERSISTENCE_ERROR'}, 500)

    def handle_api(self, method):
        path = urlparse(self.path).path; data = {}
        if method == 'POST':
            size = int(self.headers.get('Content-Length', 0))
            if size > 20000: raise ValueError('Request too large.')
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'): raise PermissionError('Invalid request origin.')
            data = json.loads(self.rfile.read(size) or '{}')
        with connect() as db:
            # ---------- Authentication (UC-001) ----------
            if path == '/api/signup' and method == 'POST':
                role, name, email, pwd, profile = signup_fields(data)
                status = 'Active' if role == 'Student' else 'Pending'
                uid = add_user(db, name, email, role, pwd, status, **profile)
                if status == 'Pending':
                    for a in db.execute("SELECT id FROM users WHERE role='Admin' AND status='Active'").fetchall():
                        notify(db, a['id'], 'Account', f"{name} registered as {'Placement Cell Coordinator' if role == 'Coordinator' else role} and is waiting for approval.")
                    audit(db, uid, f'Registered as {role} (pending approval)')
                    return self.send_json({'ok': True, 'pending': True})
                audit(db, uid, 'Registered as Student')
                data = {'email': email, 'password': pwd, 'role': role}; path = '/api/login'
            if path == '/api/login' and method == 'POST':
                email, pwd, chosen = str(data.get('email', '')).strip().lower(), str(data.get('password', '')), data.get('role')
                user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
                # BR-012: every login attempt (success or failure) and every logout is written to the audit log.
                # The message shown to the user stays generic (FR-002); the reason is recorded only in the audit log.
                def refuse(reason, message, code):
                    audit(db, user['id'] if user else None, f'Login failed ({reason})' + ('' if user else f' for unknown email {email[:120]}'))
                    return self.send_json({'error': message, 'code': code}, 401)
                if not user: return refuse('unknown email', 'Invalid email or password.', 'INVALID_CREDENTIALS')
                if not hmac.compare_digest(user['hash'], hash_password(pwd, user['salt'])): return refuse('wrong password', 'Invalid email or password.', 'INVALID_CREDENTIALS')
                if chosen and chosen != user['role']: return refuse(f'selected user type {chosen} does not match', 'This account is not registered under the selected user type.', 'WRONG_USER_TYPE')
                if user['status'] == 'Pending': return refuse('account pending approval', 'Your account is waiting for administrator approval.', 'ACCOUNT_PENDING')
                if user['status'] != 'Active': return refuse('account inactive', 'This account is inactive. Contact the administrator.', 'ACCOUNT_INACTIVE')
                audit(db, user['id'], f'Logged in as {user["role"]}')
                token = secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES(?,?,?)', (token, user['id'], str(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12))))
                return self.send_json({'ok': True}, cookie=f'mims_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            cookie = SimpleCookie(); cookie.load(self.headers.get('Cookie', ''))
            token = cookie['mims_session'].value if 'mims_session' in cookie else ''
            user = db.execute("SELECT u.* FROM users u JOIN sessions s ON s.user=u.id WHERE s.token=? AND s.expires>? AND u.status='Active'", (token, str(dt.datetime.now(dt.timezone.utc)))).fetchone()
            if not user: return self.send_json({'error': 'Please log in.', 'code': 'UNAUTHENTICATED'}, 401)
            uid, role = user['id'], user['role']
            def require(*roles):
                if role not in roles: raise PermissionError('Your role does not have access to this action.')
            def get_slot(sid):
                s = db.execute('SELECT * FROM slots WHERE id=?', (int(sid),)).fetchone()
                if not s: raise Reject('SLOT_NOT_FOUND', 'Interview slot not found.')
                return s
            if path == '/api/logout' and method == 'POST':
                db.execute('DELETE FROM sessions WHERE token=?', (token,)); audit(db, uid, 'Logged out'); return self.send_json({'ok': True}, cookie='mims_session=; Path=/; Max-Age=0')
            if path == '/api/state' and method == 'GET': return self.send_json(self.state(db, user))
            if method != 'POST': return self.send_json({'error': 'Not found.'}, 404)
            db.execute('BEGIN IMMEDIATE')
            # ---------- Slot scheduling and allocation (UC-003 to UC-006) ----------
            if path == '/api/slot':
                require('Coordinator'); f = slot_fields(data)
                sid = db.execute('INSERT INTO slots(date,start,end,domain,mode,venue,capacity,reg_end,min_cgpa,branches,instructions,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (*f.values(), uid)).lastrowid
                audit(db, uid, f'Created slot #{sid} ({f["domain"]}, {f["date"]} {f["start"]}) as Draft'); return self.send_json({'ok': True, 'id': sid})
            elif path == '/api/slot_update':
                require('Coordinator'); s = get_slot(data['id'])
                if s['status'] not in ('Draft', 'Allocated', 'Published'): raise Reject('INVALID_SLOT_STATE', f'A {s["status"].lower()} slot cannot be edited.')
                if s['status'] == 'Published': data = {k: v for k, v in data.items() if k in ('venue', 'capacity', 'reg_end', 'instructions')}
                f = slot_fields(data, s)
                if s['status'] == 'Published' and f['capacity'] < registered(db, s['id']): raise Reject('CAPACITY', 'Capacity cannot be lower than the number of registered students.')
                reset = s['interviewer'] and any(f[k] != s[k] for k in ('date', 'start', 'end', 'domain'))
                db.execute(f'UPDATE slots SET {",".join(k + "=?" for k in f)} WHERE id=?', (*f.values(), s['id']))
                if reset:
                    db.execute("UPDATE slots SET interviewer=NULL,allocated_by=NULL,status='Draft' WHERE id=?", (s['id'],))
                    notify(db, s['interviewer'], 'Allocation', f'Slot #{s["id"]} was rescheduled, so your allocation was removed.')
                if s['status'] == 'Published':
                    for r in db.execute("SELECT student FROM registrations WHERE slot=? AND status='Registered'", (s['id'],)).fetchall(): notify(db, r['student'], 'Slot update', f'Details of slot #{s["id"]} ({s["domain"]}, {s["date"]}) were updated. Please check the venue and instructions.')
                audit(db, uid, f'Edited slot #{s["id"]}')
            elif path == '/api/allocate':
                # AllocationService.allocateInterviewer - Assignment 7, 50.2
                require('Coordinator'); s = get_slot(data['id'])
                if s['status'] not in ('Draft', 'Allocated'): raise Reject('INVALID_SLOT_STATE', 'An interviewer can only be allocated before the slot is published.')
                iv = db.execute("SELECT * FROM users WHERE id=? AND role='Interviewer' AND status='Active'", (int(data['interviewer']),)).fetchone()
                if not iv: raise Reject('INTERVIEWER_NOT_FOUND', 'Interviewer not found or not active.')
                if iv['domain'] != s['domain']: raise Reject('INTERVIEWER_UNAVAILABLE', f"{iv['name']} interviews for {iv['domain']}, not {s['domain']}.")
                if clash(db, iv['id'], s): raise Reject('INTERVIEWER_UNAVAILABLE', f"{iv['name']} already has another slot at this time. Choose another interviewer.")
                old = s['interviewer']
                db.execute("UPDATE slots SET interviewer=?,allocated_by=?,status='Allocated' WHERE id=?", (iv['id'], uid, s['id']))
                if old and old != iv['id']: notify(db, old, 'Allocation', f'You were replaced as interviewer for slot #{s["id"]}.')
                notify(db, iv['id'], 'Allocation', f'You have been allocated to slot #{s["id"]} ({s["domain"]}) on {s["date"]} at {s["start"]}.')
                audit(db, uid, f'Allocated {iv["name"]} to slot #{s["id"]}')
            elif path == '/api/publish':
                require('Coordinator'); s = get_slot(data['id'])
                if s['status'] == 'Draft': raise Reject('INVALID_SLOT_STATE', 'Allocate an interviewer before publishing (BR-002).')
                if s['status'] != 'Allocated': raise Reject('INVALID_SLOT_STATE', f'A {s["status"].lower()} slot cannot be published.')
                missing = [k for k, v in (('venue or meeting link', s['venue']), ('capacity', s['capacity']), ('registration closing date', s['reg_end'])) if not v]
                if missing: raise ValueError('Fill in the ' + ', '.join(missing) + ' before publishing.')
                if s['reg_end'] < today(): raise ValueError('The registration closing date has already passed. Edit the slot first.')
                db.execute("UPDATE slots SET status='Published' WHERE id=?", (s['id'],))
                notify(db, s['interviewer'], 'Slot update', f'Slot #{s["id"]} ({s["domain"]}, {s["date"]} {s["start"]}) is now open for student registration.')
                audit(db, uid, f'Published slot #{s["id"]}')
            elif path == '/api/cancel_slot':
                require('Coordinator'); s = get_slot(data['id'])
                if s['status'] in ('Completed', 'Cancelled') or (s['status'] == 'Published' and s['date'] < today()): raise Reject('INVALID_SLOT_STATE', 'This slot can no longer be cancelled.')
                db.execute("UPDATE slots SET status='Cancelled' WHERE id=?", (s['id'],))
                for r in db.execute("SELECT student FROM registrations WHERE slot=? AND status='Registered'", (s['id'],)).fetchall(): notify(db, r['student'], 'Cancellation', f'Slot #{s["id"]} ({s["domain"]}) on {s["date"]} was cancelled by the placement cell. Your registration was cancelled.')
                db.execute("UPDATE registrations SET status='Cancelled' WHERE slot=? AND status='Registered'", (s['id'],))
                if s['interviewer']: notify(db, s['interviewer'], 'Cancellation', f'Slot #{s["id"]} ({s["domain"]}) on {s["date"]} was cancelled.')
                audit(db, uid, f'Cancelled slot #{s["id"]}')
            # ---------- Registration (UC-009, UC-010) ----------
            elif path == '/api/register_slot':
                # RegistrationService.registerForSlot - checks in the order of Assignment 7, 50.3
                require('Student'); s = get_slot(data['slot'])
                if s['status'] == 'Cancelled': raise Reject('INVALID_SLOT_STATE', 'This slot was cancelled (BR-011).')
                if s['status'] != 'Published': raise Reject('INVALID_SLOT_STATE', 'This slot is not open for registration (BR-003).')
                if not registration_open(s): raise Reject('REGISTRATION_CLOSED', f'Registration for this slot closed on {s["reg_end"]} (BR-004).')
                reason = eligibility(s, user)
                if reason: raise Reject('NOT_ELIGIBLE', 'You are not eligible for this slot. ' + reason)
                if db.execute("SELECT 1 FROM registrations WHERE student=? AND slot=? AND status='Registered'", (uid, s['id'])).fetchone(): raise Reject('ALREADY_REGISTERED', 'You are already registered for this slot (BR-005).')
                if registered(db, s['id']) >= s['capacity']: raise Reject('SLOT_FULL', 'This slot is full (BR-006).')
                if db.execute("SELECT 1 FROM registrations r JOIN slots x ON x.id=r.slot WHERE r.student=? AND r.status='Registered' AND x.status!='Cancelled' AND x.date=? AND x.start<? AND x.end>?", (uid, s['date'], s['end'], s['start'])).fetchone(): raise Reject('TIME_CLASH', 'You already have another mock interview at this time.')
                rid = db.execute('INSERT INTO registrations(student,slot) VALUES(?,?)', (uid, s['id'])).lastrowid
                notify(db, uid, 'Registration', f'You are registered for slot #{s["id"]}: {s["domain"]} on {s["date"]} at {s["start"]} ({s["mode"]}).')
                audit(db, uid, f'Registered for slot #{s["id"]}'); return self.send_json({'ok': True, 'id': rid})
            elif path == '/api/cancel_registration':
                require('Student'); r = db.execute('SELECT r.*,s.status slot_status,s.reg_end,s.date,s.domain FROM registrations r JOIN slots s ON s.id=r.slot WHERE r.id=? AND r.student=?', (int(data['id']), uid)).fetchone()
                if not r or r['status'] != 'Registered': raise Reject('NOT_FOUND', 'Active registration not found.')
                if r['slot_status'] != 'Published' or today() > r['reg_end']: raise Reject('REGISTRATION_CLOSED', 'Registrations can only be cancelled while registration is open.')
                db.execute("UPDATE registrations SET status='Cancelled' WHERE id=?", (r['id'],))
                notify(db, uid, 'Registration', f'Your registration for slot #{r["slot"]} ({r["domain"]}, {r["date"]}) was cancelled.')
                audit(db, uid, f'Cancelled registration for slot #{r["slot"]}')
            # ---------- Attendance and feedback (UC-012, UC-013) ----------
            elif path in ('/api/attendance', '/api/feedback'):
                require('Interviewer')
                r = db.execute("SELECT r.*,s.interviewer,s.date,s.status slot_status FROM registrations r JOIN slots s ON s.id=r.slot WHERE r.id=? AND r.status='Registered'", (int(data['id']),)).fetchone()
                if not r: raise Reject('REGISTRATION_NOT_FOUND', 'Registration not found.')
                if r['interviewer'] != uid: raise PermissionError('Only the allocated interviewer can do this for the slot (BR-007).')
                if r['slot_status'] not in ('Published', 'Completed') or r['date'] > today(): raise Reject('INVALID_SLOT_STATE', 'Attendance and feedback open on the interview day.')
                if path.endswith('attendance'):
                    status = data.get('status')
                    if status not in ('Present', 'Absent'): raise ValueError('Attendance must be Present or Absent.')
                    if r['score'] is not None: raise Reject('FEEDBACK_EXISTS', 'Feedback is already submitted, so attendance can no longer be changed.')
                    db.execute('UPDATE registrations SET attendance=? WHERE id=?', (status, r['id']))
                else:
                    # FeedbackService.submitFeedback - Assignment 7, 50.4
                    if r['attendance'] != 'Present': raise Reject('ATTENDANCE_REQUIRED', 'Feedback can only be given to students marked Present (BR-009).')
                    if r['score'] is not None: raise Reject('FEEDBACK_EXISTS', 'Feedback has already been submitted for this student.')
                    try: score = int(data.get('score'))
                    except (TypeError, ValueError): score = None
                    if score is None or not SCORE_MIN <= score <= SCORE_MAX: raise Reject('SCORE_OUT_OF_RANGE', f'Score must be a whole number from {SCORE_MIN} to {SCORE_MAX} (BR-008).')
                    comments = str(data.get('comments', '')).strip()[:1000]
                    if not comments: raise ValueError('Add a short comment so the student knows what to improve.')
                    db.execute('UPDATE registrations SET score=?,comments=?,feedback_at=? WHERE id=?', (score, comments, now(), r['id']))
                    notify(db, r['student'], 'Feedback', f'Feedback for slot #{r["slot"]} is ready. Score {score}/{SCORE_MAX}.')
                    audit(db, uid, f'Submitted feedback for registration #{r["id"]} ({score}/{SCORE_MAX})')
                check_completion(db, r['slot'])
            # ---------- User management (UC-002) ----------
            elif path == '/api/user':
                require('Admin'); target = db.execute('SELECT * FROM users WHERE id=?', (int(data['id']),)).fetchone()
                if not target: raise ValueError('User not found.')
                if target['id'] == uid: raise ValueError('You cannot change your own account.')
                if 'status' in data:
                    st = data['status']
                    if st not in ('Active', 'Inactive'): raise ValueError('Invalid status.')
                    db.execute('UPDATE users SET status=? WHERE id=?', (st, target['id']))
                    if st == 'Inactive': db.execute('DELETE FROM sessions WHERE user=?', (target['id'],))
                    if target['status'] == 'Pending': notify(db, target['id'], 'Account', 'Your account was approved. You can now log in.' if st == 'Active' else 'Your account request was rejected.')
                    audit(db, uid, f'{"Approved" if target["status"] == "Pending" and st == "Active" else "Rejected" if target["status"] == "Pending" else "Activated" if st == "Active" else "Deactivated"} {target["name"]} ({target["role"]})')
                if 'role' in data:
                    if data['role'] not in ROLES: raise ValueError('Choose a valid role.')
                    db.execute('UPDATE users SET role=? WHERE id=?', (data['role'], target['id']))
                    audit(db, uid, f'Changed role of {target["name"]} to {data["role"]}')
            elif path == '/api/read': db.execute('UPDATE notifications SET unread=0 WHERE user=?', (uid,))
            else: return self.send_json({'error': 'Not found.'}, 404)
            self.send_json({'ok': True})

    def state(self, db, user):
        send_reminders(db)
        uid, role = user['id'], user['role']
        me = {k: user[k] for k in ('id', 'name', 'email', 'role', 'phone', 'roll', 'branch', 'batch', 'cgpa', 'employee_id', 'designation', 'domain', 'experience', 'department')}
        q = "SELECT s.*,i.name interviewer_name FROM slots s LEFT JOIN users i ON i.id=s.interviewer"
        if role == 'Student': rows = db.execute(q + " WHERE s.status IN ('Published','Completed') OR s.id IN (SELECT slot FROM registrations WHERE student=?) ORDER BY s.date,s.start", (uid,))
        elif role == 'Interviewer': rows = db.execute(q + " WHERE s.interviewer=? AND s.status!='Draft' ORDER BY s.date,s.start", (uid,))
        else: rows = db.execute(q + ' ORDER BY s.date,s.start')
        slots = []
        for r in rows:
            s = dict(r); s['registered'] = registered(db, s['id']); s['open'] = registration_open(r); s['branches'] = listed(s['branches'])
            if role == 'Student':
                s['ineligible'] = eligibility(r, user)
                mine = db.execute("SELECT id,status FROM registrations WHERE student=? AND slot=? ORDER BY id DESC", (uid, s['id'])).fetchone()
                s['mine'] = dict(mine) if mine else None
            if role == 'Coordinator' and s['status'] in ('Draft', 'Allocated'): s['candidates'] = candidates(db, r)
            slots.append(s)
        rq = "SELECT r.id,r.student,r.slot,r.status,r.attendance,r.score,r.comments,r.feedback_at,u.name student_name,u.roll,u.branch,u.cgpa,s.date,s.start,s.end,s.domain,s.mode,s.venue,s.status slot_status,s.reg_end,i.name interviewer_name FROM registrations r JOIN users u ON u.id=r.student JOIN slots s ON s.id=r.slot LEFT JOIN users i ON i.id=s.interviewer"
        if role == 'Student': regs = db.execute(rq + ' WHERE r.student=? ORDER BY s.date DESC', (uid,))
        elif role == 'Interviewer': regs = db.execute(rq + " WHERE s.interviewer=? AND r.status='Registered' ORDER BY s.date,u.name", (uid,))
        else: regs = db.execute(rq + ' ORDER BY s.date,u.name')
        out = dict(user=me, slots=slots, registrations=[dict(r) for r in regs], notifications=[dict(r) for r in db.execute('SELECT * FROM notifications WHERE user=? ORDER BY id DESC LIMIT 50', (uid,))],
                   today=today(), domains=DOMAINS, branches=BRANCHES, modes=MODES, roles=ROLES, score_max=SCORE_MAX, score_min=SCORE_MIN)
        if role in ('Coordinator', 'Admin'):
            out['performance'] = [dict(r) for r in db.execute("SELECT u.id,u.name,u.roll,u.branch,u.batch,SUM(r.attendance='Present') attended,COUNT(r.score) evaluated,ROUND(AVG(r.score),1) average FROM users u LEFT JOIN registrations r ON r.student=u.id AND r.status='Registered' WHERE u.role='Student' GROUP BY u.id ORDER BY average DESC NULLS LAST,u.name")]
        if role == 'Admin':
            out['users'] = [dict(r) for r in db.execute(f'SELECT id,name,email,role,status,created,{",".join(PROFILE)} FROM users ORDER BY status="Pending" DESC,role,name')]
            out['audit'] = [dict(r) for r in db.execute("SELECT a.*,COALESCE(u.name,'Unknown user') name FROM audit a LEFT JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 15")]
        return out

if __name__ == '__main__':
    init()
    port = int(os.environ.get('PORT', '8765'))
    print(f'MIMS running at http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
