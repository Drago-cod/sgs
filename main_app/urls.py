"""student_management_system URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path
from django.views.generic.base import RedirectView

from main_app.EditResultView import EditResultView

from . import bursar_views, dos_views, hod_views, staff_views, student_views, views
from .hod_views import manage_class_teachers
from .staff_views import class_teacher_dashboard, view_class_students, send_class_results_sms, view_individual_results, view_student_result, send_result_sms, download_student_result, class_teacher_messages

urlpatterns = [
    path("", views.login_page, name='login_page'),
    path("index.html", RedirectView.as_view(url='/', permanent=False)),
    path("index2.html", RedirectView.as_view(url='/', permanent=False)),
    path("index3.html", RedirectView.as_view(url='/', permanent=False)),
    path("get_attendance", views.get_attendance, name='get_attendance'),
    path("firebase-messaging-sw.js", views.showFirebaseJS, name='showFirebaseJS'),
    path("doLogin/", views.doLogin, name='user_login'),
    path("logout_user/", views.logout_user, name='user_logout'),
    path("notifications/", views.notification_center, name='notification_center'),
    path("notifications/respond/", views.notification_quick_respond, name='notification_quick_respond'),
    path("admin/home/", hod_views.admin_home, name='admin_home'),
    path("staff/add", hod_views.add_staff, name='add_staff'),
    path("course/add", hod_views.add_course, name='add_course'),
    path("send_student_notification/", hod_views.send_student_notification,
         name='send_student_notification'),
    path("send_staff_notification/", hod_views.send_staff_notification,
         name='send_staff_notification'),
    path("add_session/", hod_views.add_session, name='add_session'),
    path("admin_notify_student", hod_views.admin_notify_student,
         name='admin_notify_student'),
    path("admin_notify_staff", hod_views.admin_notify_staff,
         name='admin_notify_staff'),
    path("admin_view_profile", hod_views.admin_view_profile,
         name='admin_view_profile'),
    path("check_email_availability", hod_views.check_email_availability,
         name="check_email_availability"),
    path("session/manage/", hod_views.manage_session, name='manage_session'),
    path("session/edit/<int:session_id>",
         hod_views.edit_session, name='edit_session'),
    path("student/view/feedback/", hod_views.student_feedback_message,
         name="student_feedback_message",),
    path("staff/view/feedback/", hod_views.staff_feedback_message,
         name="staff_feedback_message",),
    path("student/view/leave/", hod_views.view_student_leave,
         name="view_student_leave",),
    path("staff/view/leave/", hod_views.view_staff_leave, name="view_staff_leave",),
    path("attendance/view/", hod_views.admin_view_attendance,
         name="admin_view_attendance",),
    path("attendance/ai-tracker/", hod_views.admin_ai_attendance_dashboard,
         name="admin_ai_attendance_dashboard"),
    path("attendance/ai-tracker/scan/", hod_views.run_ai_attendance_scan_view,
         name="run_ai_attendance_scan"),
    path("attendance/ai-tracker/teacher-record/", hod_views.record_teacher_attendance,
         name="record_teacher_attendance"),
    path("attendance/ai-tracker/notifications/<int:notification_id>/read/",
         hod_views.mark_ai_notification_read, name="mark_ai_notification_read"),
    path("attendance/ai-tracker/notifications/<int:notification_id>/resolve/",
         hod_views.resolve_ai_notification, name="resolve_ai_notification"),
    path("attendance/ai-tracker/notifications/mark-all-read/",
         hod_views.mark_all_ai_notifications_read, name="mark_all_ai_notifications_read"),
    path("attendance/audit/", hod_views.admin_attendance_audit,
         name="admin_attendance_audit"),
    path("attendance/audit/scan/", hod_views.scan_attendance_anomalies,
         name="scan_attendance_anomalies"),
    path("attendance/audit/<int:anomaly_id>/review/",
         hod_views.review_attendance_anomaly, name="review_attendance_anomaly"),
    path("api/attendance/audit/evaluate/", views.evaluate_attendance_audit,
         name="evaluate_attendance_audit"),
    path("attendance/fetch/", hod_views.get_admin_attendance,
         name='get_admin_attendance'),
    path("student/add/", hod_views.add_student, name='add_student'),
    path("subject/add/", hod_views.add_subject, name='add_subject'),
    path("staff/manage/", hod_views.manage_staff, name='manage_staff'),
    path("teachers/performance/", hod_views.teacher_performance_feedback,
         name='teacher_performance_feedback'),
    path("users/roles/", hod_views.manage_user_roles, name='manage_user_roles'),
    path("staff/print/", hod_views.print_staff_list, name='print_staff_list'),
    path("student/manage/", hod_views.manage_student, name='manage_student'),
    path("student/print/", hod_views.print_student_list, name='print_student_list'),
    path("course/manage/", hod_views.manage_course, name='manage_course'),
    path("subject/manage/", hod_views.manage_subject, name='manage_subject'),
    path("staff/edit/<int:staff_id>", hod_views.edit_staff, name='edit_staff'),
    path("staff/delete/<int:staff_id>",
         hod_views.delete_staff, name='delete_staff'),

    path("course/delete/<int:course_id>",
         hod_views.delete_course, name='delete_course'),

    path("subject/delete/<int:subject_id>",
         hod_views.delete_subject, name='delete_subject'),

    path("session/delete/<int:session_id>",
         hod_views.delete_session, name='delete_session'),

    path("student/delete/<int:student_id>",
         hod_views.delete_student, name='delete_student'),
    path("student/edit/<int:student_id>",
         hod_views.edit_student, name='edit_student'),
    path("course/edit/<int:course_id>",
         hod_views.edit_course, name='edit_course'),
    path("subject/edit/<int:subject_id>",
         hod_views.edit_subject, name='edit_subject'),


    # Staff
    path("staff/home/", staff_views.staff_home, name='staff_home'),
    path("staff/apply/leave/", staff_views.staff_apply_leave,
         name='staff_apply_leave'),
    path("staff/feedback/", staff_views.staff_feedback, name='staff_feedback'),
    path("staff/view/profile/", staff_views.staff_view_profile,
         name='staff_view_profile'),
    path("staff/attendance/take/", staff_views.staff_take_attendance,
         name='staff_take_attendance'),
    path("staff/attendance/checkin/", staff_views.staff_attendance_checkin,
         name='staff_attendance_checkin'),
    path("staff/attendance/update/", staff_views.staff_update_attendance,
         name='staff_update_attendance'),
    path("staff/get_students/", staff_views.get_students, name='get_students'),
    path("staff/attendance/fetch/", staff_views.get_student_attendance,
         name='get_student_attendance'),
    path("staff/attendance/save/",
         staff_views.save_attendance, name='save_attendance'),
    path("staff/attendance/update/",
         staff_views.update_attendance, name='update_attendance'),
    path("staff/fcmtoken/", staff_views.staff_fcmtoken, name='staff_fcmtoken'),
    path("staff/view/notification/", staff_views.staff_view_notification,
         name="staff_view_notification"),
    path("staff/result/add/", staff_views.staff_add_result, name='staff_add_result'),
    path("staff/result/edit/", EditResultView.as_view(),
         name='edit_student_result'),
    path('staff/result/fetch/', staff_views.fetch_student_result,
         name='fetch_student_result'),
     path('send_student_result/', staff_views.send_student_result, name='send_student_result'),



    # Student
    path("student/home/", student_views.student_home, name='student_home'),
    path("student/view/attendance/", student_views.student_view_attendance,
         name='student_view_attendance'),
    path("student/apply/leave/", student_views.student_apply_leave,
         name='student_apply_leave'),
    path("student/feedback/", student_views.student_feedback,
         name='student_feedback'),
    path("student/view/profile/", student_views.student_view_profile,
         name='student_view_profile'),
    path("student/fcmtoken/", student_views.student_fcmtoken,
         name='student_fcmtoken'),
    path("student/view/notification/", student_views.student_view_notification,
         name="student_view_notification"),
    path('student/view/result/', student_views.student_view_result,
         name='student_view_result'),
    path('student/timetable/', student_views.student_timetable,
         name='student_timetable'),
    path('student/assignments/', student_views.student_assignments,
         name='student_assignments'),
    path('student/messages/', student_views.student_messages,
         name='student_messages'),

    # Subject Teacher Assignments & Class Teacher Welfare
    path('staff/assignments/', staff_views.staff_assignments,
         name='staff_assignments'),
    path('staff/performance/', staff_views.staff_performance_feedback,
         name='staff_performance_feedback'),
    path('staff/assignments/<int:assignment_id>/delete/',
         staff_views.staff_delete_assignment, name='staff_delete_assignment'),
    path('staff/welfare/', staff_views.class_teacher_welfare,
         name='class_teacher_welfare'),
    path('staff/welfare/<int:record_id>/update/',
         staff_views.class_teacher_update_welfare_status,
         name='class_teacher_update_welfare_status'),

    # Director of Studies (DOS)
    path('dos/home/', dos_views.dos_home, name='dos_home'),
    path('dos/exams/', dos_views.dos_manage_exams, name='dos_manage_exams'),
    path('dos/exams/<int:exam_id>/status/', dos_views.dos_update_exam_status,
         name='dos_update_exam_status'),
    path('dos/exams/<int:exam_id>/delete/', dos_views.dos_delete_exam,
         name='dos_delete_exam'),
    path('dos/timetable/', dos_views.dos_manage_timetable, name='dos_manage_timetable'),
    path('dos/timetable/<int:entry_id>/delete/', dos_views.dos_delete_timetable_entry,
         name='dos_delete_timetable_entry'),
    path('dos/results/', dos_views.dos_academic_results, name='dos_academic_results'),
    path('dos/academics/', dos_views.dos_manage_academics, name='dos_manage_academics'),
    path('dos/profile/', dos_views.dos_view_profile, name='dos_view_profile'),

    # Bursar / Accountant
    path('bursar/home/', bursar_views.bursar_home, name='bursar_home'),
    path('bursar/fees/', bursar_views.bursar_fee_structures, name='bursar_fee_structures'),
    path('bursar/fees/<int:fee_id>/delete/', bursar_views.bursar_delete_fee_structure,
         name='bursar_delete_fee_structure'),
    path('bursar/payments/', bursar_views.bursar_payments, name='bursar_payments'),
    path('bursar/receipt/<int:payment_id>/', bursar_views.bursar_receipt, name='bursar_receipt'),
    path('bursar/reports/', bursar_views.bursar_financial_reports,
         name='bursar_financial_reports'),
    path('bursar/profile/', bursar_views.bursar_view_profile, name='bursar_view_profile'),
         #New urls
    path('manage_class_teachers', manage_class_teachers, name='manage_class_teachers'),
    path('class_teacher_dashboard', class_teacher_dashboard, name='class_teacher_dashboard'),
    path('view_class_students', view_class_students, name='view_class_students'),
    path('staff/subjects/print-student-lists/', staff_views.print_subject_student_lists,
         name='print_subject_student_lists'),
    path('send_class_results_sms', send_class_results_sms, name='send_class_results_sms'),
    path('view_individual_results', view_individual_results, name='view_individual_results'),
    path('view_student_result/<int:student_id>', view_student_result, name='view_student_result'),
    path('send_result_sms/<int:student_id>/', send_result_sms, name='send_result_sms'),
    path('download_student_result/<int:student_id>/', download_student_result, name='download_student_result'),
    path('class_teacher_messages/', class_teacher_messages, name='class_teacher_messages'),
]
