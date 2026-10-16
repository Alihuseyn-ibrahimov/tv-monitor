#!/usr/bin/env python3
"""
tv-monitor üçün şəbəkəsiz testlər (requests.post mock edilir).

2026-10-16: Groq `llama-3.3-70b-versatile` modelini götürdü, classify hər
sorğuda 404 aldı və tapılan HƏR süjet səssizcə itdi. Bu testlər:
  - klassifikasiya modelinin config-dən (.env ilə dəyişdirilə bilən) gəldiyini,
  - healthcheck-in ardıcıl klassifikasiya xətalarını tutduğunu yoxlayır.

İstifadə (serverdə və ya lokal):
    venv/bin/python -m unittest test_tv_monitor -v
"""
import importlib
import json
import os
import unittest
from unittest import mock

import classify
import config
import healthcheck


def _fake_response(content: dict, status: int = 200):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = {"choices": [{"message": {"content": json.dumps(content)}}]}
    resp.raise_for_status.return_value = None
    return resp


SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "Pensiyalar artırıldı."},
    {"start": 4.0, "end": 9.0, "text": "Nazirlik açıqlama verdi."},
]


class ClassifyModelConfig(unittest.TestCase):
    def test_default_model_is_available_groq_model(self):
        # Köhnəlmiş model bir daha default olmamalıdır
        self.assertNotEqual(config.CLASSIFY_MODEL, "llama-3.3-70b-versatile")
        self.assertEqual(config.CLASSIFY_MODEL, "openai/gpt-oss-120b")

    def test_env_overrides_model(self):
        with mock.patch.dict(os.environ, {"GROQ_CLASSIFY_MODEL": "qwen/qwen3.8-27b"}):
            reloaded = importlib.reload(config)
            try:
                self.assertEqual(reloaded.CLASSIFY_MODEL, "qwen/qwen3.8-27b")
            finally:
                os.environ.pop("GROQ_CLASSIFY_MODEL", None)
                importlib.reload(config)

    def test_request_uses_config_model(self):
        answer = {"relevant": True, "title": "Pensiya artırıldı? | AzTV",
                  "sentiment": "MÜSBƏT", "start": 0.0, "end": 9.0}
        with mock.patch.object(classify.requests, "post", return_value=_fake_response(answer)) as post:
            result = classify.classify_and_title(SEGMENTS, "AzTV", "pensiya")
        self.assertEqual(post.call_args.kwargs["json"]["model"], config.CLASSIFY_MODEL)
        self.assertTrue(result["relevant"])
        self.assertEqual(result["sentiment"], "MÜSBƏT")
        self.assertEqual((result["start"], result["end"]), (0.0, 9.0))


class HealthcheckClassifyFailures(unittest.TestCase):
    LOG = (
        "Oct 16 17:16:15 host python[1]: [BakuTV] AI relevance check failed for 20261016_171329.mp4: "
        "404 Client Error: Not Found for url: https://api.groq.com/openai/v1/chat/completions\n"
        "Oct 16 17:17:00 host python[1]: [ARB] ffmpeg started (pid=2)\n"
        "Oct 16 17:18:15 host python[1]: [ITV] AI relevance check failed for 20261016_171500.mp4: 404\n"
        "Oct 16 17:19:15 host python[1]: [ATV] AI relevance check failed for 20261016_171600.mp4: 404\n"
    )

    def test_counts_only_classify_failures(self):
        self.assertEqual(healthcheck.count_classify_failures(self.LOG), 3)

    def test_clean_log_is_zero(self):
        self.assertEqual(healthcheck.count_classify_failures("[ARB] ffmpeg started (pid=2)\n"), 0)

    def test_problem_reported_at_threshold(self):
        with mock.patch.object(healthcheck, "_journal_since", return_value=self.LOG):
            self.assertEqual(healthcheck.check_classify_failures(), 3)
        with mock.patch.object(healthcheck, "_journal_since", return_value=""):
            self.assertEqual(healthcheck.check_classify_failures(), 0)
        self.assertLessEqual(healthcheck.CLASSIFY_FAILURE_THRESHOLD, 3)


if __name__ == "__main__":
    unittest.main()
