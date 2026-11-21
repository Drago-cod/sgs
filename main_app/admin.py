from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *
# Register your models here.


class UserModel(UserAdmin):
    ordering = ('email',)


admin.site.register(CustomUser, UserModel)
admin.site.register(Staff)
admin.site.register(Student)
admin.site.register(Course)
admin.site.register(Subject)
admin.site.register(Session)
admin.site.register(TeacherAttendance)
admin.site.register(AIAttendanceNotification)
admin.site.register(AttendanceAnomaly)
admin.site.register(DirectorOfStudies)
admin.site.register(Bursar)
admin.site.register(Examination)
admin.site.register(TimetableEntry)
admin.site.register(Assignment)
admin.site.register(StudentWelfareRecord)
admin.site.register(FeeStructure)
admin.site.register(FeePayment)
