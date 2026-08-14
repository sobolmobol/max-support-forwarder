"""Конфигурация приложения из переменных окружения."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} должен быть true или false")


def _int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом") from exc
    if value < minimum:
        raise ValueError(f"{name} должен быть не меньше {minimum}")
    return value


def _float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} должен быть числом") from exc
    if value < minimum:
        raise ValueError(f"{name} должен быть не меньше {minimum}")
    return value


def _path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()

@dataclass(frozen=True)
class BotConfig:
    """Полная конфигурация бота."""

    phone: str
    target_chat_id: int
    work_dir: Path
    session_name: str
    state_db: Path
    legacy_mapping_file: Path
    sms_code_file: Path
    password_file: Path
    two_factor_password: str | None = field(default=None, repr=False)

    log_level: str = "INFO"
    pymax_log_level: str = "WARNING"
    log_file: Path | None = None
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    reconnect_delay: float = 5.0
    request_timeout: float = 60.0
    outbox_poll_seconds: float = 2.0
    cleanup_interval_seconds: int = 3600
    mapping_retention_days: int = 90
    job_max_attempts: int = 0
    include_phone: bool = True

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Создать и провалидировать конфигурацию из ``.env``."""
        phone = os.getenv("MAX_PHONE", "").strip()
        if not phone:
            raise ValueError("MAX_PHONE не указан")

        try:
            target_chat_id = int(os.getenv("MAX_TARGET_CHAT_ID", "0"))
        except ValueError as exc:
            raise ValueError("MAX_TARGET_CHAT_ID должен быть целым числом") from exc
        if target_chat_id == 0:
            raise ValueError("MAX_TARGET_CHAT_ID не указан")

        work_dir = _path(os.getenv("MAX_WORK_DIR", "cache"))
        state_db = _path(os.getenv("MAX_STATE_DB", str(work_dir / "relay_state.db")))
        legacy_mapping_file = _path(
            os.getenv("MAX_LEGACY_MAPPING_FILE", str(work_dir / "message_mappings.json"))
        )
        sms_code_file = _path(
            os.getenv("MAX_SMS_CODE_FILE", str(work_dir / "sms_code.txt"))
        )
        password_file = _path(
            os.getenv("MAX_2FA_PASSWORD_FILE", str(work_dir / "2fa_password.txt"))
        )

        debug = _bool("BOT_DEBUG", False)
        log_level = "DEBUG" if debug else os.getenv("BOT_LOG_LEVEL", "INFO").upper()
        pymax_log_level = os.getenv("PYMAX_LOG_LEVEL", "WARNING").upper()
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if log_level not in valid_levels:
            raise ValueError("BOT_LOG_LEVEL содержит неизвестный уровень")
        if pymax_log_level not in valid_levels:
            raise ValueError("PYMAX_LOG_LEVEL содержит неизвестный уровень")

        log_file_value = os.getenv("BOT_LOG_FILE", "logs/bot.log").strip()
        log_file = _path(log_file_value) if log_file_value else None

        return cls(
            phone=phone,
            target_chat_id=target_chat_id,
            work_dir=work_dir,
            session_name=os.getenv("MAX_SESSION_NAME", "forwarder_bot.db"),
            state_db=state_db,
            legacy_mapping_file=legacy_mapping_file,
            sms_code_file=sms_code_file,
            password_file=password_file,
            two_factor_password=os.getenv("MAX_2FA_PASSWORD") or None,
            log_level=log_level,
            pymax_log_level=pymax_log_level,
            log_file=log_file,
            log_max_bytes=_int("BOT_LOG_MAX_BYTES", 10 * 1024 * 1024, minimum=1024),
            log_backup_count=_int("BOT_LOG_BACKUP_COUNT", 5, minimum=1),
            reconnect_delay=_float("BOT_RECONNECT_DELAY", 5.0, minimum=0.1),
            request_timeout=_float("BOT_REQUEST_TIMEOUT", 60.0, minimum=1.0),
            outbox_poll_seconds=_float("BOT_OUTBOX_POLL_SECONDS", 2.0, minimum=0.1),
            cleanup_interval_seconds=_int("BOT_CLEANUP_INTERVAL_SECONDS", 3600, minimum=60),
            mapping_retention_days=_int("BOT_MAPPING_RETENTION_DAYS", 90, minimum=1),
            job_max_attempts=_int("BOT_JOB_MAX_ATTEMPTS", 0, minimum=0),
            include_phone=_bool("BOT_INCLUDE_PHONE", True),
        )

    def prepare_directories(self) -> None:
        """Создать каталоги состояния и логов."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.sms_code_file.parent.mkdir(parents=True, exist_ok=True)
        self.password_file.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def to_public_dict(self) -> dict[str, object]:
        """Вернуть настройки без номера, пароля и токенов."""
        return {
            "phone": f"***{self.phone[-2:]}",
            "target_chat_id": self.target_chat_id,
            "work_dir": str(self.work_dir),
            "session_name": self.session_name,
            "state_db": str(self.state_db),
            "log_level": self.log_level,
            "pymax_log_level": self.pymax_log_level,
            "reconnect_delay": self.reconnect_delay,
        }
