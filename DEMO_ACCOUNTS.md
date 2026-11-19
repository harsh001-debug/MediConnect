# Demo accounts

Use these scripts when the demo database needs more accounts or slots. Both scripts read the database settings and seed passwords from `backend/.env`. Set passwords of at least eight characters.

## Admin, patient and starter doctors

| To create | Set these values |
| --- | --- |
| Administrator | `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD`. |
| Patient | `SEED_PATIENT_EMAIL` and `SEED_PATIENT_PASSWORD`. |
| Three fictional doctors | `SEED_DEMO_DATA=1` and `SEED_DOCTOR_PASSWORD`. |

From `backend/` in PowerShell, run the following command.

```powershell
.\.venv\Scripts\python.exe seed.py
```

On macOS or Linux, use `./.venv/bin/python seed.py`. The fictional doctor logins are `demo.doctor1@example.com`, `demo.doctor2@example.com` and `demo.doctor3@example.com`. They are published with clinic and online slots for the next six days when created. The script preserves existing accounts and passwords.

Use the normal **Sign in** page. Admin sign-in opens the admin panel. Seeded patients and doctors start with placeholder contacts, which you can change on the contact verification screen.

## West Bengal demo doctors

Names, qualifications, registrations and clinics are fictional. Every profile carries the demo label.

| Doctor | City | Specialty | Login email | Clinic / online fee |
| --- | --- | --- | --- | --- |
| Dr. Ananya Sen (Demo) | Kolkata | General Medicine | demo.wb.ananya.sen@example.com | ₹500 / ₹400 |
| Dr. Soumyajit Das (Demo) | Howrah | Cardiology | demo.wb.soumyajit.das@example.com | ₹900 / ₹700 |
| Dr. Ishita Roy (Demo) | Siliguri | Dermatology | demo.wb.ishita.roy@example.com | ₹600 / ₹500 |
| Dr. Arindam Ghosh (Demo) | Durgapur | Orthopedics | demo.wb.arindam.ghosh@example.com | ₹700 / ₹550 |
| Dr. Madhumita Pal (Demo) | Asansol | Pediatrics | demo.wb.madhumita.pal@example.com | ₹450 / ₹350 |
| Dr. Ritam Biswas (Demo) | Malda | ENT | demo.wb.ritam.biswas@example.com | ₹500 / ₹400 |

Each doctor has a 30-minute clinic slot at 10:00 AM IST and an online slot at
6:00 PM IST for each of the next seven days after seeding (84 slots total).

Set `WB_DEMO_PASSWORD` in `backend/.env`, then run from `backend/` in PowerShell.

```powershell
.\.venv\Scripts\python.exe seed_west_bengal.py
```

On macOS or Linux, use `./.venv/bin/python seed_west_bengal.py`.

Repeating this command preserves existing accounts, passwords, profile changes,
and bookings, and skips overlapping slots. The password applies only to newly
created accounts.

## Try a booking

1. Refresh the doctors page and filter by one of these cities or specialties.
2. Sign in as a doctor with an email above. On the first sign-in, use **Change** on the verification page to enter an email and mobile number you control, with the delivery providers configured. Use the updated email on future sign-ins.
3. Sign out and use a patient account to book a clinic or online slot.
4. Complete checkout with configured Razorpay test credentials.
5. Sign in as the booked doctor to inspect the appointment.
6. For online appointments, the meeting opens from 15 minutes before the slot
   until 30 minutes after its end. Create a nearer slot in the doctor dashboard
   if you want to test joining immediately.
7. Complete the appointment as the doctor, then leave a review as the patient.

These doctors are already published for booking tests. To test the separate
admin approval flow, submit a new fictional doctor application and review it
with an admin account.
