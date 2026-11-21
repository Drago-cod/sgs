from django.utils.deprecation import MiddlewareMixin
from django.urls import reverse
from django.shortcuts import redirect


ROLE_HOME_URLS = {
    '1': 'admin_home',
    '2': 'staff_home',
    '3': 'student_home',
    '4': 'dos_home',
    '5': 'bursar_home',
    '6': 'class_teacher_dashboard',
}

# Shared academic, roster, attendance follow-up, and teacher performance views
# accessible by BOTH Principal/Administrator ('1') and Director of Studies ('4').
DOS_ALLOWED_HOD_VIEWS = {
    'add_student',
    'manage_student',
    'edit_student',
    'print_student_list',
    'add_staff',
    'manage_staff',
    'edit_staff',
    'print_staff_list',
    'manage_class_teachers',
    'add_course',
    'manage_course',
    'edit_course',
    'add_subject',
    'manage_subject',
    'edit_subject',
    'add_session',
    'manage_session',
    'edit_session',
    'check_email_availability',
    'admin_view_attendance',
    'get_admin_attendance',
    'admin_attendance_audit',
    'scan_attendance_anomalies',
    'review_attendance_anomaly',
    'admin_ai_attendance_dashboard',
    'run_ai_attendance_scan_view',
    'record_teacher_attendance',
    'mark_ai_notification_read',
    'resolve_ai_notification',
    'mark_all_ai_notifications_read',
    'teacher_performance_feedback',
}


def get_user_home_url(user):
    role = str(getattr(user, 'user_type', ''))
    return reverse(ROLE_HOME_URLS.get(role, 'login_page'))


class LoginCheckMiddleWare(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        modulename = view_func.__module__
        user = request.user
        if user.is_authenticated:
            role = str(getattr(user, 'user_type', ''))
            if role == '1':  # Principal / Administrator (full oversight)
                if modulename in ('main_app.student_views', 'main_app.staff_views'):
                    return redirect(reverse('admin_home'))
            elif role in ('2', '6'):  # Subject Teacher ('2') & Class Teacher ('6')
                if modulename in ('main_app.student_views', 'main_app.dos_views', 'main_app.bursar_views'):
                    return redirect(get_user_home_url(user))
                if modulename == 'main_app.hod_views' and view_func.__name__ != 'view_student_leave':
                    return redirect(get_user_home_url(user))
            elif role == '3':  # Student
                if modulename in (
                    'main_app.hod_views',
                    'main_app.staff_views',
                    'main_app.dos_views',
                    'main_app.bursar_views',
                ):
                    return redirect(reverse('student_home'))
            elif role == '4':  # Director of Studies (DOS)
                if modulename in (
                    'main_app.staff_views',
                    'main_app.student_views',
                    'main_app.bursar_views',
                ):
                    return redirect(reverse('dos_home'))
                if modulename == 'main_app.hod_views' and view_func.__name__ not in DOS_ALLOWED_HOD_VIEWS:
                    return redirect(reverse('dos_home'))
            elif role == '5':  # Bursar / Accountant
                if modulename in (
                    'main_app.hod_views',
                    'main_app.staff_views',
                    'main_app.student_views',
                    'main_app.dos_views',
                ):
                    return redirect(reverse('bursar_home'))
            else:
                return redirect(reverse('login_page'))
        else:
            if (
                request.path == reverse('login_page')
                or modulename == 'django.contrib.auth.views'
                or request.path == reverse('user_login')
            ):
                pass
            else:
                return redirect(reverse('login_page'))
