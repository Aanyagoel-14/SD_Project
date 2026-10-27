"""MIMS local prototype. Python 3.10+, no third-party packages required."""
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = Path(os.environ.get('MIMS_DB', str(ROOT / 'mims.sqlite3')))
SHIFTS = {'Morning': (9, 13), 'Afternoon': (13, 17), 'Evening': (17, 20)}
DURATION = 45  # minutes per mock interview
ROUNDS = ('Technical', 'HR', 'Behavioural', 'Case study')
SCORES = ('technical', 'communication', 'problem_solving')

def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()

@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()

def password(value, salt):
    return hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 150000).hex()

def add_user(db, name, email, role, pwd='Demo@123', **profile):
    salt = secrets.token_hex(16)
    cur = db.execute('INSERT INTO users(name,email,role,salt,hash,expertise,bio,location) VALUES(?,?,?,?,?,?,?,?)',
        (name, email.lower(), role, salt, password(pwd, salt), profile.get('expertise',''), profile.get('bio',''), profile.get('location','')))
    return cur.lastrowid

def clock(minute):
    return f'{minute//60:02}:{minute%60:02}'

def generate(db, iid, shift, start, end):
    if shift not in SHIFTS: raise ValueError('Choose a valid schedule.')
    start, end = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if start < today() or end < start or (end-start).days > 90:
        raise ValueError('Choose a date range from today, up to 90 days.')
    if not db.execute("SELECT 1 FROM users WHERE id=? AND role='Interviewer' AND active=1", (iid,)).fetchone():
        raise ValueError('Active interviewer not found.')
    if db.execute('SELECT 1 FROM assignments WHERE interviewer=? AND start<=? AND end>=?', (iid,str(end),str(start))).fetchone():
        raise ValueError('This interviewer already has a schedule in that date range.')
    db.execute('INSERT INTO assignments(interviewer,shift,start,end) VALUES(?,?,?,?)',(iid,shift,str(start),str(end)))
    count = 0
    for day in range((end-start).days+1):
        date = str(start+dt.timedelta(days=day))
        for minute in range(SHIFTS[shift][0]*60, SHIFTS[shift][1]*60-DURATION+1, DURATION):
            db.execute('INSERT INTO slots(interviewer,date,start,end,shift) VALUES(?,?,?,?,?)',(iid,date,clock(minute),clock(minute+DURATION),shift))
            count += 1
    return count

