from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main_app', '0004_alter_customuser_profile_pic'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancereport',
            name='attendance_status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('PRESENT', 'Present'), ('TARDY', 'Tardy'), ('ABSENT', 'Absent'), ('EXCUSED', 'Excused'), ('DISMISSED', 'Dismissed')], default='PENDING', max_length=10),
        ),
        migrations.AddField(
            model_name='subject',
            name='attendance_type',
            field=models.CharField(choices=[('ACADEMIC', 'Academic'), ('STUDY_HALL', 'Study hall'), ('FREE_PERIOD', 'Free period'), ('DUAL_ENROLLMENT', 'Off-campus dual enrollment')], default='ACADEMIC', max_length=20),
        ),
        migrations.AddField(
            model_name='subject',
            name='end_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subject',
            name='period_number',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subject',
            name='start_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='AttendanceAnomaly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('flagged_period', models.PositiveSmallIntegerField(default=0)),
                ('flag_type', models.CharField(max_length=30)),
                ('confidence_score', models.FloatField()),
                ('summary', models.TextField()),
                ('source_records', models.JSONField(default=list)),
                ('review_status', models.CharField(choices=[('PENDING', 'Needs human review'), ('REVIEWED', 'Reviewed')], default='PENDING', max_length=10)),
                ('review_outcome', models.CharField(blank=True, choices=[('NO_ACTION', 'No action required'), ('RECORDS_CORRECTED', 'Attendance records corrected'), ('CONFIRMED', 'Discrepancy confirmed')], max_length=20)),
                ('review_notes', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('detected_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='main_app.student')),
            ],
            options={
                'ordering': ['-date', 'student__admin__last_name', 'flagged_period'],
            },
        ),
        migrations.CreateModel(
            name='AttendanceAuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('DETECTED', 'Anomaly detected'), ('REVIEWED', 'Human review completed')], max_length=10)),
                ('details', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('anomaly', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_events', to='main_app.attendanceanomaly')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='StudentAttendancePermission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('permission_type', models.CharField(choices=[('EXCUSED', 'Excused absence'), ('DISMISSED', 'Early dismissal'), ('SCHOOL_ACTIVITY', 'School-sanctioned activity'), ('OFF_CAMPUS', 'Authorized off-campus activity')], max_length=20)),
                ('approval_status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('DENIED', 'Denied')], default='PENDING', max_length=10)),
                ('period_number', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('start_time', models.TimeField(blank=True, null=True)),
                ('end_time', models.TimeField(blank=True, null=True)),
                ('is_all_day', models.BooleanField(default=False)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('authorized_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='main_app.staff')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='main_app.student')),
            ],
            options={
                'indexes': [models.Index(fields=['student', 'date', 'active'], name='main_app_stu_student_8a5bce_idx')],
            },
        ),
        migrations.AddConstraint(
            model_name='attendanceanomaly',
            constraint=models.UniqueConstraint(fields=('student', 'date', 'flagged_period'), name='unique_attendance_anomaly_per_period'),
        ),
    ]
