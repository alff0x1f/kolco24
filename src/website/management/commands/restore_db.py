import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restore PostgreSQL database from a backup file."

    def add_arguments(self, parser):
        parser.add_argument(
            "backup_file",
            nargs="?",
            help="Path to the backup file to restore.",
        )
        parser.add_argument(
            "--latest",
            action="store_true",
            help="Restore the most recent backup from BACKUP_DIR.",
        )
        parser.add_argument(
            "--no-confirm",
            action="store_true",
            help="Skip the confirmation prompt (useful in scripts).",
        )

    def handle(self, *args, **options):
        filepath = self._resolve_file(options)

        db = settings.DATABASES["default"]
        host = db.get("HOST", "localhost")
        port = str(db.get("PORT", "5432"))
        name = db["NAME"]
        user = db["USER"]
        password = db.get("PASSWORD", "")

        if not options["no_confirm"]:
            self._confirm(filepath, name)

        self.stdout.write(f"Restoring '{name}' from {filepath} ...")

        env = {**os.environ, "PGPASSWORD": password}
        result = subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "-Fc",
                "-h",
                host,
                "-p",
                port,
                "-U",
                user,
                "-d",
                name,
                str(filepath),
            ],
            env=env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            # pg_restore may emit non-fatal warnings to stderr and still exit 0,
            # but a non-zero exit means something actually failed.
            raise CommandError(f"pg_restore failed:\n{result.stderr}")

        if result.stderr:
            self.stderr.write(result.stderr)

        self.stdout.write(self.style.SUCCESS("Restore completed successfully."))

    def _resolve_file(self, options):
        if options["backup_file"]:
            path = Path(options["backup_file"])
            if not path.exists():
                raise CommandError(f"File not found: {path}")
            return path

        if options["latest"]:
            backup_dir = os.getenv("BACKUP_DIR", "/app/backups")
            dumps = sorted(
                Path(backup_dir).glob("*.dump"), key=lambda p: p.stat().st_mtime
            )
            if not dumps:
                raise CommandError(f"No .dump files found in {backup_dir}")
            return dumps[-1]

        raise CommandError(
            "Provide a backup file path or use "
            "--latest to restore the most recent backup."
        )

    def _confirm(self, filepath, db_name):
        self.stderr.write(
            self.style.WARNING(
                f"\nWARNING: This will overwrite the database '{db_name}' "
                f"with data from:\n  {filepath}\n"
            )
        )
        if not sys.stdin.isatty():
            raise CommandError(
                "Restore requires confirmation. "
                "Pass --no-confirm to skip in non-interactive mode."
            )
        answer = input("Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            raise CommandError("Restore cancelled.")
