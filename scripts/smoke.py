"""Exercise the running Compose API/dispatcher/worker using a synthetic PDF."""

import time
from uuid import uuid4

import httpx
import pymupdf
from documind.config import settings

cfg = settings()
with pymupdf.open() as pdf:
    pdf.new_page().insert_text((50, 70), "DocuMind smoke fixture. Total INR 1100.00")
    table = pdf.new_page(width=300, height=300)
    for x in (30, 150, 270):
        table.draw_line((x, 60), (x, 140))
    for y in (60, 100, 140):
        table.draw_line((30, y), (270, y))
    for x, y, text in (
        (40, 85, "Item"),
        (160, 85, "Amount"),
        (40, 125, "Pens"),
        (160, 125, "12.50"),
    ):
        table.insert_text((x, y), text)
    data = pdf.tobytes()
with httpx.Client(
    base_url="http://127.0.0.1:8000",
    timeout=10,
    headers={"Authorization": "Bearer " + cfg.local_api_token.get_secret_value()},
) as c:
    c.get("/api/v1/ready").raise_for_status()
    base = f"/api/v1/workspaces/{cfg.local_workspace_id}"
    response = c.post(
        base + "/documents",
        files={"file": ("smoke.pdf", data)},
        headers={"Idempotency-Key": str(uuid4())},
    )
    response.raise_for_status()
    result = response.json()
    deadline = time.monotonic() + 45
    while True:
        job = c.get(base + "/jobs/" + result["job_id"])
        job.raise_for_status()
        if job.json()["status"] in {"succeeded", "failed"}:
            break
        assert time.monotonic() < deadline, "Worker did not finish within 45 seconds"
        time.sleep(0.5)
    assert job.json()["status"] == "succeeded", job.json()["error_code"]
    path = base + "/documents/" + result["document_id"]
    document = c.get(path).json()
    assert document["preview_available"] and not document["searchable"]
    page = c.get(path + "/pages/1").json()
    assert "1100.00" in page["text_raw"]
    assert c.get(page["image_url"]).content.startswith(b"\x89PNG")
    elements = c.get(path + "/elements").json()["items"]
    assert elements and elements[0]["confidence"] is None
    assert c.get(path + "/original").content == data
    assert document["extraction_status"] == "needs_review"
    cells = c.get(path + "/elements", params={"element_type": "table_cell"}).json()["items"]
    assert len(cells) == 4
    cell = next(element for element in cells if element["text_raw"] == "12.50")
    base_version = document["preview_ingestion_version"]
    review = c.post(
        path + "/review",
        json={
            "base_ingestion_version": base_version,
            "confirm_all_pages": True,
            "corrections": [{"element_id": cell["id"], "text_raw": "13.50"}],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    review.raise_for_status()
    deadline = time.monotonic() + 45
    while True:
        result_job = c.get(base + "/jobs/" + review.json()["job_id"]).json()
        if result_job["status"] in {"succeeded", "failed"}:
            break
        assert time.monotonic() < deadline, "Review worker did not finish"
        time.sleep(0.5)
    assert result_job["status"] == "succeeded", result_job["error_code"]
    assert c.get(path).json()["extraction_status"] == "complete"
    assert "13.50" in c.get(path + "/pages/2").json()["text_raw"]
    assert (
        "12.50"
        in c.get(path + "/pages/2", params={"ingestion_version": base_version}).json()["text_raw"]
    )
    deleted = c.delete(path).json()
    assert c.get(path).status_code == 410
    deadline = time.monotonic() + 30
    while c.get(base + "/jobs/" + deleted["job_id"]).json()["status"] != "succeeded":
        assert time.monotonic() < deadline, "Cleanup did not finish within 30 seconds"
        time.sleep(0.5)
    print(
        "PASS: real HTTP upload -> Redis/Celery -> PostgreSQL -> PNG/text/table preview "
        "-> immutable review -> old-version read -> deletion."
    )
    print("Document:", result["document_id"], "(synthetic fixture deleted after verification)")
