"""Configuração central, lida de variáveis de ambiente com padrões seguros."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("COMANDA_DB", PROJECT_ROOT / "data" / "comanda.db"))
    )
    runs_dir: Path = field(
        default_factory=lambda: Path(os.getenv("COMANDA_RUNS", PROJECT_ROOT / "runs"))
    )
    # Reembolsos acima deste valor (em centavos) exigem aprovação humana.
    auto_refund_limit_cents: int = field(
        default_factory=lambda: _env_int("COMANDA_AUTO_REFUND_LIMIT_CENTS", 8000)
    )
    # Modelos por papel. Aliases aceitos pelo Claude Agent SDK: "haiku", "sonnet", "opus".
    reception_model: str = field(
        default_factory=lambda: os.getenv("COMANDA_RECEPTION_MODEL", "haiku")
    )
    specialist_model: str = field(
        default_factory=lambda: os.getenv("COMANDA_SPECIALIST_MODEL", "sonnet")
    )
    max_turns: int = field(default_factory=lambda: _env_int("COMANDA_MAX_TURNS", 20))


def get_settings() -> Settings:
    return Settings()
