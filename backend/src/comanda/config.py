"""Configuração central, lida de variáveis de ambiente com padrões seguros."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Raiz do repositório: `evals/` fica fora do pacote Python, ao lado de `backend/` e `web/`.
REPO_ROOT = PROJECT_ROOT.parent
OLLAMA_DEFAULT_HOST = "http://localhost:11434"
# Janela de contexto pedida ao Ollama. A padrão dele pode ser 4096 tokens, e o que passa é cortado
# do começo sem aviso: o juiz perderia a instrução de sistema (política e formato da resposta).
OLLAMA_DEFAULT_NUM_CTX = 16384


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("COMANDA_DB", PROJECT_ROOT / "data" / "comanda.db"))
    )
    runs_dir: Path = field(default_factory=lambda: Path(os.getenv("COMANDA_RUNS", PROJECT_ROOT / "runs")))
    # Reembolsos acima deste valor (em centavos) exigem aprovação humana.
    auto_refund_limit_cents: int = field(
        default_factory=lambda: _env_int("COMANDA_AUTO_REFUND_LIMIT_CENTS", 8000)
    )
    # Modelos por papel. Aliases aceitos pelo Claude Agent SDK: "haiku", "sonnet", "opus".
    reception_model: str = field(default_factory=lambda: os.getenv("COMANDA_RECEPTION_MODEL", "haiku"))
    specialist_model: str = field(default_factory=lambda: os.getenv("COMANDA_SPECIALIST_MODEL", "sonnet"))
    max_turns: int = field(default_factory=lambda: _env_int("COMANDA_MAX_TURNS", 20))
    # Origens que podem chamar a API pelo navegador, separadas por vírgula: o painel em dev.
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in os.getenv("COMANDA_CORS_ORIGINS", "http://localhost:3000").split(",")
            if o.strip()
        )
    )
    # Avaliação: cenários em YAML e o provedor do cliente simulado (ADR-0004). A chave do
    # Gemini não fica aqui: o adaptador a lê de `GEMINI_API_KEY` e ela nunca é gravada.
    scenarios_dir: Path = field(
        default_factory=lambda: Path(os.getenv("COMANDA_SCENARIOS", REPO_ROOT / "evals" / "scenarios"))
    )
    eval_provider: str = field(default_factory=lambda: os.getenv("COMANDA_EVAL_PROVIDER", "gemini"))
    gemini_model: str = field(default_factory=lambda: os.getenv("COMANDA_GEMINI_MODEL", "gemini-3.8-flash"))
    # Ollama: modelo já baixado (`ollama pull`), host e janela de contexto pedida (ADR-0026).
    ollama_model: str = field(default_factory=lambda: os.getenv("COMANDA_OLLAMA_MODEL", "qwen3:8b"))
    ollama_host: str = field(default_factory=lambda: os.getenv("COMANDA_OLLAMA_HOST", OLLAMA_DEFAULT_HOST))
    ollama_num_ctx: int = field(
        default_factory=lambda: _env_int("COMANDA_OLLAMA_NUM_CTX", OLLAMA_DEFAULT_NUM_CTX)
    )
    # O juiz usa o mesmo provedor do cliente simulado, com modelo próprio (ADR-0023). Sem
    # `COMANDA_JUDGE_MODEL`, `judge_model` é o modelo do cliente simulado nesse provedor.
    judge_model_override: str | None = field(default_factory=lambda: os.getenv("COMANDA_JUDGE_MODEL") or None)
    # Relatórios versionados das rodadas de avaliação (ADR-0015).
    results_dir: Path = field(
        default_factory=lambda: Path(os.getenv("COMANDA_RESULTS", REPO_ROOT / "evals" / "results"))
    )

    @property
    def simulated_customer_model(self) -> str:
        """O modelo do cliente simulado no provedor escolhido em `COMANDA_EVAL_PROVIDER`."""
        return self.ollama_model if self.eval_provider == "ollama" else self.gemini_model

    @property
    def judge_model(self) -> str:
        return self.judge_model_override or self.simulated_customer_model


def get_settings() -> Settings:
    return Settings()
