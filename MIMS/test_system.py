"""Integration tests for MIMS: the three detailed use cases (UC-004, UC-009, UC-013),
the business rules BR-001 to BR-011, sign-up per user type and role-based access.
Each test talks to a real HTTP server backed by a temporary SQLite database."""
import concurrent.futures
import datetime as dt
import http.cookiejar
import json
import os
import socketserver
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import server

class SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.DB = Path(cls.temp.name) / 'test.sqlite3'
        server.TWO_FACTOR = True
        # Capture one-time codes instead of emailing them; the real senders are tested separately below.
        cls.codes = {}
        server.deliver_otp = lambda user, code: (cls.codes.__setitem__(user['email'], code), {'email': 'sent', 'sms': 'sent'})[1]
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.http.server_port}/api/'
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.temp.cleanup()

    def opener(self): return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def call(self, c, path, data=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type': 'application/json'})
        try:
            with c.open(req) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e: return e.code, json.load(e)
    def verify(self, c, res, email):
        """Step 2 of login: answer the one-time code challenge with the code that was 'sent'."""
        self.assertEqual(res[0], 200, res[1]); self.assertTrue(res[1].get('otp_required'), res[1])
        return self.call(c, 'verify_otp', {'challenge': res[1]['challenge'], 'code': self.codes[email]})
    def login(self, c, email, password='Demo@123', role=None):
        return self.verify(c, self.call(c, 'login', {'email': email, 'password': password, **({'role': role} if role else {})}), email)
    def client(self, email):
        c = self.opener(); self.expect(self.login(c, email), 200); return c
    def expect(self, result, status, code=None):
        self.assertEqual(result[0], status, result[1])
        if code: self.assertEqual(result[1].get('code'), code, result[1])
    def state(self, c): return self.call(c, 'state')[1]
    def slot(self, c, sid): return next(s for s in self.state(c)['slots'] if s['id'] == sid)
    def reg(self, c, name, sid): return next(r for r in self.state(c)['registrations'] if r['student_name'] == name and r['slot'] == sid)

    def test_signup_per_user_type_and_approval(self):
        c = self.opener()
        base = {'first': 'Riya', 'middle': 'K.', 'last': 'Sharma', 'phone': '9876543210', 'password': 'Password@123'}
        student = {**base, 'role': 'Student', 'email': 'riya@example.test', 'roll': '24ucs150', 'branch': 'CSE', 'batch': 2028, 'cgpa': 8.1}
        self.expect(self.call(c, 'signup', {**student, 'password': 'password'}), 400)
        self.expect(self.call(c, 'signup', {**student, 'roll': ''}), 400)
        self.expect(self.call(c, 'signup', {**student, 'phone': '123'}), 400)
        self.expect(self.call(c, 'signup', {**base, 'role': 'Admin', 'email': 'boss@example.test'}), 400, 'INVALID_ROLE')
        self.expect(self.verify(c, self.call(c, 'signup', student), 'riya@example.test'), 200)   # students are active immediately, then verify the code
        me = self.state(c)['user']
        self.assertEqual((me['role'], me['name'], me['roll']), ('Student', 'Riya K. Sharma', '24UCS150'))
        self.expect(self.call(c, 'signup', student), 409)
        self.expect(self.call(c, 'logout', {}), 200); self.expect(self.call(c, 'state'), 401)
        iv = {**base, 'role': 'Interviewer', 'email': 'neha@example.test', 'first': 'Neha', 'employee_id': 'emp-900', 'designation': 'Data Scientist', 'domain': 'Data Science', 'experience': 5}
        self.expect(self.call(c, 'signup', {**iv, 'domain': ''}), 400)
        ok = self.call(c, 'signup', iv); self.expect(ok, 200); self.assertTrue(ok[1]['pending'])
        self.expect(self.call(c, 'login', {'email': 'neha@example.test', 'password': 'Password@123', 'role': 'Interviewer'}), 401, 'ACCOUNT_PENDING')
        self.expect(self.call(c, 'signup', {**base, 'role': 'Coordinator', 'email': 'tpo@example.test', 'employee_id': 'EMP-901', 'department': ''}), 400)
        admin = self.client('admin@mims.demo')
        neha = next(u for u in self.state(admin)['users'] if u['email'] == 'neha@example.test')
        self.assertEqual((neha['status'], neha['domain'], neha['experience']), ('Pending', 'Data Science', 5))
        self.expect(self.call(admin, 'user', {'id': neha['id'], 'status': 'Active'}), 200)
        self.expect(self.call(c, 'login', {'email': 'neha@example.test', 'password': 'Password@123', 'role': 'Student'}), 401, 'WRONG_USER_TYPE')
        self.expect(self.login(c, 'neha@example.test', 'Password@123', 'Interviewer'), 200)
        self.assertTrue(any('approved' in n['message'] for n in self.state(c)['notifications']))
        self.expect(self.call(admin, 'user', {'id': neha['id'], 'status': 'Inactive'}), 200)
        self.expect(self.call(c, 'state'), 401)                                  # deactivation ends the session

    def test_uc004_allocate_and_publish(self):
        coord = self.client('coordinator@mims.demo'); student = self.client('student@mims.demo'); admin = self.client('admin@mims.demo'); sharma = self.client('interviewer@mims.demo')
        draft = self.slot(coord, 6)
        self.assertEqual((draft['status'], [c['id'] for c in draft['candidates']]), ('Draft', [5]), 'only active, domain-matching, free interviewers are offered')
        self.expect(self.call(student, 'allocate', {'id': 6, 'interviewer': 5}), 403, 'FORBIDDEN')
        self.expect(self.call(admin, 'slot', {'date': server.day(3), 'start': '10:00', 'end': '10:30', 'domain': 'Data Science'}), 403)   # BR-001
        self.expect(self.call(coord, 'publish', {'id': 6}), 400, 'INVALID_SLOT_STATE')                                                   # BR-002
        self.expect(self.call(coord, 'allocate', {'id': 6, 'interviewer': 6}), 400, 'INTERVIEWER_UNAVAILABLE')                           # wrong domain
        self.expect(self.call(coord, 'allocate', {'id': 6, 'interviewer': 10}), 400, 'INTERVIEWER_NOT_FOUND')                            # pending account
        kavya = next(u for u in self.state(admin)['users'] if u['email'] == 'kavya@mims.demo')
        self.expect(self.call(admin, 'user', {'id': kavya['id'], 'status': 'Active'}), 200)
        self.assertEqual(sorted(c['id'] for c in self.slot(coord, 6)['candidates']), [5, 10])
        self.expect(self.call(coord, 'allocate', {'id': 6, 'interviewer': 5}), 200)
        self.assertEqual((self.slot(coord, 6)['status'], self.slot(coord, 6)['interviewer']), ('Allocated', 5))
        self.assertTrue(any('#6' in n['message'] for n in self.state(sharma)['notifications']), 'interviewer is notified')
        new = self.call(coord, 'slot', {'date': server.day(7), 'start': '14:15', 'end': '14:45', 'domain': 'Software Engineering', 'capacity': 2})
        self.expect(new, 200); nid = new[1]['id']
        self.assertEqual([c['id'] for c in self.slot(coord, nid)['candidates']], [10], 'Dr. Sharma is busy at that time')
        self.expect(self.call(coord, 'allocate', {'id': nid, 'interviewer': 5}), 400, 'INTERVIEWER_UNAVAILABLE')
        self.expect(self.call(coord, 'allocate', {'id': nid, 'interviewer': 10}), 200)
        self.expect(self.call(coord, 'publish', {'id': nid}), 400, 'VALIDATION')                                                       # venue / closing date missing
        self.expect(self.call(coord, 'publish', {'id': 6}), 200)
        self.assertEqual(self.slot(coord, 6)['status'], 'Published')
        self.assertTrue(any(s['id'] == 6 for s in self.state(student)['slots']))
        self.expect(self.call(coord, 'allocate', {'id': 6, 'interviewer': 10}), 400, 'INVALID_SLOT_STATE')
        self.expect(self.call(coord, 'slot', {'date': server.day(-1), 'start': '10:00', 'end': '10:30', 'domain': 'Data Science'}), 400)

    def test_uc009_register(self):
        aanya = self.client('student@mims.demo'); panth = self.client('panth@mims.demo'); arnav = self.client('arnav@mims.demo'); akshat = self.client('akshat@mims.demo'); coord = self.client('coordinator@mims.demo')
        self.assertTrue(self.slot(arnav, 3)['ineligible'])
        self.expect(self.call(arnav, 'register_slot', {'slot': 3}), 400, 'NOT_ELIGIBLE')
        self.expect(self.call(aanya, 'register_slot', {'slot': 7}), 400, 'INVALID_SLOT_STATE')      # cancelled
        self.expect(self.call(aanya, 'register_slot', {'slot': 2}), 400, 'REGISTRATION_CLOSED')     # window over
        draft = self.call(coord, 'slot', {'date': server.day(9), 'start': '10:00', 'end': '10:30', 'domain': 'Software Engineering'})[1]['id']
        self.expect(self.call(aanya, 'register_slot', {'slot': draft}), 400, 'INVALID_SLOT_STATE')  # not published
        self.expect(self.call(panth, 'register_slot', {'slot': 3}), 400, 'ALREADY_REGISTERED')
        ok = self.call(aanya, 'register_slot', {'slot': 3}); self.expect(ok, 200)
        self.assertEqual((self.slot(aanya, 3)['registered'], self.slot(aanya, 3)['mine']['status']), (2, 'Registered'))
        self.assertTrue(any(n['type'] == 'Registration' for n in self.state(aanya)['notifications']))
        self.expect(self.call(akshat, 'register_slot', {'slot': 3}), 400, 'SLOT_FULL')
        self.expect(self.call(panth, 'cancel_registration', {'id': ok[1]['id']}), 400)              # not your registration
        self.expect(self.call(aanya, 'cancel_registration', {'id': ok[1]['id']}), 200)
        self.expect(self.call(akshat, 'register_slot', {'slot': 3}), 200)
        self.expect(self.call(coord, 'slot_update', {'id': 4, 'capacity': 1}), 200)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = [f.result() for f in [pool.submit(self.call, aanya, 'register_slot', {'slot': 4}), pool.submit(self.call, panth, 'register_slot', {'slot': 4})]]
        self.assertEqual(sorted(r[0] for r in results), [200, 400])
        self.expect(self.call(coord, 'slot_update', {'id': 4, 'capacity': 0}), 400, 'CAPACITY')
        self.expect(self.call(coord, 'cancel_slot', {'id': 4}), 200)
        winner = aanya if results[0][0] == 200 else panth
        self.assertTrue(any(n['type'] == 'Cancellation' and '#4' in n['message'] for n in self.state(winner)['notifications']))
        self.assertEqual(next(r for r in self.state(winner)['registrations'] if r['slot'] == 4)['status'], 'Cancelled')

    def test_uc013_feedback(self):
        vikram = self.client('vikram@mims.demo'); sharma = self.client('interviewer@mims.demo'); aanya = self.client('student@mims.demo'); coord = self.client('coordinator@mims.demo')
        pending = self.reg(vikram, 'Aanya Goel', 2)
        self.assertEqual((pending['attendance'], pending['score']), ('Present', None))
        self.expect(self.call(sharma, 'feedback', {'id': pending['id'], 'score': 8, 'comments': 'x'}), 403)                      # BR-007
        absent = self.reg(sharma, 'Akshat Dwivedi', 1)
        self.expect(self.call(sharma, 'feedback', {'id': absent['id'], 'score': 8, 'comments': 'x'}), 400, 'ATTENDANCE_REQUIRED')  # BR-009
        self.expect(self.call(vikram, 'feedback', {'id': pending['id'], 'score': 11, 'comments': 'x'}), 400, 'SCORE_OUT_OF_RANGE') # BR-008
        self.expect(self.call(vikram, 'feedback', {'id': pending['id'], 'score': 8}), 400)                                          # comment required
        self.expect(self.call(vikram, 'feedback', {'id': pending['id'], 'score': 8, 'comments': 'Clear and confident.'}), 200)
        self.expect(self.call(vikram, 'feedback', {'id': pending['id'], 'score': 9, 'comments': 'again'}), 400, 'FEEDBACK_EXISTS')
        self.expect(self.call(vikram, 'attendance', {'id': pending['id'], 'status': 'Absent'}), 400, 'FEEDBACK_EXISTS')
        mine = self.reg(aanya, 'Aanya Goel', 2)
        self.assertEqual((mine['score'], mine['comments']), (8, 'Clear and confident.'))
        self.assertEqual(self.slot(coord, 2)['status'], 'Completed', 'slot completes when every present student has feedback')
        perf = next(p for p in self.state(coord)['performance'] if p['name'] == 'Aanya Goel')
        self.assertEqual((perf['attended'], perf['evaluated'], perf['average']), (2, 2, 8.0))
        upcoming = self.reg(sharma, 'Panth Moradia', 3)
        self.expect(self.call(sharma, 'attendance', {'id': upcoming['id'], 'status': 'Present'}), 400, 'INVALID_SLOT_STATE')

    def test_br012_login_audit(self):
        c = self.opener()
        self.expect(self.call(c, 'login', {'email': 'panth@mims.demo', 'password': 'Wrong@123'}), 401, 'INVALID_CREDENTIALS')
        self.expect(self.call(c, 'login', {'email': 'nobody@mims.demo', 'password': 'Demo@123'}), 401, 'INVALID_CREDENTIALS')
        self.expect(self.call(c, 'login', {'email': 'panth@mims.demo', 'password': 'Demo@123', 'role': 'Interviewer'}), 401, 'WRONG_USER_TYPE')
        self.expect(self.login(c, 'panth@mims.demo', role='Student'), 200)
        self.expect(self.call(c, 'logout', {}), 200)
        log = [(a['name'], a['action']) for a in self.state(self.client('admin@mims.demo'))['audit']]
        for entry in [('Panth Moradia', 'Login failed (wrong password)'), ('Unknown user', 'Login failed (unknown email) for unknown email nobody@mims.demo'),
                      ('Panth Moradia', 'Login failed (selected user type Interviewer does not match)'), ('Panth Moradia', 'Logged in as Student'), ('Panth Moradia', 'Logged out')]:
            self.assertIn(entry, log)
        self.assertFalse(any('Wrong@123' in a for _, a in log), 'passwords are never written to the audit log')

    def test_two_step_login(self):
        c = self.opener()
        step1 = self.call(c, 'login', {'email': 'meera@mims.demo', 'password': 'Demo@123', 'role': 'Interviewer'})
        self.expect(step1, 200); r = step1[1]
        self.assertEqual((r['otp_required'], r['email'], r['phone']), (True, 'm***@mims.demo', '******0202'))
        self.expect(self.call(c, 'state'), 401)                                              # no session until the code is verified
        code = self.codes['meera@mims.demo']; wrong = '000000' if code != '000000' else '111111'
        bad = self.call(c, 'verify_otp', {'challenge': r['challenge'], 'code': wrong})
        self.expect(bad, 401, 'OTP_INVALID'); self.assertIn('4 attempts left', bad[1]['error'])
        self.expect(self.call(c, 'resend_otp', {'challenge': r['challenge']}), 429, 'OTP_WAIT')   # 30-second gap between codes
        self.expect(self.call(c, 'verify_otp', {'challenge': r['challenge'], 'code': code}), 200)
        self.assertEqual(self.state(c)['user']['email'], 'meera@mims.demo')
        self.expect(self.call(c, 'verify_otp', {'challenge': r['challenge'], 'code': code}), 401, 'OTP_EXPIRED')   # single use
        # Five wrong codes end the attempt.
        r2 = self.call(self.opener(), 'login', {'email': 'meera@mims.demo', 'password': 'Demo@123'})[1]
        wrong = '000000' if self.codes['meera@mims.demo'] != '000000' else '111111'
        for _ in range(4): self.expect(self.call(c, 'verify_otp', {'challenge': r2['challenge'], 'code': wrong}), 401, 'OTP_INVALID')
        self.expect(self.call(c, 'verify_otp', {'challenge': r2['challenge'], 'code': wrong}), 401, 'OTP_LOCKED')
        self.expect(self.call(c, 'verify_otp', {'challenge': r2['challenge'], 'code': self.codes['meera@mims.demo']}), 401, 'OTP_EXPIRED')
        # An expired code is refused; a resend after the gap issues a new code and the old one stops working.
        r3 = self.call(self.opener(), 'login', {'email': 'meera@mims.demo', 'password': 'Demo@123'})[1]
        old = self.codes['meera@mims.demo']
        with server.connect() as db:
            db.execute('UPDATE login_challenges SET last_sent=? WHERE token=?', ((server.utcnow() - dt.timedelta(seconds=40)).isoformat(timespec='seconds'), r3['challenge']))
        self.expect(self.call(c, 'resend_otp', {'challenge': r3['challenge']}), 200)
        new = self.codes['meera@mims.demo']
        if new != old: self.expect(self.call(c, 'verify_otp', {'challenge': r3['challenge'], 'code': old}), 401, 'OTP_INVALID')
        with server.connect() as db:
            db.execute('UPDATE login_challenges SET expires=? WHERE token=?', ((server.utcnow() - dt.timedelta(seconds=1)).isoformat(timespec='seconds'), r3['challenge']))
        self.expect(self.call(c, 'verify_otp', {'challenge': r3['challenge'], 'code': new}), 401, 'OTP_EXPIRED')
        # Codes are stored hashed and never written to the audit log.
        with server.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM login_challenges WHERE code_hash=?', (new,)).fetchone())
        log = [a['action'] for a in self.state(self.client('admin@mims.demo'))['audit']]
        self.assertFalse(any(new in a or old in a for a in log))
        self.assertIn('Login failed (wrong one-time code)', log)

    def test_email_is_sent_over_smtp(self):
        """send_email talks real SMTP; a tiny local SMTP server stands in for Gmail."""
        received = []
        class SMTPHandler(socketserver.StreamRequestHandler):
            def handle(self):
                say = lambda line: self.wfile.write((line + '\r\n').encode())
                say('220 test ready'); data = None
                for raw in self.rfile:
                    line = raw.decode().rstrip('\r\n')
                    if data is not None:
                        if line == '.': received.append('\n'.join(data)); data = None; say('250 queued')
                        else: data.append(line)
                    elif line.upper().startswith('EHLO'): say('250 localhost')
                    elif line.upper().startswith('DATA'): data = []; say('354 go ahead')
                    elif line.upper().startswith('QUIT'): say('221 bye'); break
                    else: say('250 ok')
        smtp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SMTPHandler)
        threading.Thread(target=smtp.serve_forever, daemon=True).start()
        env = {'MIMS_SMTP_HOST': '127.0.0.1', 'MIMS_SMTP_PORT': str(smtp.server_address[1]), 'MIMS_SMTP_SECURITY': 'none', 'MIMS_SMTP_FROM': 'mims@example.test'}
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            self.assertEqual(server.send_email('riya@example.test', 'Your MIMS login code', 'Your MIMS login code is 123456.'), 'sent')
            self.assertEqual(server.send_email('student@mims.demo', 'x', 'y'), 'simulated')     # demo accounts never leave the machine
        finally:
            for k, v in old.items(): os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            smtp.shutdown(); smtp.server_close()
        self.assertTrue(received and 'To: riya@example.test' in received[0] and '123456' in received[0])
        self.assertEqual(server.send_email('riya@example.test', 'x', 'y'), 'simulated')           # no SMTP server configured

    def test_sms_request(self):
        """send_sms builds the Fast2SMS request; the network call is replaced, so no SMS is sent."""
        sent = []
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, *a): return b'{"return": true, "message": ["SMS sent successfully."]}'
        real = server.urllib.request.urlopen
        server.urllib.request.urlopen = lambda req, timeout=None: (sent.append(req), FakeResponse())[1]
        os.environ['MIMS_FAST2SMS_KEY'] = 'test-key'
        try:
            self.assertEqual(server.send_sms('+91 98765 00119', 'Your MIMS login code is 123456.'), 'sent')
        finally:
            server.urllib.request.urlopen = real; os.environ.pop('MIMS_FAST2SMS_KEY')
        req = sent[0]; body = json.loads(req.data)
        self.assertEqual((req.full_url, req.get_method(), req.get_header('Authorization')), ('https://www.fast2sms.com/dev/bulkV2', 'POST', 'test-key'))
        self.assertEqual((body['route'], body['numbers']), ('q', '9876500119'))
        self.assertEqual(server.send_sms('9876500119', 'x'), 'simulated')                         # no gateway configured

    def test_role_visibility(self):
        student = self.client('student@mims.demo'); coord = self.client('coordinator@mims.demo'); admin = self.client('admin@mims.demo'); sharma = self.client('interviewer@mims.demo')
        s = self.state(student)
        self.assertNotIn('users', s); self.assertNotIn('performance', s)
        self.assertTrue(all(r['student_name'] == 'Aanya Goel' for r in s['registrations']), 'students see only their own records')
        self.assertFalse(any(x['status'] == 'Draft' for x in s['slots']))
        self.assertTrue(all(x['interviewer'] == 5 for x in self.state(sharma)['slots']), 'interviewers see only their slots')
        self.assertNotIn('users', self.state(coord)); self.assertIn('performance', self.state(coord))
        a = self.state(admin)
        self.assertTrue(a['users'] and a['audit'] and a['performance'])
        self.expect(self.call(coord, 'user', {'id': 1, 'status': 'Inactive'}), 403)
        self.expect(self.call(admin, 'user', {'id': 9, 'status': 'Inactive'}), 400)   # not your own account

if __name__ == '__main__': unittest.main(verbosity=2)
