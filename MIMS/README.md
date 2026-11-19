# MIMS — Mock Interview Management System

Working local prototype for **Team T065** (CS314 Software Development Laboratory, LNMIIT Jaipur), built from Lab Assignments 1–7. Students register for mock interview slots, the placement cell schedules slots and allocates interviewers, interviewers mark attendance and give feedback, and the administrator manages accounts.

## Start

Requires Python 3.10 or later. No packages, API keys or database server are needed.

```bash
cd MIMS
python3 server.py
```

On Windows run `python server.py` or double-click `start.cmd`. Open http://127.0.0.1:8765.

To reset the demo data, stop the server, delete `mims.sqlite3` and start it again.

## Users

Choose the **User type** on the login form, then enter the email and password. Every demo account uses the password **Demo@123**.

| User type | Email | What they do |
|---|---|---|
| Student | student@mims.demo | Find slots, register, cancel, see attendance and feedback |
| Student (not eligible for CSE-only slots) | arnav@mims.demo | Shows the eligibility check |
| Interviewer | interviewer@mims.demo, vikram@mims.demo, meera@mims.demo | See allocated slots, mark attendance, give feedback |
| Placement Cell Coordinator | coordinator@mims.demo | Create, edit, allocate, publish and cancel slots; reports |
| Administrator | admin@mims.demo | Approve sign-ups, activate/deactivate accounts, change roles; reports |

**Create account** asks for different details for each user type:

- **Student:** name, email, mobile, roll number, branch, batch, CGPA. The account is active straight away.
- **Interviewer:** name, email, mobile, employee ID, designation, domain of expertise, years of experience. The account waits for administrator approval.
- **Placement Cell Coordinator:** name, email, mobile, employee ID, department. The account waits for administrator approval.
- Administrator accounts cannot be self-registered.

Passwords need 8+ characters with an uppercase letter, a lowercase letter, a number and a special character. The rules tick off as you type, and both password fields have Show/Hide.

## Demo walkthrough

The seed data includes a completed slot (#1), a past slot with one feedback still pending (#2), three published slots (#3–#5), a draft with no interviewer (#6), a cancelled slot (#7) and one interviewer sign-up waiting for approval (Ms. Kavya Rao).

1. **Allocate an interviewer (UC-004).** Log in as Coordinator → Slots → open slot #6 → *Allocate interviewer*. Only interviewers with the same domain who are free at that time are offered. Allocate, then *Publish*. The progress bar on the slot moves from Draft to Published, and the interviewer is notified.
2. **Register for a slot (UC-009).** Log in as Student → Find slots → open a slot → *Register for this slot*. The system checks, in order, that the slot is published, registration is open, you are eligible, you are not already registered and seats are left. Cancel from the same page while registration is open. Log in as arnav@mims.demo to see the *Not eligible* message.
3. **Attendance and feedback (UC-012, UC-013).** Log in as vikram@mims.demo → My slots → open slot #2. Aanya is marked Present and needs feedback. *Give feedback* takes a score from 1 to 10 and a comment. Once every present student has feedback, the slot becomes Completed and the student sees the score under My interviews.
4. **Approve an account (UC-002).** Log in as Administrator. Kavya Rao is under *Waiting for approval*. Approve her, and she can log in and be allocated to Software Engineering slots. The Users page also deactivates accounts and changes roles.
5. **Reports (UC-016).** Coordinator and Administrator → Reports: a slot summary (registrations, attendance, average score) and student performance, both exportable as CSV.
6. **Notifications (UC-015).** The bell in the top bar shows confirmations, allocation notices, slot changes, cancellations, feedback alerts and reminders for interviews today or tomorrow.

## How the requirements are covered

- **Business rules:** only coordinators create slots (BR-001). A slot needs an interviewer before publishing (BR-002). Only published, open slots accept registrations (BR-003, BR-004). No duplicate registration (BR-005) and no overbooking, even with simultaneous requests (BR-006). Only the allocated interviewer marks attendance and gives feedback (BR-007). Scores must be 1–10 (BR-008). Feedback only for students marked Present (BR-009). Reports only for coordinator and administrator (BR-010). Cancelled slots accept no registrations (BR-011). Every login attempt, successful or failed, and every logout is written to the audit log with the user (or the unknown email), the reason and the time; passwords are never logged (BR-012, added for authentication auditability).
- **Slot lifecycle:** Draft → Allocated → Published → Completed, or Cancelled.
- **Errors** use the outcome codes from the interface design, for example `NOT_ELIGIBLE`, `SLOT_FULL`, `INTERVIEWER_UNAVAILABLE`, `ATTENDANCE_REQUIRED`, `SCORE_OUT_OF_RANGE` and `FEEDBACK_EXISTS`.
- **Security:** salted PBKDF2 password hashes, HttpOnly session cookies, role checks on every server action, and every important action written to the audit log.

## Files

- `server.py` — HTTP server, business rules and SQLite storage (standard library only).
- `static/index.html`, `static/app.js`, `static/style.css` — the web interface (plain HTML, CSS and JavaScript).
- `test_system.py` — integration tests.
- `start.cmd` — Windows launcher.

To keep the prototype small, attendance, score and comments are stored on the registration record, and a student's performance record (attended, evaluated, average) is calculated from those rows.

## Tests

```bash
python3 -m unittest test_system.py -v
```

The tests run against a real HTTP server with a temporary database. They cover sign-up for each user type and approval, interviewer allocation and publishing, registration including concurrent requests, attendance and feedback, slot completion, and what each role is allowed to see.

## Prototype limits

Runs on localhost only. Notifications are in-app; no email or SMS is sent. There is no password reset, waitlist, calendar sync or video calling, in line with the out-of-scope list in Assignment 1. Do not use real student data.
