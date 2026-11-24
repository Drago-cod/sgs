from datetime import date, time, timedelta
from decimal import Decimal
import json

from django.core.files.uploadedfile import SimpleUploadedFile
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
    AssignmentComment,
    AssignmentSubmission,
    Attendance,
    AttendanceReport,
    Course,
    CustomUser,
    Examination,
    FeePayment,
    FeeStructure,
    FeedbackStudent,
    LeaveReportStaff,
    LeaveReportStudent,
    NotificationStudent,
    Session,
    Staff,
    Student,
    StudentMessage,
    StudentResult,
    StudentWelfareRecord,
    Subject,
    TeacherAttendance,
    TeacherPerformanceFeedback,
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

        # Before fee structures exist, report should NOT falsely say all students are cleared
        empty_report = self.client.get(reverse("bursar_financial_reports"))
        self.assertEqual(empty_report.status_code, 200)
        self.assertContains(empty_report, "No fee structures configured")
        self.assertNotContains(empty_report, "All students are cleared")

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
            self.assertContains(resp, "school-doc-watermark-layer")

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

    def test_notification_center_and_quick_responses_across_roles(self):
        # Student sends feedback and a message to Class Teacher
        fb = FeedbackStudent.objects.create(
            student=self.student, feedback="Can we get more library hours?", reply=""
        )
        msg = StudentMessage.objects.create(
            sender=self.student_user,
            receiver=self.class_teacher_user,
            subject="Homework Question",
            message="When is the lab report due?",
        )
        leave = LeaveReportStudent.objects.create(
            student=self.student, date="2026-11-25", message="Medical checkup", status=0
        )
        notif = NotificationStudent.objects.create(
            student=self.student, message="Assembly starts at 8:00 AM tomorrow."
        )

        # Principal sees student feedback in Notification Center & quick-replies inline
        self.client.force_login(self.principal)
        admin_nc = self.client.get(reverse("notification_center"))
        self.assertEqual(admin_nc.status_code, 200)
        self.assertContains(admin_nc, "Can we get more library hours?")
        self.client.post(
            reverse("notification_quick_respond"),
            {"action": "reply_student_feedback", "item_id": fb.id, "reply": "Extended until 6 PM."},
        )
        fb.refresh_from_db()
        self.assertEqual(fb.reply, "Extended until 6 PM.")

        # Class Teacher sees student message & leave, quick-replies & approves leave inline
        self.client.force_login(self.class_teacher_user)
        ct_nc = self.client.get(reverse("notification_center"))
        self.assertEqual(ct_nc.status_code, 200)
        self.assertContains(ct_nc, "Homework Question")
        self.client.post(
            reverse("notification_quick_respond"),
            {"action": "reply_message", "item_id": msg.id, "reply": "It is due Friday!"},
        )
        self.assertTrue(
            StudentMessage.objects.filter(
                sender=self.class_teacher_user, receiver=self.student_user, message="It is due Friday!"
            ).exists()
        )
        self.client.post(
            reverse("notification_quick_respond"),
            {"action": "decide_student_leave", "item_id": leave.id, "decision": "approve"},
        )
        leave.refresh_from_db()
        self.assertEqual(leave.status, 1)

        # Student sees teacher's reply & announcement, acknowledges announcement inline
        self.client.force_login(self.student_user)
        stu_nc = self.client.get(reverse("notification_center"))
        self.assertEqual(stu_nc.status_code, 200)
        self.assertContains(stu_nc, "It is due Friday!")
        self.client.post(
            reverse("notification_quick_respond"),
            {"action": "read_student_announcement", "item_id": notif.id},
        )
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

        # DOS & Bursar Notification Centers render cleanly
        for user in (self.dos, self.bursar):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("notification_center")).status_code, 200)

    def test_dos_and_admin_shared_management_attendance_and_teacher_performance(self):
        # DOS can add students, teachers, subjects, terms, follow up attendance, and give performance feedback
        self.client.force_login(self.dos)

        # DOS adds a new Student
        stu_resp = self.client.post(
            reverse("add_student"),
            {
                "first_name": "Noah",
                "last_name": "Kofi",
                "email": "noah.kofi@test.com",
                "gender": "M",
                "password": "password123",
                "address": "Dorm C",
                "course": self.course.id,
                "session": self.session.id,
                "phone_number": "+211900000111",
            },
        )
        self.assertEqual(stu_resp.status_code, 302)
        self.assertTrue(Student.objects.filter(admin__email="noah.kofi@test.com").exists())

        # DOS adds a new Teacher (Subject Teacher)
        staff_resp = self.client.post(
            reverse("add_staff"),
            {
                "first_name": "Peter",
                "last_name": "Laku",
                "email": "peter.laku@test.com",
                "gender": "M",
                "password": "password123",
                "address": "Staff House 2",
                "role": "2",
                "course": self.course.id,
            },
        )
        self.assertEqual(staff_resp.status_code, 302)
        new_teacher = Staff.objects.get(admin__email="peter.laku@test.com")

        # DOS cannot escalate and create a Principal account
        esc_resp = self.client.post(
            reverse("add_staff"),
            {
                "first_name": "Fake",
                "last_name": "Admin",
                "email": "fake.admin@test.com",
                "gender": "M",
                "password": "password123",
                "address": "Nowhere",
                "role": "1",
                "course": self.course.id,
            },
        )
        self.assertFalse(CustomUser.objects.filter(email="fake.admin@test.com").exists())

        # DOS adds Subject & Term
        subj_resp = self.client.post(
            reverse("add_subject"),
            {
                "name": "Chemistry",
                "staff": new_teacher.id,
                "course": self.course.id,
                "attendance_type": "ACADEMIC",
            },
        )
        self.assertEqual(subj_resp.status_code, 302)
        self.assertTrue(Subject.objects.filter(name="Chemistry").exists())

        term_resp = self.client.post(
            reverse("add_session"),
            {"start_year": "2027-01-10", "end_year": "2027-04-20"},
        )
        self.assertEqual(term_resp.status_code, 302)
        self.assertTrue(Session.objects.filter(start_year=date(2027, 1, 10)).exists())

        # DOS follows up attendance (AI tracker + View Attendance)
        self.assertEqual(self.client.get(reverse("admin_ai_attendance_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin_view_attendance")).status_code, 200)

        # DOS gives performance feedback to the new teacher
        perf_resp = self.client.post(
            reverse("teacher_performance_feedback"),
            {
                "staff": new_teacher.id,
                "session": self.session.id,
                "category": "TEACHING_QUALITY",
                "rating": 5,
                "strengths": "Clear lab demonstrations and punctual attendance.",
                "areas_for_improvement": "Share homework rubrics earlier.",
                "recommendations": "Mentor junior teachers in lab safety.",
            },
        )
        self.assertEqual(perf_resp.status_code, 302)
        perf = TeacherPerformanceFeedback.objects.get(staff=new_teacher, reviewer=self.dos)
        self.assertEqual(perf.rating, 5)

        # Principal can also access and submit teacher performance feedback
        self.client.force_login(self.principal)
        self.assertEqual(self.client.get(reverse("teacher_performance_feedback")).status_code, 200)

        # Teacher receives feedback in Notification Center & responds inline
        self.client.force_login(new_teacher.admin)
        nc = self.client.get(reverse("notification_center"))
        self.assertEqual(nc.status_code, 200)
        self.assertContains(nc, "Clear lab demonstrations")
        self.client.post(
            reverse("notification_quick_respond"),
            {
                "action": "respond_teacher_performance",
                "item_id": perf.id,
                "teacher_response": "Thank you, I will post rubrics on Mondays.",
            },
        )
        perf.refresh_from_db()
        self.assertTrue(perf.acknowledged_by_teacher)
        self.assertEqual(perf.teacher_response, "Thank you, I will post rubrics on Mondays.")
        self.assertEqual(self.client.get(reverse("staff_performance_feedback")).status_code, 200)

    def test_google_classroom_assignment_document_workflow(self):
        # 1. Subject teacher publishes assignment with topic & late-submission allowed
        self.client.force_login(self.subject_teacher_user)
        create_resp = self.client.post(
            reverse("staff_assignments"),
            {
                "title": "Photosynthesis Essay",
                "subject": self.subject.id,
                "topic": "Unit 2: Plant Biology",
                "description": "Write a 500-word essay explaining photosynthesis.",
                "due_date": "2026-12-05",
                "max_score": "50",
                "allow_late_submission": "on",
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        assignment = Assignment.objects.get(title="Photosynthesis Essay")

        # 2. Student opens workspace, saves draft, then turns in document + file attachment
        self.client.force_login(self.student_user)
        ws_url = reverse("student_assignment_workspace", args=[assignment.id])
        self.assertEqual(self.client.get(ws_url).status_code, 200)

        self.client.post(
            ws_url,
            {
                "action": "save_draft",
                "document_title": "My Photosynthesis Report",
                "document_content": "<p>Plants convert sunlight into chemical energy.</p>",
            },
        )
        sub = AssignmentSubmission.objects.get(assignment=assignment, student=self.student)
        self.assertEqual(sub.status, "DRAFT")

        # Turn in with uploaded document file
        sample_doc = SimpleUploadedFile("essay.txt", b"Photosynthesis full report", content_type="text/plain")
        self.client.post(
            ws_url,
            {
                "action": "turn_in",
                "document_title": "My Photosynthesis Report (Final)",
                "document_content": "<h3>Photosynthesis</h3><p>Chloroplasts absorb light energy.</p>",
                "uploaded_file": sample_doc,
            },
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, "TURNED_IN")
        self.assertIsNotNone(sub.turned_in_at)
        self.assertTrue(bool(sub.uploaded_file))

        # Student posts class comment & private comment
        self.client.post(ws_url, {"action": "class_comment", "body": "Should we include diagrams?"})
        self.client.post(ws_url, {"action": "private_comment", "body": "Attached my essay!"})
        self.assertEqual(AssignmentComment.objects.filter(assignment=assignment).count(), 2)

        # Student can unsubmit and re-turn in before grading
        self.client.post(ws_url, {"action": "unsubmit"})
        sub.refresh_from_db()
        self.assertEqual(sub.status, "DRAFT")

        self.client.post(
            ws_url,
            {
                "action": "turn_in",
                "document_title": "My Photosynthesis Report (Revised)",
                "document_content": "<h3>Photosynthesis</h3><p>Updated with Calvin cycle details.</p>",
            },
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, "TURNED_IN")

        # 3. Teacher sees turned-in submission, reviews document, and grades & returns it
        self.client.force_login(self.subject_teacher_user)
        sub_hub_url = reverse("staff_assignment_submissions", args=[assignment.id])
        hub_resp = self.client.get(sub_hub_url)
        self.assertEqual(hub_resp.status_code, 200)
        self.assertContains(hub_resp, "My Photosynthesis Report (Revised)")

        self.client.post(
            sub_hub_url,
            {
                "action": "grade",
                "submission_id": sub.id,
                "score": "46",
                "teacher_feedback": "Excellent explanation of the Calvin cycle!",
            },
        )
        sub.refresh_from_db()
        self.assertEqual(sub.status, "GRADED")
        self.assertEqual(sub.score, 46.0)

        # 4. Official printable JASS document sheet includes school logo, name, content & grade
        doc_resp = self.client.get(reverse("assignment_submission_document", args=[sub.id]))
        self.assertEqual(doc_resp.status_code, 200)
        self.assertContains(doc_resp, "JUBA ADVENTIST SECONDARY SCHOOL")
        self.assertContains(doc_resp, "logo.jpeg")
        self.assertContains(doc_resp, "46.0")

        # 5. Two-way private message notifications between Teacher and Student
        # Teacher sends private message on submission -> Student notified
        self.client.post(
            sub_hub_url,
            {"action": "private_comment", "submission_id": sub.id, "body": "Can you expand paragraph 2?"},
        )
        self.client.force_login(self.student_user)
        stu_nc = self.client.get(reverse("notification_center"))
        self.assertContains(stu_nc, "Can you expand paragraph 2?")

        # Student responds to teacher's private message -> Teacher immediately sees notification & replies inline
        self.client.post(ws_url, {"action": "private_comment", "body": "Done! I expanded paragraph 2."})
        self.client.force_login(self.subject_teacher_user)
        teacher_nc = self.client.get(reverse("notification_center"))
        self.assertEqual(teacher_nc.status_code, 200)
        self.assertContains(teacher_nc, "Done! I expanded paragraph 2.")
        self.assertGreaterEqual(teacher_nc.context["nav_unread_count"], 1)

        student_pc = AssignmentComment.objects.filter(
            submission=sub, author=self.student_user, body="Done! I expanded paragraph 2."
        ).first()
        self.client.post(
            reverse("notification_quick_respond"),
            {"action": "reply_assignment_comment", "item_id": student_pc.id, "reply": "Looks great now!"},
        )
        student_pc.refresh_from_db()
        self.assertTrue(student_pc.is_read_by_recipient)
        self.assertTrue(
            AssignmentComment.objects.filter(
                submission=sub, author=self.subject_teacher_user, body="Looks great now!"
            ).exists()
        )

    def test_ai_assignment_detector_warns_and_auto_rejects_forced_submissions(self):
        assignment = Assignment.objects.create(
            title="Climate Change Essay",
            subject=self.subject,
            staff=self.subject_teacher,
            description="Explain the causes and impacts of climate change in your own words.",
            due_date=date(2026, 12, 10),
            max_score=50.0,
        )
        self.client.force_login(self.student_user)
        check_url = reverse("student_check_assignment_ai", args=[assignment.id])
        ws_url = reverse("student_assignment_workspace", args=[assignment.id])

        ai_essay = (
            "Certainly! Here is a comprehensive essay on climate change. "
            "In today's fast-paced world, it is crucial to understand the multifaceted nature "
            "of climate change, which plays a pivotal role in navigating the complexities of our planet. "
            "Furthermore, moreover, additionally, consequently, it is important to note that "
            "delving into a myriad of factors underscores the importance of fostering a deeper understanding. "
            "In conclusion, it is evident that climate change cannot be overstated."
        )

        # 1. Pre-submission live AI check clearly warns student before submitting
        check_resp = self.client.post(
            check_url,
            {"document_content": ai_essay, "paste_ratio": "0.95", "paste_events": "1"},
        )
        self.assertEqual(check_resp.status_code, 200)
        payload = check_resp.json()
        self.assertTrue(payload["is_flagged"])
        self.assertEqual(payload["verdict"], "AI_OR_COPY_PASTE_DETECTED")
        self.assertGreaterEqual(payload["ai_score"], 60.0)
        self.assertTrue(len(payload["reasons"]) >= 2)

        # 2. Student forces submission anyway -> AUTOMATICALLY REJECTED
        force_resp = self.client.post(
            ws_url,
            {
                "action": "turn_in",
                "document_title": "Climate Change",
                "document_content": ai_essay,
                "paste_ratio": "0.95",
                "paste_events": "1",
            },
            follow=True,
        )
        self.assertEqual(force_resp.status_code, 200)
        sub = AssignmentSubmission.objects.get(assignment=assignment, student=self.student)
        self.assertEqual(sub.status, "REJECTED_AI")
        self.assertTrue(sub.ai_flagged)
        self.assertEqual(sub.ai_rejection_count, 1)
        self.assertContains(force_resp, "Submission Automatically Rejected")

        # 3. Student rewrites in genuine original words -> ACCEPTED cleanly
        original_essay = (
            "Last rainy season our garden flooded twice and my uncle said the weather in Juba "
            "has become harder to predict than when he was young. Cutting down trees for charcoal "
            "and burning fuel from cars and generators adds heat-trapping gases to the air. "
            "At school we planted twelve neem seedlings behind the classroom block to give shade "
            "and cool the compound."
        )
        ok_check = self.client.post(
            check_url,
            {"document_content": original_essay, "paste_ratio": "0.0", "paste_events": "0"},
        ).json()
        self.assertFalse(ok_check["is_flagged"])
        self.assertEqual(ok_check["verdict"], "ORIGINAL")

        submit_ok = self.client.post(
            ws_url,
            {
                "action": "turn_in",
                "document_title": "Climate Change in Juba",
                "document_content": original_essay,
                "paste_ratio": "0.0",
                "paste_events": "0",
            },
        )
        self.assertEqual(submit_ok.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.status, "TURNED_IN")
        self.assertFalse(sub.ai_flagged)


class ELearningVirtualClassroomEndToEndTests(TestCase):
    """End-to-end verification of the SGS eLearning & Virtual Classroom module (Section 23)."""

    def setUp(self):
        self.course = Course.objects.create(name="Senior 2 (S2)")
        self.session = Session.objects.create(
            start_year=date(2026, 1, 10),
            end_year=date(2026, 4, 20),
        )
        self.admin_user = CustomUser.objects.create_superuser(
            email="elearn_admin@school.test",
            password="pass",
            first_name="Head",
            last_name="Principal",
            gender="M",
            address="Juba",
            user_type="1",
        )
        self.teacher_user = CustomUser.objects.create_user(
            email="elearn_teacher@school.test",
            password="pass",
            first_name="Grace",
            last_name="Teacher",
            gender="F",
            address="Juba",
            user_type="2",
        )
        self.teacher = Staff.objects.get(admin=self.teacher_user)
        self.teacher.course = self.course
        self.teacher.save()

        self.subject = Subject.objects.create(
            name="Mathematics",
            staff=self.teacher,
            course=self.course,
        )

        self.student_user = CustomUser.objects.create_user(
            email="elearn_student@school.test",
            password="pass",
            first_name="Daniel",
            last_name="Learner",
            gender="M",
            address="Juba",
            user_type="3",
        )
        self.student = Student.objects.get(admin=self.student_user)
        self.student.course = self.course
        self.student.session = self.session
        self.student.save()

        self.parent_user = CustomUser.objects.create_user(
            email="elearn_parent@school.test",
            password="pass",
            first_name="Mary",
            last_name="Guardian",
            gender="F",
            address="Juba",
            user_type="7",
        )
        from .models import ParentGuardian
        self.guardian, _ = ParentGuardian.objects.get_or_create(admin=self.parent_user)
        self.guardian.students.add(self.student)

    def test_full_end_to_end_elearning_virtual_classroom_workflow(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import (
            AILearningInteraction,
            AssessmentAttempt,
            DiscussionThread,
            LearningMaterial,
            LearningTopic,
            LectureRecording,
            MaterialAccessLog,
            NotificationStudent,
            OnlineAssessment,
            QuestionBankItem,
            VirtualAttendanceRecord,
            VirtualClassroom,
        )

        # 1. Teacher opens eLearning studio & subject portal, creates Topic & uploads Lesson Notes
        self.client.force_login(self.teacher_user)
        self.assertEqual(self.client.get(reverse("elearning_dashboard")).status_code, 200)
        portal_url = reverse("elearning_subject_portal", args=[self.subject.id])
        self.assertEqual(self.client.get(portal_url).status_code, 200)

        self.client.post(portal_url, {
            "action": "create_topic",
            "title": "Topic 1: Quadratic Equations",
            "order": "1",
            "description": "Factorization and quadratic formula",
            "learning_objectives": "Solve quadratic equations accurately.",
            "is_published": "on",
        })
        topic = LearningTopic.objects.get(subject=self.subject, title="Topic 1: Quadratic Equations")

        pdf_file = SimpleUploadedFile("quadratics.pdf", b"%PDF-1.4 sample notes", content_type="application/pdf")
        self.client.post(portal_url, {
            "action": "upload_material",
            "topic": topic.id,
            "title": "Quadratics Study Guide",
            "material_type": "PDF",
            "description": "Core worked examples",
            "file": pdf_file,
            "rich_content": "A quadratic equation has the standard form ax^2 + bx + c = 0.",
            "allow_download": "on",
            "is_published": "on",
        })
        material = LearningMaterial.objects.get(subject=self.subject, title="Quadratics Study Guide")
        self.assertTrue(NotificationStudent.objects.filter(student=self.student, message__icontains="Quadratics Study Guide").exists())

        # 2. Teacher launches an instant live Virtual Classroom -> Students notified
        self.client.post(reverse("elearning_classrooms"), {
            "action": "instant",
            "subject_id": self.subject.id,
            "title": "Live Quadratics Workshop",
            "duration_minutes": "45",
        })
        classroom = VirtualClassroom.objects.get(title="Live Quadratics Workshop")
        self.assertEqual(classroom.status, "LIVE")
        self.assertTrue(NotificationStudent.objects.filter(student=self.student, message__icontains="LIVE NOW").exists())

        # 3. Teacher saves Whiteboard content & uploads Lecture Recording
        wb_resp = self.client.post(
            reverse("elearning_classroom_whiteboard", args=[classroom.id]),
            {"whiteboard_data": "data:image/png;base64,iVBORw0KGgo="},
        )
        self.assertEqual(wb_resp.status_code, 200)
        classroom.refresh_from_db()
        self.assertTrue(classroom.whiteboard_data.startswith("data:image/png"))

        rec_file = SimpleUploadedFile("lesson.webm", b"\x1a\x45\xdf\xa3mockwebm", content_type="video/webm")
        up_resp = self.client.post(
            reverse("elearning_upload_recording", args=[classroom.id]),
            {"video_file": rec_file, "duration_seconds": "1800", "title": "Quadratics Recording"},
        ).json()
        self.assertTrue(up_resp["ok"])
        recording = LectureRecording.objects.get(id=up_resp["recording_id"])
        self.assertFalse(recording.is_published)

        # Teacher publishes recording
        self.client.post(reverse("elearning_recordings"), {
            "action": "toggle_publish",
            "recording_id": recording.id,
        })
        recording.refresh_from_db()
        self.assertTrue(recording.is_published)

        # 4. Student logs in, accesses Subject Portal, marks material complete, joins Live Classroom & sends heartbeat
        self.client.force_login(self.student_user)
        self.assertEqual(self.client.get(reverse("elearning_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(portal_url).status_code, 200)
        self.client.post(portal_url, {"action": "toggle_material_complete", "material_id": material.id})
        self.assertTrue(MaterialAccessLog.objects.filter(student=self.student, material=material, is_completed=True).exists())

        room_resp = self.client.get(reverse("elearning_classroom_room", args=[classroom.id]))
        self.assertEqual(room_resp.status_code, 200)
        self.assertContains(room_resp, classroom.room_code)

        # Simulate active participation heartbeat
        hb = self.client.post(reverse("elearning_classroom_heartbeat", args=[classroom.id])).json()
        self.assertEqual(hb["classroom_status"], "LIVE")
        vrec = VirtualAttendanceRecord.objects.get(classroom=classroom, student=self.student)
        vrec.active_seconds = 2400  # 40 of 45 min -> >60% threshold -> PRESENT
        vrec.save()

        # Student posts in classroom chat & discussion forum
        self.client.post(reverse("elearning_classroom_chat", args=[classroom.id]), {"message": "Could you repeat step 2?", "is_question": "1"})
        self.client.post(reverse("elearning_discussions"), {"subject_id": self.subject.id, "topic": topic.id, "title": "Factoring question", "body": "How do we factor x^2 - 9?"})
        self.assertTrue(DiscussionThread.objects.filter(subject=self.subject, title="Factoring question").exists())

        # Student accesses published recording
        rec_resp = self.client.get(reverse("elearning_recordings"))
        self.assertEqual(rec_resp.status_code, 200)
        self.assertContains(rec_resp, "Quadratics Recording")

        # 5. Teacher ends lesson -> Automatically synchronizes attendance into existing SGS Attendance & AttendanceReport
        self.client.force_login(self.teacher_user)
        self.client.post(reverse("elearning_classroom_control", args=[classroom.id]), {"action": "end"})
        classroom.refresh_from_db()
        self.assertEqual(classroom.status, "ENDED")
        self.assertTrue(classroom.attendance_synced)
        self.assertTrue(AttendanceReport.objects.filter(student=self.student, attendance=classroom.synced_attendance, status=True).exists())

        # 6. Teacher creates Online Quiz (Objective MCQ + Subjective Short Answer), publishes it
        self.client.post(reverse("elearning_assessments"), {
            "title": "Quadratics Quiz 1",
            "subject": self.subject.id,
            "topic": topic.id,
            "session": self.session.id,
            "assessment_type": "QUIZ",
            "instructions": "Answer all questions.",
            "duration_minutes": "20",
            "pass_marks": "5",
            "max_attempts": "1",
            "is_published": "on",
            "allow_review": "on",
        })
        quiz = OnlineAssessment.objects.get(title="Quadratics Quiz 1")
        manage_url = reverse("elearning_assessment_manage", args=[quiz.id])
        self.client.post(manage_url, {
            "action": "add_question",
            "question_type": "MCQ",
            "marks": "4",
            "difficulty": "EASY",
            "prompt": "What are the roots of x^2 - 9 = 0?",
            "choices_text": "3 and -3\n9 and -9\n0 and 3",
            "correct_answer_text": "3 and -3",
        })
        self.client.post(manage_url, {
            "action": "add_question",
            "question_type": "SHORT_ANSWER",
            "marks": "6",
            "difficulty": "MEDIUM",
            "prompt": "Explain the discriminant b^2 - 4ac.",
            "marking_guide": "Mention positive, zero, and negative cases.",
        })
        quiz.refresh_from_db()
        self.assertEqual(quiz.total_marks, 10.0)
        q_mcq = QuestionBankItem.objects.get(assessment=quiz, question_type="MCQ")
        q_sa = QuestionBankItem.objects.get(assessment=quiz, question_type="SHORT_ANSWER")

        # 7. Student takes quiz, autosaves, and submits -> Objective auto-graded!
        self.client.force_login(self.student_user)
        take_url = reverse("elearning_take_assessment", args=[quiz.id])
        self.assertEqual(self.client.get(take_url).status_code, 200)
        as_resp = self.client.post(
            reverse("elearning_assessment_autosave", args=[quiz.id]),
            {f"q_{q_mcq.id}": "3 and -3", f"q_{q_sa.id}": "It tells how many real roots exist."},
        ).json()
        self.assertTrue(as_resp["ok"])

        self.client.post(take_url, {
            f"q_{q_mcq.id}": "3 and -3",
            f"q_{q_sa.id}": "Positive means 2 real roots, zero means 1, negative means none.",
        })
        attempt = AssessmentAttempt.objects.get(assessment=quiz, student=self.student)
        self.assertEqual(attempt.objective_score, 4.0)
        self.assertEqual(attempt.status, "SUBMITTED")  # Awaiting teacher manual grade for subjective Q

        # 8. Teacher grades subjective question & publishes results
        self.client.force_login(self.teacher_user)
        sa_ans = attempt.answers.get(question=q_sa)
        self.client.post(manage_url, {
            "action": "grade_attempt",
            "attempt_id": attempt.id,
            f"marks_{sa_ans.id}": "5",
            f"comment_{sa_ans.id}": "Clear explanation!",
            "teacher_feedback": "Excellent effort.",
        })
        self.client.post(manage_url, {"action": "toggle_publish_results"})
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "GRADED")
        self.assertEqual(attempt.total_score, 9.0)
        self.assertEqual(attempt.percentage, 90.0)
        self.assertTrue(attempt.passed)

        # Student views published result & uses AI Study Assistant
        self.client.force_login(self.student_user)
        res_resp = self.client.get(reverse("elearning_attempt_result", args=[attempt.id]))
        self.assertContains(res_resp, "90.0%")
        ai_resp = self.client.post(reverse("elearning_ai_assistant"), {
            "mode": "REVISION_NOTES",
            "subject_id": self.subject.id,
            "topic_id": topic.id,
            "prompt": "Summarize quadratic equations for revision",
        })
        self.assertEqual(ai_resp.status_code, 200)
        self.assertTrue(AILearningInteraction.objects.filter(user=self.student_user, mode="REVISION_NOTES").exists())

        # 9. Teacher uses AI to generate draft quiz -> Requires teacher review & stays unpublished on import
        self.client.force_login(self.teacher_user)
        self.client.post(reverse("elearning_ai_assistant"), {
            "mode": "GENERATE_QUIZ",
            "subject_id": self.subject.id,
            "topic_id": topic.id,
            "prompt": "Draft a short quiz on quadratics",
        })
        ai_quiz_inter = AILearningInteraction.objects.filter(user=self.teacher_user, mode="GENERATE_QUIZ").first()
        self.assertTrue(ai_quiz_inter.requires_teacher_review)

        # 10. Parent/Guardian can monitor linked child's published results & attendance, but CANNOT join live classroom
        self.client.force_login(self.parent_user)
        parent_resp = self.client.get(reverse("parent_guardian_portal"))
        self.assertEqual(parent_resp.status_code, 200)
        self.assertContains(parent_resp, "Daniel Learner")
        self.assertContains(parent_resp, "90.0%")
        blocked_room = self.client.get(reverse("elearning_classroom_room", args=[classroom.id]))
        self.assertEqual(blocked_room.status_code, 302)  # Blocked from joining live teacher classroom

        # 11. Administrator views eLearning Command Center & exports CSV Analytics
        self.client.force_login(self.admin_user)
        admin_el = self.client.get(reverse("elearning_dashboard"))
        self.assertEqual(admin_el.status_code, 200)
        csv_resp = self.client.get(reverse("elearning_analytics") + "?export=csv")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp["Content-Type"])
        self.assertContains(csv_resp, "Mathematics")