def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,role TEXT NOT NULL,salt TEXT NOT NULL,hash TEXT NOT NULL,active INTEGER DEFAULT 1,expertise TEXT DEFAULT '',bio TEXT DEFAULT '',location TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user INTEGER REFERENCES users(id),expires TEXT);
        CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY,interviewer INTEGER REFERENCES users(id),shift TEXT,start TEXT,end TEXT);
        CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY,interviewer INTEGER REFERENCES users(id),date TEXT,start TEXT,end TEXT,shift TEXT,UNIQUE(interviewer,date,start));
        CREATE TABLE IF NOT EXISTS interviews(id INTEGER PRIMARY KEY,student INTEGER REFERENCES users(id),slot INTEGER REFERENCES slots(id),status TEXT DEFAULT 'Confirmed',channel TEXT,round TEXT,target TEXT DEFAULT '',notes TEXT DEFAULT '',created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot ON interviews(slot) WHERE status!='Cancelled';
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY,interview INTEGER UNIQUE REFERENCES interviews(id),technical INTEGER,communication INTEGER,problem_solving INTEGER,strengths TEXT DEFAULT '',improvements TEXT DEFAULT '',created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user INTEGER REFERENCES users(id),message TEXT,unread INTEGER DEFAULT 1,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor INTEGER REFERENCES users(id),action TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]: return
        add_user(db,'Aanya Goel','student@mims.demo','Student')
        add_user(db,'Placement Cell Administrator','admin@mims.demo','Admin')
        add_user(db,'Institute Management','management@mims.demo','Management')
        interviewers = [
            ('Rohan Verma','interviewer@mims.demo','Software engineering · Technical rounds','Practise data structures, system design and live coding the way product companies interview.','Placement Cell · P-101','Morning'),
            ('Ananya Iyer','ananya@mims.demo','HR & behavioural rounds','Sharpen your introduction, handle behavioural questions and present yourself with confidence.','Placement Cell · P-104','Afternoon'),
            ('Vikram Nair','vikram@mims.demo','Data, analytics & case rounds','Work through case studies, SQL and analytical problem solving as asked by consulting and analytics firms.','Online · Google Meet','Evening')]
        for name,email,expertise,bio,location,shift in interviewers:
            iid = add_user(db,name,email,'Interviewer',expertise=expertise,bio=bio,location=location)
            generate(db,iid,shift,str(today()),str(today()+dt.timedelta(days=13)))
        sid = db.execute("SELECT id FROM slots WHERE interviewer=4 AND date=? AND start='10:30'",(str(today()+dt.timedelta(days=1)),)).fetchone()[0]
        db.execute("INSERT INTO interviews(student,slot,channel,round,target,notes) VALUES(1,?,'Online','Technical','SDE Intern · Product company','Arrays, graphs and a short system design discussion')",(sid,))
        # One completed interview from yesterday so the feedback flow is visible on first start.
        past = db.execute("INSERT INTO slots(interviewer,date,start,end,shift) VALUES(5,?,'14:30','15:15','Afternoon')",(str(today()-dt.timedelta(days=1)),)).lastrowid
        iid = db.execute("INSERT INTO interviews(student,slot,status,channel,round,target) VALUES(1,?,'Completed','Online','HR','Analyst · Consulting')",(past,)).lastrowid
        db.execute("INSERT INTO feedback(interview,technical,communication,problem_solving,strengths,improvements) VALUES(?,4,3,4,'Clear structure in answers and a confident introduction.','Keep answers to behavioural questions under two minutes and quantify achievements.')",(iid,))
        db.execute("INSERT INTO notifications(user,message) VALUES(1,'Welcome to MIMS. Your first mock interview is confirmed and feedback from your HR round is available.')")

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/'static'),**kwargs)
    def send_json(self,data,status=200,cookie=None):
        blob=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if cookie: self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(blob)))
        self.end_headers(); self.wfile.write(blob)
    def do_GET(self):
        if self.path.startswith('/api/'): self.api('GET')
        else: super().do_GET()
    def do_POST(self): self.api('POST')
    def api(self,method):
        try:
            self.handle_api(method)
        except PermissionError as e: self.send_json({'error':str(e)},403)
        except (ValueError,KeyError,TypeError) as e: self.send_json({'error':str(e)},400)
        except sqlite3.IntegrityError: self.send_json({'error':'This slot is no longer available, or the email already exists. Please refresh and try again.'},409)
        except Exception as e:
            print(type(e).__name__,str(e)); self.send_json({'error':'The request could not be completed.'},500)
    def handle_api(self,method):
        path=urlparse(self.path).path
        data={}
        if method=='POST':
            if int(self.headers.get('Content-Length',0))>20000: raise ValueError('Request too large.')
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'): raise PermissionError('Invalid request origin.')
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
        with connect() as db:
            if path in ('/api/login','/api/register') and method=='POST':
                email=str(data.get('email','')).strip().lower(); pwd=str(data.get('password',''))
                if path.endswith('register'):
                    name=str(data.get('name','')).strip()
                    if not name or '@' not in email or len(pwd)<8: raise ValueError('Enter your name, a valid email, and a password of at least 8 characters.')
                    add_user(db,name[:100],email,'Student',pwd)
                user=db.execute('SELECT * FROM users WHERE email=? AND active=1',(email,)).fetchone()
                if not user or not hmac.compare_digest(user['hash'],password(pwd,user['salt'])):
                    return self.send_json({'error':'Invalid email or password.'},401)
                token=secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(token,user['id'],str(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=12))))
                return self.send_json({'ok':True},cookie=f'mims_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            cookie=SimpleCookie(); cookie.load(self.headers.get('Cookie',''))
            token=cookie['mims_session'].value if 'mims_session' in cookie else ''
            user=db.execute('SELECT u.* FROM users u JOIN sessions s ON s.user=u.id WHERE s.token=? AND s.expires>? AND u.active=1',(token,str(dt.datetime.now(dt.timezone.utc)))).fetchone()
            if not user: return self.send_json({'error':'Please sign in.'},401)
            uid,role=user['id'],user['role']
            def require(*roles):
                if role not in roles: raise PermissionError('Your role does not have access to this action.')
            def audit(action): db.execute('INSERT INTO audit(actor,action) VALUES(?,?)',(uid,action))
            def notify(iid,message,student_only=False):
                row=db.execute('SELECT i.student,s.interviewer FROM interviews i JOIN slots s ON s.id=i.slot WHERE i.id=?',(iid,)).fetchone()
                for recipient in ((row['student'],) if student_only else (row['student'],row['interviewer'])):
                    db.execute('INSERT INTO notifications(user,message) VALUES(?,?)',(recipient,f'Interview #{iid}: {message}'))
            if path=='/api/logout' and method=='POST':
                db.execute('DELETE FROM sessions WHERE token=?',(token,)); return self.send_json({'ok':True},cookie='mims_session=; Path=/; Max-Age=0')
            if path=='/api/state' and method=='GET':
                public={k:user[k] for k in ('id','name','email','role')}
                interviewers=[dict(r) for r in db.execute("SELECT id,name,expertise,bio,location,active FROM users WHERE role='Interviewer'")]
                slots=[dict(r) for r in db.execute("SELECT s.*,CASE WHEN i.id IS NULL THEN 1 ELSE 0 END available FROM slots s JOIN users u ON u.id=s.interviewer LEFT JOIN interviews i ON i.slot=s.id AND i.status!='Cancelled' WHERE s.date>=? AND u.active=1 ORDER BY s.date,s.start",(str(today()),))]
                query='SELECT i.*,s.interviewer,s.date,s.start,s.end,v.name interviewer_name,u.name student_name,u.email student_email,f.technical,f.communication,f.problem_solving,f.strengths,f.improvements,f.created feedback_at FROM interviews i JOIN slots s ON s.id=i.slot JOIN users v ON v.id=s.interviewer JOIN users u ON u.id=i.student LEFT JOIN feedback f ON f.interview=i.id'
                params=()
                if role=='Student': query+=' WHERE i.student=?'; params=(uid,)
                elif role=='Interviewer': query+=' WHERE s.interviewer=?'; params=(uid,)
                interviews=[dict(r) for r in db.execute(query+' ORDER BY s.date,s.start',params)]
                users=[dict(r) for r in db.execute('SELECT id,name,email,role,active FROM users')] if role=='Admin' else []
                notes=[dict(r) for r in db.execute('SELECT * FROM notifications WHERE user=? ORDER BY id DESC LIMIT 50',(uid,))]
                logs=[dict(r) for r in db.execute('SELECT a.*,u.name FROM audit a JOIN users u ON u.id=a.actor ORDER BY a.id DESC LIMIT 30')] if role=='Admin' else []
                assignments=[dict(r) for r in db.execute('SELECT * FROM assignments')]
                return self.send_json(dict(user=public,interviewers=interviewers,slots=slots,interviews=interviews,users=users,notifications=notes,audit=logs,assignments=assignments,rounds=ROUNDS,duration=DURATION,today=str(today())))
            if method!='POST': return self.send_json({'error':'Not found.'},404)
            db.execute('BEGIN IMMEDIATE')
            if path=='/api/book':
                require('Student','Admin')
                student=uid if role=='Student' else int(data['student'])
                if not db.execute("SELECT 1 FROM users WHERE id=? AND role='Student' AND active=1",(student,)).fetchone(): raise ValueError('Select an active student.')
                slot=db.execute('SELECT s.* FROM slots s JOIN users u ON u.id=s.interviewer WHERE s.id=? AND u.active=1',(int(data['slot']),)).fetchone()
                self.validate_slot(slot)
                self.check_student_conflict(db,student,slot)
                rnd=data.get('round','Technical')
                if rnd not in ROUNDS: raise ValueError('Choose a valid interview round.')
                cur=db.execute('INSERT INTO interviews(student,slot,channel,round,target,notes) VALUES(?,?,?,?,?,?)',(student,slot['id'],'Online' if role=='Student' else 'Offline',rnd,str(data.get('target',''))[:120],str(data.get('notes',''))[:1000]))
                notify(cur.lastrowid,'Mock interview confirmed.'); audit(f'Created interview #{cur.lastrowid}')
            elif path in ('/api/reschedule','/api/status','/api/feedback'):
                i=db.execute('SELECT i.*,s.interviewer FROM interviews i JOIN slots s ON s.id=i.slot WHERE i.id=?',(int(data['id']),)).fetchone()
                if not i: raise ValueError('Interview not found.')
                if role=='Management' or (role=='Student' and i['student']!=uid) or (role=='Interviewer' and i['interviewer']!=uid): raise PermissionError('You cannot change this interview.')
                if path.endswith('feedback'):
                    require('Interviewer','Admin')
                    if i['status']!='Completed': raise ValueError('Feedback can only be recorded for completed interviews.')
                    scores=[int(data[k]) for k in SCORES]
                    if any(s<1 or s>5 for s in scores): raise ValueError('Scores must be between 1 and 5.')
                    db.execute('INSERT INTO feedback(interview,technical,communication,problem_solving,strengths,improvements) VALUES(?,?,?,?,?,?) ON CONFLICT(interview) DO UPDATE SET technical=excluded.technical,communication=excluded.communication,problem_solving=excluded.problem_solving,strengths=excluded.strengths,improvements=excluded.improvements,created=CURRENT_TIMESTAMP',(i['id'],*scores,str(data.get('strengths',''))[:1000],str(data.get('improvements',''))[:1000]))
                    notify(i['id'],'Interview feedback is now available.',student_only=True); audit(f'Recorded feedback for interview #{i["id"]}')
                    return self.send_json({'ok':True})
                if i['status'] not in ('Confirmed','Pending'): raise ValueError('This interview is already closed.')
                if path.endswith('reschedule'):
                    require('Student','Admin')
                    slot=db.execute('SELECT * FROM slots WHERE id=?',(int(data['slot']),)).fetchone(); self.validate_slot(slot)
                    if slot['interviewer']!=i['interviewer']: raise ValueError('Choose another slot with the same interviewer.')
                    self.check_student_conflict(db,i['student'],slot,i['id'])
                    db.execute('UPDATE interviews SET slot=? WHERE id=?',(slot['id'],i['id']))
                    message='Rescheduled successfully.'
                else:
                    status=data['status']
                    allowed=('Cancelled',) if role=='Student' else ('Confirmed','Completed','No-show','Cancelled')
                    if status not in allowed: raise PermissionError('Invalid status change for your role.')
                    db.execute('UPDATE interviews SET status=? WHERE id=?',(status,i['id'])); message=f'Status updated to {status}.'+(' Feedback will be shared shortly.' if status=='Completed' else '')
                notify(i['id'],message); audit(f'Interview #{i["id"]}: {message}')
            elif path=='/api/schedule':
                require('Admin'); count=generate(db,int(data['interviewer']),data['shift'],data['from'],data['to']); audit(f'Generated {count} slots for interviewer #{data["interviewer"]}')
            elif path=='/api/profile':
                require('Admin','Interviewer'); target=uid if role=='Interviewer' else int(data['id'])
                if not db.execute("SELECT 1 FROM users WHERE id=? AND role='Interviewer'",(target,)).fetchone(): raise ValueError('Interviewer not found.')
                db.execute('UPDATE users SET expertise=?,bio=?,location=? WHERE id=?',(str(data['expertise'])[:200],str(data['bio'])[:1000],str(data['location'])[:200],target)); audit(f'Updated interviewer #{target}')
            elif path=='/api/user':
                require('Admin')
                if int(data['id'])==uid: raise ValueError('You cannot deactivate your own account.')
                db.execute('UPDATE users SET active=? WHERE id=?',(int(bool(data['active'])),int(data['id']))); audit(f'Updated account #{data["id"]}')
            elif path=='/api/read': db.execute('UPDATE notifications SET unread=0 WHERE user=?',(uid,))
            else: return self.send_json({'error':'Not found.'},404)
            self.send_json({'ok':True})
    @staticmethod
    def validate_slot(slot):
        if not slot: raise ValueError('Slot not found.')
        now=dt.datetime.now(dt.timezone(dt.timedelta(hours=5,minutes=30)))
        if slot['date']+'T'+slot['start'] <= now.strftime('%Y-%m-%dT%H:%M'): raise ValueError('Choose a future slot.')
    @staticmethod
    def check_student_conflict(db,student,slot,exclude=-1):
        if db.execute("SELECT 1 FROM interviews i JOIN slots s ON s.id=i.slot WHERE i.student=? AND i.id!=? AND i.status!='Cancelled' AND s.date=? AND s.start<? AND s.end>?",(student,exclude,slot['date'],slot['end'],slot['start'])).fetchone():
            raise ValueError('This student already has a mock interview at that time.')

if __name__=='__main__':
    init()
    port=int(os.environ.get('PORT','8765'))
    print(f'MIMS running at http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
