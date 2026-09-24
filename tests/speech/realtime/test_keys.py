"""Key loading: env first, then file; never leak key text."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from msb_v3.speech.realtime.keys import (
    InsecureKeyFileError,
    MissingKeyError,
    load_gemini_key,
    load_openai_key,
)

SECRET = "sk-test-SECRET-VALUE-123"


def _key_file(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    p = tmp_path / "gemini-api-key.txt"
    p.write_text(text)
    os.chmod(p, mode)
    return p


class TestOpenAIKey:
    def test_env_wins(self, tmp_path):
        assert load_openai_key(env={"OPENAI_API_KEY": SECRET}, dotenv_path=tmp_path / "none") == SECRET

    def test_reads_dotenv(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"OTHER=1\nOPENAI_API_KEY={SECRET}\n")
        assert load_openai_key(env={}, dotenv_path=env_file) == SECRET

    def test_dotenv_strips_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f'OPENAI_API_KEY="{SECRET}"\n')
        assert load_openai_key(env={}, dotenv_path=env_file) == SECRET

    def test_missing_raises(self, tmp_path):
        with pytest.raises(MissingKeyError):
            load_openai_key(env={}, dotenv_path=tmp_path / "none")

    def test_empty_value_is_missing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=\n")
        with pytest.raises(MissingKeyError):
            load_openai_key(env={}, dotenv_path=env_file)


class TestGeminiKey:
    def test_env_wins(self, tmp_path):
        assert load_gemini_key(env={"GEMINI_API_KEY": SECRET}, key_file=tmp_path / "x") == SECRET

    def test_reads_file_and_strips(self, tmp_path):
        f = _key_file(tmp_path, SECRET + "\n")
        assert load_gemini_key(env={}, key_file=f) == SECRET

    def test_env_can_point_at_file(self, tmp_path):
        f = _key_file(tmp_path, SECRET)
        assert load_gemini_key(env={"GEMINI_API_KEY_FILE": str(f)}) == SECRET

    def test_group_readable_file_refused(self, tmp_path):
        f = _key_file(tmp_path, SECRET, mode=0o644)
        with pytest.raises(InsecureKeyFileError) as exc:
            load_gemini_key(env={}, key_file=f)
        assert SECRET not in str(exc.value)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(MissingKeyError):
            load_gemini_key(env={}, key_file=tmp_path / "nope.txt")

    def test_empty_file_raises(self, tmp_path):
        f = _key_file(tmp_path, "  \n")
        with pytest.raises(MissingKeyError):
            load_gemini_key(env={}, key_file=f)
