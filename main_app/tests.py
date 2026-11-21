from datetime import date, time, timedelta
from decimal import Decimal
import json

from django.test import TestCase
from django.urls import reverse

from .ai_attendance import (
    analyze_student_attendance,
    analyze_teacher_attendance,
    run_ai_attendance_scan,
)
from .models import (
    AIAttendanceNotification,
    Assignment,
    Attendance,
    AttendanceReport,
    Course,
    CustomUser,
    Examination,
    FeePayment,
    FeeStructure,
    LeaveReportStaff,
    Session,
    Staff,
    Student,
    StudentResult,
    StudentWelfareRecord,
    Subject,
    TeacherAttendance,
    TimetableEntry,
)


class AIAttendanceTrackingTests(TestCase):
    def setUp(self):
        self.admin_user = CustomUser.objects.create_superuser(
            email="admin@test.com",
            password="password123",
            user_type="1",
            first_name="System",
            last_name="Admin",
            gender="M",
            address="School HQ",
        )
        self.course = Course.objects.create(name="Form One")
        self.session = Session.objects.create(
            start_year=date(2026, 1, 1), end_year=date(2026, 12, 31)
        )

        self.teacher_user = CustomUser.objects.create_user(
            email="teacher@test.com",
            password="password123",
            user_type="2",
            first_name="Sarah",
            last_name="Johnson",
            gender="F",
            address="Staff Quarters",
        )
        self.staff = self.teacher_user.staff
        self.staff.course = self.course
        self.staff.class_teacher = True
        self.staff.save()

        self.student_user = CustomUser.objects.create_user(
            email="student@test.com",
            password="password123",
            user_type="3",
            first_name="Alex",
            last_name="Smith",
            gender="M",
            address="Dorm A",
        )
        self.student = self.student_user.student
        self.student.course = self.course
        self.student.session = self.session
        self.student.save()

        self.math = Subject.objects.create(
            name="Mathematics", staff=self.staff, course=self.course, period_number=1
        )
        self.physics = Subject.objects.create(
            name="Physics", staff=self.staff, course=self.course, period_number=2
        )

    def test_student_consecutive_and_chronic_absence_notifies_admin(self):
        base_day = date(2026, 11, 10)
        statuses = ["PRESENT", "ABSENT", "ABSENT", "ABSENT"]
        for idx, st in enumerate(statuses):
            att = Attendance.objects.create(
                session=self.session,
                subject=self.math,
                date=base_day + timedelta(days=idx),
            )
            AttendanceReport.objects.create(
                student=self.student,
                attendance=att,
                status=(st == "PRESENT"),
                attendance_status=st,
            )

        profile = analyze_student_attendance(
            self.student, reference_date=base_day + timedelta(days=3), create_alerts=True
        )
        self.assertEqual(profile["consecutive_absences"], 3)
        self.assertLess(profile["attendance_rate"], 75.0)
        self.assertIn(profile["risk_level"], ("HIGH", "CRITICAL"))

        alert_types = set(
            AIAttendanceNotification.objects.filter(
                target_type="STUDENT", student=self.student
            ).values_list("alert_type", flat=True)
        )
        self.assertIn("CONSECUTIVE_ABSENCE", alert_types)
        self.assertIn("CHRONIC_ABSENCE", alert_types)

    def test_student_same_day_class_skip_notifies_admin(self):
        today = date(2026, 11, 20)
        att1 = Attendance.objects.create(session=self.session, subject=self.math, date=today)
        att2 = Attendance.objects.create(session=self.session, subject=self.physics, date=today)
        AttendanceReport.objects.create(
            student=self.student, attendance=att1, status=True, attendance_status="PRESENT"
        )
        AttendanceReport.objects.create(
            student=self.student, attendance=att2, status=False, attendance_status="ABSENT"
        )

        analyze_student_attendance(self.student, reference_date=today, create_alerts=True)
        self.assertTrue(
            AIAttendanceNotification.objects.filter(
                target_type="STUDENT", student=self.student, alert_type="CLASS_SKIP"
            ).exists()
        )

    def test_teacher_unexcused_absence_and_late_pattern_notifies_admin(self):
        day = date(2026, 11, 20)
        TeacherAttendance.objects.create(
            staff=self.staff, date=day, status="ABSENT", source="ADMIN"
        )
        profile = analyze_teacher_attendance(self.staff, reference_date=day, create_alerts=True)
        self.assertIn(profile["risk_level"], ("HIGH", "CRITICAL"))
        self.assertTrue(
            AIAttendanceNotification.objects.filter(
                target_type="TEACHER",
                staff=self.staff,
                alert_type="TEACHER_ABSENT_UNEXCUSED",
            ).exists()
        )

        # Approved leave should NOT trigger unexcused absence on next day
        next_day = day + timedelta(days=1)
        LeaveReportStaff.objects.create(
            staff=self.staff, date=next_day.isoformat(), message="Conference", status=1
        )
        TeacherAttendance.objects.create(
            staff=self.staff, date=next_day, status="ABSENT", source="ADMIN"
        )
        analyze_teacher_attendance(self.staff, reference_date=next_day, create_alerts=True)
        self.assertFalse(
            AIAttendanceNotification.objects.filter(
                target_type="TEACHER",
                staff=self.staff,
                alert_type="TEACHER_ABSENT_UNEXCUSED",
                reference_date=next_day,
            ).exists()
        )

    def test_school_wide_scan_deduplicates_alerts(self):
        today = date(2026, 11, 21)
        TeacherAttendance.objects.create(
            staff=self.staff,
            date=today,
            status="LATE",
            check_in_time=time(8, 45),
            source="SELF_CHECKIN",
        )
        first = run_ai_attendance_scan(reference_date=today, actor=self.admin_user)
        count_after_first = AIAttendanceNotification.objects.count()
        self.assertGreaterEqual(first["new_alerts"], 2)  # Teacher late + daily digest

        second = run_ai_attendance_scan(reference_date=today, actor=self.admin_user)
        self.assertEqual(second["new_alerts"], 0)
        self.assertEqual(AIAttendanceNotification.objects.count(), count_after_first)

    def test_admin_dashboard_and_notification_lifecycle(self):
        self.client.force_login(self.admin_user)

        # Record teacher absence via Admin view -> immediately notifies Admin
        resp = self.client.post(
            reverse("record_teacher_attendance"),
            {
                "staff_id": self.staff.id,
                "date": "2026-11-21",
                "status": "ABSENT",
                "notes": "Did not arrive",
            },
        )
        self.assertEqual(resp.status_code, 302)
        alert = AIAttendanceNotification.objects.filter(
            target_type="TEACHER", staff=self.staff
        ).first()
        self.assertIsNotNone(alert)
        self.assertFalse(alert.is_read)

        # Admin AI tracker & home page render with unread notification badge
        dash_resp = self.client.get(reverse("admin_ai_attendance_dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertGreaterEqual(dash_resp.context["ai_unread_count"], 1)

        home_resp = self.client.get(reverse("admin_home"))
        self.assertEqual(home_resp.status_code, 200)
        self.assertContains(home_resp, alert.title)

        # Mark read & resolve
        self.client.post(reverse("mark_ai_notification_read", args=[alert.id]))
        alert.refresh_from_db()
        self.assertTrue(alert.is_read)

        self.client.post(reverse("resolve_ai_notification", args=[alert.id]))
        alert.refresh_from_db()
        self.assertTrue(alert.is_resolved)
        self.assertEqual(alert.resolved_by, self.admin_user)

    def test_staff_checkin_and_save_attendance_auto_tracking(self):
        self.client.force_login(self.teacher_user)

        # Teacher checks in via dedicated endpoint
        checkin_resp = self.client.post(
            reverse("staff_attendance_checkin"),
            {"action": "checkin", "notes": "Arrived for class"},
        )
        self.assertEqual(checkin_resp.status_code, 302)
        self.assertTrue(TeacherAttendance.objects.filter(staff=self.staff).exists())

        # Teacher saves student attendance across two days of absence -> triggers student AI alert
        for d in ("2026-11-18", "2026-11-19"):
            save_resp = self.client.post(
                reverse("save_attendance"),
                {
                    "subject": self.math.id,
                    "session": self.session.id,
                    "date": d,
                    "student_ids": json.dumps([{"id": self.student.id, "status": "ABSENT"}]),
                },
            )
            self.assertEqual(save_resp.status_code, 200)
            self.assertEqual(save_resp.content.decode(), "OK")

        # Teacher auto-tracked for 2026-11-18 & 2026-11-19, student alerted for 2 consecutive absences
        self.assertTrue(
            TeacherAttendance.objects.filter(
                staff=self.staff, date=date(2026, 11, 19), source="AUTO_ACTIVITY"
            ).exists()
        )
        self.assertTrue(
            AIAttendanceNotification.objects.filter(
                target_type="STUDENT",
                student=self.student,
                alert_type="CONSECUTIVE_ABSENCE",
            ).exists()
        )


class SixUserRolesTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(name="Form Two")
        self.session = Session.objects.create(
            start_year=date(2026, 1, 1), end_year=date(2026, 12, 31)
        )

        self.principal = CustomUser.objects.create_superuser(
            email="principal@test.com", password="password123", user_type="1",
            first_name="Grace", last_name="Principal", gender="F", address="HQ",
        )
        self.dos = CustomUser.objects.create_user(
            email="dos@test.com", password="password123", user_type="4",
            first_name="David", last_name="Studies", gender="M", address="Block B",
        )
        self.class_teacher_user = CustomUser.objects.create_user(
            email="classteacher@test.com", password="password123", user_type="6",
            first_name="Claire", last_name="Mentor", gender="F", address="Block C",
        )
        self.class_teacher = self.class_teacher_user.staff
        self.class_teacher.course = self.course
        self.class_teacher.save()

        self.subject_teacher_user = CustomUser.objects.create_user(
            email="subjectteacher@test.com", password="password123", user_type="2",
            first_name="Sam", last_name="Science", gender="M", address="Block D",
        )
        self.subject_teacher = self.subject_teacher_user.staff
        self.subject_teacher.course = self.course
        self.subject_teacher.save()

        self.bursar = CustomUser.objects.create_user(
            email="bursar@test.com", password="password123", user_type="5",
            first_name="Ben", last_name="Accounts", gender="M", address="Finance Office",
        )
        self.student_user = CustomUser.objects.create_user(
            email="learner@test.com", password="password123", user_type="3",
            first_name="Lina", last_name="Student", gender="F", address="Dorm B",
        )
        self.student = self.student_user.student
        self.student.course = self.course
        self.student.session = self.session
        self.student.save()

        self.subject = Subject.objects.create(
            name="Biology", staff=self.subject_teacher, course=self.course, period_number=1
        )

    def test_role_profiles_and_login_redirects(self):
        self.assertTrue(self.class_teacher.class_teacher)
        self.assertFalse(self.subject_teacher.class_teacher)
        self.assertTrue(hasattr(self.dos, "directorofstudies"))
        self.assertTrue(hasattr(self.bursar, "bursar"))

        expected_redirects = [
            (self.principal, reverse("admin_home")),
            (self.dos, reverse("dos_home")),
            (self.class_teacher_user, reverse("class_teacher_dashboard")),
            (self.subject_teacher_user, reverse("staff_home")),
            (self.bursar, reverse("bursar_home")),
            (self.student_user, reverse("student_home")),
        ]
        for user, expected_url in expected_redirects:
            self.client.force_login(user)
            resp = self.client.get(reverse("login_page"))
            self.assertRedirects(resp, expected_url, fetch_redirect_response=False)

    def test_dos_workflow_and_access_control(self):
        self.client.force_login(self.dos)
        # DOS cannot access Bursar or Admin views
        self.assertRedirects(
            self.client.get(reverse("bursar_home")), reverse("dos_home"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(reverse("admin_home")), reverse("dos_home"),
            fetch_redirect_response=False,
        )

        # DOS creates Examination and Timetable slot
        self.assertEqual(self.client.get(reverse("dos_home")).status_code, 200)
        exam_resp = self.client.post(
            reverse("dos_manage_exams"),
            {
                "name": "Mid-Term Exams",
                "session": self.session.id,
                "course": self.course.id,
                "start_date": "2026-11-25",
                "end_date": "2026-11-30",
                "status": "UPCOMING",
                "description": "Term 3 mid-terms",
            },
        )
        self.assertEqual(exam_resp.status_code, 302)
        self.assertTrue(Examination.objects.filter(name="Mid-Term Exams").exists())

        tt_resp = self.client.post(
            reverse("dos_manage_timetable"),
            {
                "course": self.course.id,
                "subject": self.subject.id,
                "day_of_week": "MON",
                "start_time": "08:00",
                "end_time": "09:00",
                "room": "Lab 1",
            },
        )
        self.assertEqual(tt_resp.status_code, 302)
        self.assertTrue(TimetableEntry.objects.filter(subject=self.subject, room="Lab 1").exists())
        self.assertEqual(self.client.get(reverse("dos_academic_results")).status_code, 200)

    def test_bursar_fees_payments_receipts_and_reports(self):
        self.client.force_login(self.bursar)
        # Bursar cannot access DOS or Admin views
        self.assertRedirects(
            self.client.get(reverse("dos_home")), reverse("bursar_home"),
            fetch_redirect_response=False,
        )

        # Create fee structure & payment
        fs_resp = self.client.post(
            reverse("bursar_fee_structures"),
            {
                "course": self.course.id,
                "session": self.session.id,
                "title": "Term 3 Tuition",
                "amount": "500.00",
                "due_date": "2026-12-01",
                "description": "Standard term fee",
            },
        )
        self.assertEqual(fs_resp.status_code, 302)
        fs = FeeStructure.objects.get(title="Term 3 Tuition")

        pay_resp = self.client.post(
            reverse("bursar_payments"),
            {
                "student": self.student.id,
                "fee_structure": fs.id,
                "amount_paid": "300.00",
                "payment_date": "2026-11-21",
                "payment_method": "MOBILE_MONEY",
                "reference_code": "MM-9911",
                "notes": "First installment",
            },
        )
        self.assertEqual(pay_resp.status_code, 302)
        payment = FeePayment.objects.get(student=self.student)
        self.assertEqual(payment.amount_paid, Decimal("300.00"))
        self.assertTrue(payment.receipt_number.startswith("JASS-"))

        self.assertEqual(self.client.get(reverse("bursar_receipt", args=[payment.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("bursar_financial_reports")).status_code, 200)
        self.assertEqual(self.client.get(reverse("bursar_home")).status_code, 200)

    def test_class_teacher_welfare_subject_teacher_assignments_and_student_views(self):
        # Subject teacher creates assignment
        self.client.force_login(self.subject_teacher_user)
        assign_resp = self.client.post(
            reverse("staff_assignments"),
            {
                "title": "Cell Structure Lab Report",
                "subject": self.subject.id,
                "description": "Draw and label plant & animal cells.",
                "due_date": "2026-11-28",
                "max_score": "50",
            },
        )
        self.assertEqual(assign_resp.status_code, 302)
        self.assertTrue(Assignment.objects.filter(title="Cell Structure Lab Report").exists())

        # Class teacher logs student welfare case
        self.client.force_login(self.class_teacher_user)
        welfare_resp = self.client.post(
            reverse("class_teacher_welfare"),
            {
                "student": self.student.id,
                "category": "ACADEMIC",
                "summary": "Needs extra tutoring in Biology.",
                "action_taken": "Paired with study group.",
                "status": "OPEN",
            },
        )
        self.assertEqual(welfare_resp.status_code, 302)
        self.assertTrue(StudentWelfareRecord.objects.filter(student=self.student).exists())

        # Student sees timetable & assignment
        TimetableEntry.objects.create(
            course=self.course, subject=self.subject, day_of_week="TUE",
            start_time=time(10, 0), end_time=time(11, 0), room="Room 4",
        )
        self.client.force_login(self.student_user)
        tt_page = self.client.get(reverse("student_timetable"))
        self.assertEqual(tt_page.status_code, 200)
        self.assertContains(tt_page, "Biology")

        assign_page = self.client.get(reverse("student_assignments"))
        self.assertEqual(assign_page.status_code, 200)
        self.assertContains(assign_page, "Cell Structure Lab Report")

    def test_principal_manage_user_roles_and_oversight(self):
        self.client.force_login(self.principal)
        self.assertEqual(self.client.get(reverse("manage_user_roles")).status_code, 200)
        # Principal has oversight access to DOS and Bursar hubs
        self.assertEqual(self.client.get(reverse("dos_home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("bursar_home")).status_code, 200)

    def test_all_documents_include_school_logo_and_name(self):
        # Admin printable staff & student lists
        self.client.force_login(self.principal)
        for url_name in ("print_staff_list", "print_student_list"):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "JUBA ADVENTIST SECONDARY SCHOOL")
            self.assertContains(resp, "logo.jpeg")

        # DOS printable academic results & timetable
        self.client.force_login(self.dos)
        for url_name in ("dos_academic_results", "dos_manage_timetable"):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "JUBA ADVENTIST SECONDARY SCHOOL")
            self.assertContains(resp, "logo.jpeg")

        # Bursar printable financial report & receipt
        self.client.force_login(self.bursar)
        fs = FeeStructure.objects.create(
            course=self.course, session=self.session, title="Tuition", amount=Decimal("200.00")
        )
        pay = FeePayment.objects.create(
            student=self.student, fee_structure=fs, session=self.session,
            amount_paid=Decimal("200.00"), payment_date=date(2026, 11, 21),
            payment_method="CASH", receipt_number="JASS-2026-DOC1", recorded_by=self.bursar,
        )
        for url in (reverse("bursar_financial_reports"), reverse("bursar_receipt", args=[pay.id])):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "JUBA ADVENTIST SECONDARY SCHOOL")
            self.assertContains(resp, "logo.jpeg")

        # Teacher printable subject lists & PDF result download
        self.client.force_login(self.class_teacher_user)
        sub_list_resp = self.client.get(reverse("print_subject_student_lists"))
        self.assertEqual(sub_list_resp.status_code, 200)
        self.assertContains(sub_list_resp, "JUBA ADVENTIST SECONDARY SCHOOL")
        self.assertContains(sub_list_resp, "logo.jpeg")

        pdf_resp = self.client.get(reverse("download_student_result", args=[self.student.id]))
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")

        # Student result slip & timetable
        self.client.force_login(self.student_user)
        for url_name in ("student_view_result", "student_timetable"):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "JUBA ADVENTIST SECONDARY SCHOOL")
            self.assertContains(resp, "logo.jpeg")
