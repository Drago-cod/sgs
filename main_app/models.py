from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.db import models
from django.contrib.auth.models import AbstractUser




class CustomUserManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = CustomUser(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        assert extra_fields["is_staff"]
        assert extra_fields["is_superuser"]
        return self._create_user(email, password, **extra_fields)


class Session(models.Model):
    start_year = models.DateField()
    end_year = models.DateField()

    def __str__(self):
        return "From " + str(self.start_year) + " to " + str(self.end_year)


class CustomUser(AbstractUser):
    USER_TYPE = ((1, "HOD"), (2, "Staff"), (3, "Student"))
    GENDER = [("M", "Male"), ("F", "Female")]
    
    
    username = None  # Removed username, using email instead
    email = models.EmailField(unique=True)
    user_type = models.CharField(default=1, choices=USER_TYPE, max_length=1)
    gender = models.CharField(max_length=1, choices=GENDER)
    profile_pic = models.ImageField(upload_to='profile_pics/', blank=True, default='')
    address = models.TextField()
    fcm_token = models.TextField(default="")  # For firebase notifications
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    def __str__(self):
        return self.last_name + ", " + self.first_name


class Admin(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)



class Course(models.Model):
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Student(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.DO_NOTHING, null=True, blank=False)
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING, null=True)
  
    # My additional field
    phone_number = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return self.admin.last_name + ", " + self.admin.first_name


class Staff(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, null=True)
    class_teacher = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.admin.last_name + " " + self.admin.first_name


class Subject(models.Model):
    ATTENDANCE_TYPES = (
        ("ACADEMIC", "Academic"),
        ("STUDY_HALL", "Study hall"),
        ("FREE_PERIOD", "Free period"),
        ("DUAL_ENROLLMENT", "Off-campus dual enrollment"),
    )

    name = models.CharField(max_length=120)
    staff = models.ForeignKey(Staff,on_delete=models.CASCADE,)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    period_number = models.PositiveSmallIntegerField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    attendance_type = models.CharField(
        max_length=20, choices=ATTENDANCE_TYPES, default="ACADEMIC")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Attendance(models.Model):
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING)
    subject = models.ForeignKey(Subject, on_delete=models.DO_NOTHING)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AttendanceReport(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("PRESENT", "Present"),
        ("TARDY", "Tardy"),
        ("ABSENT", "Absent"),
        ("EXCUSED", "Excused"),
        ("DISMISSED", "Dismissed"),
    )

    student = models.ForeignKey(Student, on_delete=models.DO_NOTHING)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE)
    status = models.BooleanField(default=False)
    attendance_status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class StudentAttendancePermission(models.Model):
    PERMISSION_TYPES = (
        ("EXCUSED", "Excused absence"),
        ("DISMISSED", "Early dismissal"),
        ("SCHOOL_ACTIVITY", "School-sanctioned activity"),
        ("OFF_CAMPUS", "Authorized off-campus activity"),
    )
    APPROVAL_STATUSES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("DENIED", "Denied"),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.DateField()
    permission_type = models.CharField(max_length=20, choices=PERMISSION_TYPES)
    approval_status = models.CharField(
        max_length=10, choices=APPROVAL_STATUSES, default="PENDING")
    period_number = models.PositiveSmallIntegerField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_all_day = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    authorized_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["student", "date", "active"]),
        ]


class LeaveReportStudent(models.Model):
    LEAVE_CATEGORIES = (
        ('sick', 'Sick Leave'),
        ('personal', 'Personal Leave'),
        ('study', 'Study Leave'),
        ('annual', 'Annual Leave'),
        ('official', 'Official / Administrative Leave'),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    leave_category = models.CharField(
        max_length=20, choices=LEAVE_CATEGORIES, default='personal')
    forwarded_to_admin = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    date = models.CharField(max_length=120, blank=True, default='')
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeaveReportStaff(models.Model):
    LEAVE_CATEGORIES = (
        ('annual', 'Annual Leave'),
        ('sick', 'Sick Leave'),
        ('personal', 'Personal Leave'),
        ('study', 'Study Leave'),
        ('official', 'Official / Administrative Leave'),
    )

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    leave_category = models.CharField(
        max_length=20, choices=LEAVE_CATEGORIES, default='personal')
    forwarded_to_admin = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    date = models.CharField(max_length=120, blank=True, default='')
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class StudentResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    test = models.FloatField(default=0)
    exam = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        if str(instance.user_type) == '1':
            Admin.objects.get_or_create(admin=instance)
        if str(instance.user_type) == '2':
            Staff.objects.get_or_create(admin=instance)
        if str(instance.user_type) == '3':
            Student.objects.get_or_create(admin=instance)


@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    if str(instance.user_type) == '1' and hasattr(instance, 'admin'):
        instance.admin.save()
    if str(instance.user_type) == '2' and hasattr(instance, 'staff'):
        instance.staff.save()
    if str(instance.user_type) == '3' and hasattr(instance, 'student'):
        instance.student.save()


# Messaging system between students and class teachers
class StudentMessage(models.Model):
    sender = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='received_messages')
    subject = models.CharField(max_length=200, default='No Subject')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    parent_message = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"From {self.sender.get_full_name()} to {self.receiver.get_full_name()} - {self.subject}"


class AttendanceAnomaly(models.Model):
    REVIEW_STATUSES = (
        ("PENDING", "Needs human review"),
        ("REVIEWED", "Reviewed"),
    )
    REVIEW_OUTCOMES = (
        ("NO_ACTION", "No action required"),
        ("RECORDS_CORRECTED", "Attendance records corrected"),
        ("CONFIRMED", "Discrepancy confirmed"),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.DateField()
    flagged_period = models.PositiveSmallIntegerField(default=0)
    flag_type = models.CharField(max_length=30)
    confidence_score = models.FloatField()
    summary = models.TextField()
    source_records = models.JSONField(default=list)
    review_status = models.CharField(
        max_length=10, choices=REVIEW_STATUSES, default="PENDING")
    review_outcome = models.CharField(
        max_length=20, choices=REVIEW_OUTCOMES, blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "student__admin__last_name", "flagged_period"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "date", "flagged_period"],
                name="unique_attendance_anomaly_per_period"),
        ]


class AttendanceAuditEvent(models.Model):
    EVENT_TYPES = (
        ("DETECTED", "Anomaly detected"),
        ("REVIEWED", "Human review completed"),
    )

    anomaly = models.ForeignKey(
        AttendanceAnomaly, on_delete=models.CASCADE, related_name="audit_events")
    event_type = models.CharField(max_length=10, choices=EVENT_TYPES)
    actor = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


# Last additional model.
class SMSLog(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    message = models.TextField()
    status = models.CharField(max_length=20)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"SMS to {self.student.admin.first_name} - {self.status}"