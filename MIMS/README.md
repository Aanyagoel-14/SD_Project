# MIMS — Mock Interview Management System

Working local prototype of a mock interview management system for placement preparation. Students book practice interviews with interviewers, interviewers record structured feedback, and the placement cell manages schedules and reports.

## Start

Requires Python 3.10 or later; no packages, API keys, or database services are required.

```bash
cd MIMS
python3 server.py
```

On Windows use `python server.py` or double-click `start.cmd`. Open http://127.0.0.1:8765 in your browser.

All seeded accounts use **Demo@123**:

| Role | Email |
|---|---|
| Student | student@mims.demo |
| Administrator (Placement Cell) | admin@mims.demo |
| Interviewer (Rohan · Technical) | interviewer@mims.demo |
| Interviewer (Ananya · HR) | ananya@mims.demo |
| Interviewer (Vikram · Case) | vikram@mims.demo |
| Management | management@mims.demo |

The sign-in page has shortcuts to enter each main demo role. New registration always creates a student, regardless of submitted role fields.

## Demonstration

1. Sign in as Student. Find an interviewer, filter by date and time, choose a round (Technical, HR, Behavioural, Case study), add the role you are preparing for, and book a slot. See the confirmation in My interviews and Notifications.
2. Reschedule the interview. The previous slot becomes available again. Cancel a booking to release its slot.
3. Open Feedback. One completed HR round is seeded with scores and comments so the feedback view is visible on first start.
4. Sign out and use Admin. Register an offline interview for an existing active student. It occupies the same slot inventory as online bookings.
5. Under Schedules, assign an interviewer a date range **after the initial 14-day seeded range**. Morning is 09:00–13:00, afternoon 13:00–17:00, evening 17:00–20:00. Slots last 45 minutes and never spill past the end of a shift. All days, including weekends, are enabled in this prototype. Overlapping date assignments are rejected.
6. Edit interviewer expertise, biographies and locations under Interviewers; disable or enable accounts under Users.
7. Use the Interviewer account to view its schedule and mark its own sessions Completed or No-show. Marking an interview Completed unlocks **Give feedback**: three scores from 1 to 5 (technical knowledge, communication, problem solving) plus what went well and what to work on. Feedback can be edited later; the student is notified each time.
8. Sign back in as Student to see the new feedback under Feedback and the overall score in My interviews.
9. Use Management for read-only reports: outcomes, rounds, slot utilization, average scores and feedback coverage; export the interview CSV. Admin Reports also contains the audit trail.

## Implementation and requirement coverage

- Authentication and authorization: salted PBKDF2 passwords, server-side sessions with 12-hour expiry, HttpOnly SameSite cookies; backend role checks on every mutation.
- Users and profiles: student registration, admin account activation/deactivation, interviewer profile editing. Adding staff accounts and changing roles currently require a code/database migration.
- Schedules: predefined shifts, date-range assignments, overlap validation, automatic 45-minute slot generation.
- Discovery: interviewer profiles with expertise, date/time-range filtering, available future slots.
- Booking: online and offline registration, interview round and target role captured at booking, shared SQLite data, database uniqueness constraint, student overlap validation, immediate confirmation.
- Interview lifecycle: scoped interview lists, atomic rescheduling to the same interviewer, cancellation, completion and no-show statuses. Bookings confirm immediately; Pending is supported as a stored status but not created by the UI.
- Feedback: one feedback record per completed interview, three 1–5 scores with written strengths and improvements, editable by the interviewer or admin, visible to the student, interviewer, admin and management.
- In-app notifications for students and interviewers, including a notification when feedback is recorded; marking as read.
- Dashboards, status and round counts, slot utilization, average scores, feedback coverage, CSV export, management read-only view.
- Audit records for profile, user, schedule, interview and feedback mutations.

`server.py` implements the HTTP application, authorization, domain operations, and SQLite persistence. The static frontend is plain HTML/CSS/JavaScript. Data lives in `mims.sqlite3`, created and seeded on first start. Slot availability is derived from interview records, preventing contradictory availability flags. SQL `BEGIN IMMEDIATE` transactions serialize competing writes; a unique index prevents multiple non-cancelled interviews per slot, and a unique constraint keeps one feedback record per interview. Rescheduling failure rolls back the transaction and preserves the old slot. Completed and no-show interviews retain their slots for historical accuracy.

## Validation

```bash
python3 -m unittest test_system.py -v
```

Tests use a temporary database and real HTTP requests. They exercise booking, round validation, cancellation, rescheduling rollback, the feedback flow (only after completion, only by the right interviewer, editable, visible to the student), simultaneous online/offline booking, schedule overlap rejection, slot boundaries, role access, student-only registration, session logout and notifications.

## Prototype limits

This runs on localhost for demonstrations. Email delivery is not connected; notifications are in-app. Video links are stored as plain text in the interviewer location field; no meeting integration exists. Updates appear after actions or Refresh rather than server push. The UI loads fonts from Google Fonts when online, with system font fallbacks. There is no password reset, login throttling, account invitation, resume upload, configurable working-day calendar, or production HTTPS deployment. The code is a compact prototype rather than a fully separated service/repository architecture. Do not use real student records for this demo.

To choose a different port set `PORT` before starting. To use a different database set `MIMS_DB`. Stop the server before moving or replacing the database. Keep the database to preserve changes between runs.
