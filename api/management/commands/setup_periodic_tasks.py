"""
Register (or update) the periodic Celery Beat tasks in the database.
Run once after deploy:  python manage.py setup_periodic_tasks
"""

from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Create or update periodic Celery Beat tasks"

    def handle(self, *args, **options):
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="9",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="Europe/Istanbul",
        )

        task, created = PeriodicTask.objects.update_or_create(
            name="Daily expiry notification check",
            defaults={
                "task": "api.check_expiring_ingredients",
                "crontab": schedule,
                "enabled": True,
            },
        )

        status = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{status} periodic task: '{task.name}' → every day at 09:00 Istanbul time"
        ))
