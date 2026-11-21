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


def get_user_home_url(user):
    role = str(getattr(user, 'user_type', ''))
    return reverse(ROLE_HOME_URLS.get(role, 'login_page'))


class LoginCheckMiddleWare(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        modulename = view_func.__module__
        user = request.user
        if user.is_authenticated:
            role = str(getattr(user, 'user_type', ''))
            if role == '1':  # Principal / Administrator (full oversight over admin, DOS, and Bursar)
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
                    'main_app.hod_views',
                    'main_app.staff_views',
                    'main_app.student_views',
                    'main_app.bursar_views',
                ):
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
