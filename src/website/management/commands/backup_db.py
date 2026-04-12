import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Dump PostgreSQL database to a compressed backup file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=None,
            help="Directory to save the backup (overrides BACKUP_DIR env var).",
        )

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        host = db.get("HOST", "localhost")
        port = str(db.get("PORT", "5432"))
        name = db["NAME"]
        user = db["USER"]
        password = db.get("PASSWORD", "")

        output_dir = options["output_dir"] or os.getenv("BACKUP_DIR", "/app/backups")
        retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"kolco24_{timestamp}.dump"
        filepath = os.path.join(output_dir, filename)

        self.stdout.write(f"Backing up database '{name}' to {filepath} ...")

        env = {**os.environ, "PGPASSWORD": password}
        result = subprocess.run(
            ["pg_dump", "-Fc", "-h", host, "-p", port, "-U", user, "-d", name, "-f", filepath],
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise CommandError(f"pg_dump failed:\n{result.stderr}")

        size = Path(filepath).stat().st_size
        self.stdout.write(self.style.SUCCESS(f"Backup created: {filepath} ({size:,} bytes)"))

        self._prune_old_backups(output_dir, retention_days)

    def _prune_old_backups(self, output_dir, retention_days):
        cutoff = datetime.now() - timedelta(days=retention_days)
        pruned = 0
        for path in Path(output_dir).glob("*.dump"):
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                pruned += 1
        if pruned:
            self.stdout.write(f"Pruned {pruned} backup(s) older than {retention_days} days.")
