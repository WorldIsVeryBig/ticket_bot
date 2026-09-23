"""captcha solver 測試（mock ddddocr）"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ticket_bot.config import CaptchaConfig


@pytest.fixture()
def mock_ddddocr():
    """Mock ddddocr.DdddOcr"""
    with patch("ticket_bot.captcha.solver.ddddocr") as mock_mod:
        mock_ocr = MagicMock()
        mock_mod.DdddOcr.return_value = mock_ocr
        yield mock_ocr


def test_solve_high_confidence(mock_ddddocr):
    """辨識結果信心足夠"""
    from ticket_bot.captcha.solver import CaptchaSolver

    mock_ddddocr.classification.return_value = {"text": "abcd", "confidence": 0.95}

    cfg = CaptchaConfig(preprocess=False)
    solver = CaptchaSolver(cfg)
    text, conf = solver.solve(b"fake-image-bytes")

    assert text == "abcd"
    assert conf == 0.95


def test_solve_with_retry_first_try(mock_ddddocr):
    """第一次就通過信心門檻（4 字母驗證碼）"""
    from ticket_bot.captcha.solver import CaptchaSolver

    mock_ddddocr.classification.return_value = {"text": "abcd", "confidence": 0.95}

    cfg = CaptchaConfig(confidence_threshold=0.6, max_attempts=3, preprocess=False)
    solver = CaptchaSolver(cfg)
    solver.bandit.select = MagicMock(return_value=0.6)

    result = solver.solve_with_retry(lambda: b"image")
    assert result == "abcd"
    assert mock_ddddocr.classification.call_count == 1


def test_solve_with_retry_needs_multiple(mock_ddddocr):
    """前幾次信心不足，最後一次通過"""
    from ticket_bot.captcha.solver import CaptchaSolver

    mock_ddddocr.classification.side_effect = [
        {"text": "abcd", "confidence": 0.3},
        {"text": "efgh", "confidence": 0.95},
    ]

    cfg = CaptchaConfig(confidence_threshold=0.6, max_attempts=5, preprocess=False)
    solver = CaptchaSolver(cfg)
    solver.bandit.select = MagicMock(return_value=0.6)

    call_count = 0

    def fetch():
        nonlocal call_count
        call_count += 1
        return b"image"

    result = solver.solve_with_retry(fetch)
    assert result == "efgh"
    assert call_count == 2


def test_solve_with_retry_exhausted(mock_ddddocr):
    """全部嘗試信心都不足 → 回傳最後結果"""
    from ticket_bot.captcha.solver import CaptchaSolver

    mock_ddddocr.classification.return_value = {"text": "wxyz", "confidence": 0.2}

    cfg = CaptchaConfig(confidence_threshold=0.9, max_attempts=2, preprocess=False)
    solver = CaptchaSolver(cfg)
    solver.bandit.select = MagicMock(return_value=0.9)

    result = solver.solve_with_retry(lambda: b"image")
    assert result == "wxyz"
    assert mock_ddddocr.classification.call_count == 2


@pytest.mark.asyncio
async def test_asolve_with_retry(mock_ddddocr):
    """async 版本正常運作（4 字母驗證碼）"""
    from ticket_bot.captcha.solver import CaptchaSolver

    mock_ddddocr.classification.return_value = {"text": "qrst", "confidence": 0.95}

    cfg = CaptchaConfig(confidence_threshold=0.6, max_attempts=3, preprocess=False)
    solver = CaptchaSolver(cfg)
    solver.bandit.select = MagicMock(return_value=0.6)

    async def fetch():
        return b"image"

    result = await solver.asolve_with_retry(fetch)
    assert result == "qrst"


def test_custom_charset_txt_support(tmp_path, mock_ddddocr):
    """自訓練模型可載入 txt charset，且自動保留 CTC blank=0"""
    from ticket_bot.captcha.solver import CaptchaSolver

    model_path = tmp_path / "captcha_model.onnx"
    model_path.write_bytes(b"fake-model")
    charset_path = tmp_path / "charset.txt"
    charset_path.write_text("a\nb\nc\n", encoding="utf-8")

    fake_session = MagicMock()
    fake_ort = SimpleNamespace(InferenceSession=MagicMock(return_value=fake_session))

    with patch.dict(sys.modules, {"onnxruntime": fake_ort}):
        solver = CaptchaSolver(
            CaptchaConfig(
                custom_model_path=str(model_path),
                custom_charset_path=str(charset_path),
            )
        )

    assert solver._ort_session is fake_session
    assert solver._idx_to_char == {0: "", 1: "a", 2: "b", 3: "c"}
