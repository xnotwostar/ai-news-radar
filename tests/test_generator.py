"""Tests for generator layer."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.schemas import EventCard, LLMModelEntry, TrendingItem
from src.generator.llm_client import LLMClient
from src.generator.report_writer import ReportWriter
from src.pusher.dingtalk import DingTalkPusher

FIXTURES = Path(__file__).parent / "fixtures"


class TestLLMClient:
    def test_fallback_chain(self):
        chain = [
            LLMModelEntry(provider="anthropic", model="claude-sonnet-4-5-20250929", priority=1, timeout=10),
            LLMModelEntry(provider="dashscope", model="qwen-plus", priority=2, timeout=10),
        ]
        client = LLMClient(chain=chain)

        with patch.object(client, "_call") as mock_call:
            mock_call.side_effect = [RuntimeError("anthropic down"), "Report from Qwen"]
            result = client.generate("system", "user")
            assert result == "Report from Qwen"
            assert mock_call.call_count == 2

    def test_all_providers_fail(self):
        chain = [
            LLMModelEntry(provider="anthropic", model="test", priority=1, timeout=5),
        ]
        client = LLMClient(chain=chain)

        with patch.object(client, "_call", side_effect=RuntimeError("fail")):
            with pytest.raises(RuntimeError, match="All LLM providers failed"):
                client.generate("system", "user")


class TestReportWriter:
    def test_load_prompt_split(self, tmp_path):
        prompt_file = tmp_path / "test_prompt.txt"
        prompt_file.write_text("---SYSTEM---\nYou are an analyst.\n---ONESHOT---\n# Report\nExample content")

        system, oneshot = ReportWriter._load_prompt(str(prompt_file))
        assert "analyst" in system
        assert "# Report" in oneshot

    def test_load_prompt_no_markers(self, tmp_path):
        prompt_file = tmp_path / "test_prompt.txt"
        prompt_file.write_text("# Just a report example\nContent here")

        system, oneshot = ReportWriter._load_prompt(str(prompt_file))
        assert "情报分析师" in system  # default system prompt
        assert "# Just a report example" in oneshot


class TestDingTalkPusher:
    def test_split_chunks_short(self):
        text = "Short message"
        chunks = DingTalkPusher._split_chunks(text)
        assert len(chunks) == 1

    def test_split_chunks_long(self):
        section = "## Section\n" + "x" * 3000 + "\n"
        text = section * 4  # ~12000 chars
        chunks = DingTalkPusher._split_chunks(text, max_size=6000)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 6000

    def test_split_preserves_content(self):
        text = "## A\nContent A\n## B\nContent B\n## C\nContent C"
        chunks = DingTalkPusher._split_chunks(text, max_size=50)
        rejoined = "\n".join(chunks)
        assert "Content A" in rejoined
        assert "Content B" in rejoined
        assert "Content C" in rejoined
