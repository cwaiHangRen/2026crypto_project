import io

from PIL import Image, ImageDraw

from gm_provenance.screenshot import ScreenshotBudget, analyze_screenshot, locate_page_regions


def _shot():
    image = Image.new("RGB", (320, 240), "#888888")
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 25, 285, 215), fill="white")
    draw.rectangle((80, 80, 240, 120), fill="black")
    out = io.BytesIO(); image.save(out, format="PNG")
    return out.getvalue()


def test_locate_regions_and_preserve_original_sm3():
    raw = _shot(); result = analyze_screenshot(raw, budget=ScreenshotBudget(max_candidates=1))
    assert result.original_bytes == raw
    assert len(result.original_sm3) == 64
    assert result.regions[0].bbox[0] <= 35
    assert result.evidence[0].candidate.operation == "crop"


def test_region_verifier_is_called_with_derived_image_and_budget():
    calls = []

    def verify(payload):
        calls.append(payload)
        return {"conclusion": "ORIGIN_HINT_ONLY", "bytes": len(payload)}

    result = analyze_screenshot(_shot(), verify_fn=verify,
                                budget=ScreenshotBudget(max_candidates=4, max_verify_calls=2))
    assert len(calls) == 2
    assert len(result.evidence) == 2
    assert all(e.credential["conclusion"] == "ORIGIN_HINT_ONLY" for e in result.evidence)
    assert result.original_bytes != calls[0]


def test_invalid_pdf_is_rejected_for_real_screenshot():
    try:
        analyze_screenshot(b"%PDF-1.7")
    except ValueError as exc:
        assert "PNG 或 JPEG" in str(exc)
    else:
        raise AssertionError("expected screenshot format rejection")
