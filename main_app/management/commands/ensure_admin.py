import os

from django.core.management.base import BaseCommand

from main_app.models import Admin, CustomUser


class Command(BaseCommand):
    help = "Ensure an initial Principal/Admin account exists for fresh deployments."

    def handle(self, *args, **options):
        email = os.environ.get("ADMIN_EMAIL", "try@gmail.com").strip()
        password = os.environ.get("ADMIN_PASSWORD", "123try")
        first_name = os.environ.get("ADMIN_FIRST_NAME", "Principal")
        last_name = os.environ.get("ADMIN_LAST_NAME", "Admin")

        user = CustomUser.objects.filter(email__iexact=email).first()
        if user:
            Admin.objects.get_or_create(admin=user)
            self.stdout.write(self.style.SUCCESS(f"Admin account already exists: {user.email}"))
            return

        if CustomUser.objects.filter(user_type="1").exists():
            self.stdout.write(self.style.SUCCESS("A Principal/Admin account already exists; skipping seed."))
            return

        user = CustomUser.objects.create_superuser(
            email=email,
            password=password,
            user_type="1",
            first_name=first_name,
            last_name=last_name,
            gender="M",
            address="Juba Adventist Secondary School",
        )
        Admin.objects.get_or_create(admin=user)
        self.stdout.write(self.style.SUCCESS(f"Created initial Principal/Admin account: {user.email}"))
