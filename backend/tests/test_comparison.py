"""Phase 4 tests use real PDF anchors, PostgreSQL and the durable worker entry point."""

import csv
import io
from decimal import Decimal
from uuid import UUID, uuid4

import pymupdf
import pytest
import sqlalchemy as sa
from documind import investigations
from documind import schema as t
from documind.comparison import compare_records, extract_record
from sqlalchemy.exc import IntegrityError
from test_phase2 import process, upload

INVOICE = (
    "Document type: Invoice\nInvoice number: INV-001\nPO reference: PO-0100\n"
    "Vendor ID: VEN-005\nGrand total: INR 1,100.00\nGrand total basis: tax-inclusive"
)
ORDER = (
    "Document type: Purchase order\nPO number: PO-0100\nVendor ID: VEN-005\n"
    "Grand total: INR 1,000.00\nGrand total basis: tax-inclusive"
)


def extracted(raw, document_id, status="complete"):
    row = extract_record(
        status,
        [
            {
                "id": uuid4(),
                "page_id": uuid4(),
                "element_type": "paragraph",
                "text_raw": raw,
            }
        ],
    )
    return {**vars(row), "document_id": document_id}


def test_reviewed_table_cannot_hide_a_competing_grand_total():
    invoice = extract_record(
        "complete",
        [
            {"id": uuid4(), "page_id": uuid4(), "element_type": kind, "text_raw": raw}
            for kind, raw in (
                ("paragraph", INVOICE),
                ("table_cell", "Grand total"),
                ("table_cell", "INR 2,000.00"),
            )
        ],
    )
    decision = compare_records(
        [{**vars(invoice), "document_id": uuid4()}, extracted(ORDER, uuid4())]
    )
    assert decision.reason == "COMPARISON_TABLE_FIELDS_UNSUPPORTED"
    assert decision.status == "needs_review" and decision.difference is None


@pytest.mark.parametrize(
    ("invoice", "order", "status", "expected_type", "expected_reason", "difference"),
    [
        (INVOICE, ORDER, "complete", "AMOUNT_MISMATCH", None, "100.00"),
        (INVOICE.replace("1,100.00", "1,000.00"), ORDER, "complete", "AMOUNT_MATCH", None, "0"),
        (
            INVOICE.replace("1,100.00", "-1,100.000001"),
            ORDER.replace("1,000.00", "-1,000.00"),
            "complete",
            "AMOUNT_MISMATCH",
            None,
            "-100.000001",
        ),
        (
            INVOICE.replace("INR 1,100.00", "1,100.00") + "\nCurrency: INR",
            ORDER,
            "complete",
            "AMOUNT_MISMATCH",
            None,
            "100.00",
        ),
        (
            INVOICE.replace("1,100.00", "1,23,456.123456"),
            ORDER,
            "complete",
            "AMOUNT_MISMATCH",
            None,
            "122456.123456",
        ),
        (
            INVOICE.replace("1,100.00", "99999999999999.999999"),
            ORDER.replace("1,000.00", "-99999999999999.999999"),
            "complete",
            "AMOUNT_MISMATCH",
            None,
            "199999999999999.999998",
        ),
        (
            INVOICE.replace("PO-0100", "PO-100"),
            ORDER,
            "complete",
            "UNVERIFIED_MATCH",
            "IDENTIFIERS_DO_NOT_MATCH",
            None,
        ),
        (
            INVOICE.replace("VEN-005", "VEN-006"),
            ORDER,
            "complete",
            "UNVERIFIED_MATCH",
            "IDENTIFIERS_DO_NOT_MATCH",
            None,
        ),
        (
            INVOICE + "\nPO reference: PO-0100",
            ORDER,
            "complete",
            "UNVERIFIED_MATCH",
            "IDENTIFIERS_MISSING_OR_AMBIGUOUS",
            None,
        ),
        (INVOICE, ORDER, "needs_review", "UNVERIFIED_MATCH", "SOURCE_REVIEW_REQUIRED", None),
        (
            INVOICE + "\nAmendment: pending",
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "AMENDMENT_OR_CREDIT",
            None,
        ),
        (
            INVOICE.replace("INR ", ""),
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "TOTAL_OR_CURRENCY_UNRESOLVED",
            None,
        ),
        (
            INVOICE.replace("INR", "USD"),
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "CURRENCY_MISMATCH",
            None,
        ),
        (
            INVOICE.replace("tax-inclusive", "tax-exclusive"),
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "TOTAL_BASIS_UNRESOLVED",
            None,
        ),
        (
            INVOICE.replace("\nGrand total basis: tax-inclusive", ""),
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "TOTAL_BASIS_UNRESOLVED",
            None,
        ),
        (
            INVOICE.replace("1,100.00", "100000000000000.00"),
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "TOTAL_OR_CURRENCY_UNRESOLVED",
            None,
        ),
        (
            INVOICE.replace("1,100.00", "NaN"),
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "TOTAL_OR_CURRENCY_UNRESOLVED",
            None,
        ),
        (
            INVOICE + "\nGrand total: INR 1,100.0000001",
            ORDER,
            "complete",
            "UNVERIFIED_TOTAL",
            "TOTAL_OR_CURRENCY_UNRESOLVED",
            None,
        ),
    ],
)
def test_bounded_comparison(invoice, order, status, expected_type, expected_reason, difference):
    decision = compare_records([extracted(invoice, uuid4(), status), extracted(order, uuid4())])
    assert (decision.type, decision.reason) == (expected_type, expected_reason)
    assert decision.difference == (Decimal(difference) if difference is not None else None)


def post(client, path, key, body=None):
    return client.post(path, json=body, headers={"Idempotency-Key": key})


def create(client, base, documents, key="create"):
    response = post(
        client, base + "/investigations", key, {"document_ids": list(map(str, documents))}
    )
    assert response.status_code == 201, response.json()
    identifier = response.json()["investigation_id"]
    return base + "/investigations/" + identifier, UUID(identifier)


def start(client, path, key="run"):
    response = post(client, path + "/run", key)
    assert response.status_code == 202, response.json()
    return response.json()["job_id"]


@pytest.mark.integration
def test_comparison_and_report_isolate_two_populated_workspaces(client, repo, pdf, monkeypatch):
    workspace_a, workspace_b = repo.cfg.local_workspace_id, uuid4()
    monkeypatch.setattr(repo.cfg, "indexing_enabled", False)
    monkeypatch.setattr(repo.cfg, "generation_enabled", False)
    monkeypatch.setattr(repo.cfg, "self_rag_enabled", False)

    def compare(workspace, amount):
        with monkeypatch.context() as context:
            context.setattr(repo.cfg, "local_workspace_id", workspace)
            repo.initialize()
            base = f"/api/v1/workspaces/{workspace}"
            invoice = upload(repo, pdf, INVOICE.replace("1,100.00", amount), "invoice")
            order = upload(repo, pdf, ORDER, "order")
            detail, identifier = create(client, base, [invoice, order])
            process(start(client, detail))
            finding = client.get(detail).json()["findings"][0]
            receipt = post(client, detail + "/reports", "report", {"format": "csv"})
            assert receipt.status_code == 202
            process(receipt.json()["job_id"])
            report = base + "/reports/" + receipt.json()["report_id"]
            url = client.get(report).json()["download_url"]
            csv_response = client.get(url)
            assert csv_response.status_code == 200
            rows = list(csv.DictReader(io.StringIO(csv_response.text)))
            assert {row["document_id"] for row in rows} == {str(invoice), str(order)}
            return base, detail, identifier, finding, report, url, invoice, rows

    a = compare(workspace_a, "1,100.00")
    b = compare(workspace_b, "7,123.45")
    assert [a[3]["difference"], b[3]["difference"]] == ["100.00", "6123.45"]
    for local, foreign in ((a, b), (b, a)):
        base, detail, identifier, finding, _, _, invoice, rows = local
        foreign_base, foreign_detail, _, foreign_finding, report, url, foreign_invoice, _ = foreign
        with monkeypatch.context() as context:
            context.setattr(repo.cfg, "local_workspace_id", UUID(base.split("/")[-1]))
            items = client.get(base + "/investigations").json()["items"]
            assert [item["investigation_id"] for item in items] == [str(identifier)]
            assert all(row["difference"] == finding["difference"] for row in rows)
            for path in (
                foreign_detail,
                foreign_detail + "/findings",
                report,
                url,
                *(anchor["source_url"] for anchor in foreign_finding["evidence"]),
            ):
                for scope in (path, path.replace(foreign_base, base, 1)):
                    response = client.get(scope)
                    assert response.status_code == 404
                    assert foreign_finding["difference"] not in response.text
            denied = post(
                client,
                base + "/investigations",
                "mixed",
                {"document_ids": [str(invoice), str(foreign_invoice)]},
            )
            assert denied.status_code == 404
            assert post(client, detail + "/reports", "report", {"format": "csv"}).status_code == 202


@pytest.mark.integration
def test_comparison_and_csv_preserve_exact_cited_sources(client, repo, pdf, monkeypatch):
    workspace = repo.cfg.local_workspace_id
    monkeypatch.setattr(repo.cfg, "indexing_enabled", False)
    monkeypatch.setattr(repo.cfg, "generation_enabled", False)
    base = f"/api/v1/workspaces/{workspace}"
    invoice = upload(repo, pdf, INVOICE, "invoice")
    order = upload(repo, pdf, ORDER, "purchase-order")
    route, identifier = create(client, base, [invoice, order])
    assert create(client, base, [order, invoice])[1] == identifier
    assert (
        post(
            client,
            base + "/investigations",
            "create",
            {
                "document_ids": [str(order), str(order)],
            },
        ).status_code
        == 409
    )
    assert client.get(route).json()["status"] == "draft"
    assert post(client, route + "/reports", "early", {"format": "csv"}).status_code == 409
    with repo.engine.connect() as c:
        original = c.execute(
            sa.select(t.elements.c.id, t.elements.c.ingestion_version, t.elements.c.text_raw).where(
                t.elements.c.document_id == invoice, t.elements.c.text_raw.contains("1,100.00")
            )
        ).one()
    reviewed = repo.reprocess(
        workspace,
        invoice,
        "corrected-after-selection",
        review={
            "base_ingestion_version": original.ingestion_version,
            "confirm_all_pages": True,
            "corrections": [
                {
                    "element_id": str(original.id),
                    "text_raw": original.text_raw.replace("1,100.00", "1,000.00"),
                    "is_header": None,
                }
            ],
        },
    )
    process(reviewed["job_id"])
    assert repo.job(workspace, UUID(str(reviewed["job_id"])))["status"] == "succeeded"
    new_page = client.get(base + f"/documents/{invoice}/pages/1").json()
    assert new_page["ingestion_version"] != original.ingestion_version
    assert "Grand total: INR 1,000.00" in new_page["text_raw"]
    job = start(client, route)
    assert start(client, route, "another-run-key") == job
    process(job)
    process(job)
    result = client.get(route).json()
    assert result["status"] == "succeeded", result
    assert result["mode"] == "deterministic_comparison"
    (finding,) = client.get(route + "/findings").json()["items"]
    assert finding["type"] == "AMOUNT_MISMATCH" and finding["status"] == "verified"
    assert finding["difference"] == "100.00" and finding["retrieval"] == "structured_sql"
    assert finding["difference"] == str(
        Decimal(finding["invoice_amount"]) - Decimal(finding["purchase_order_amount"])
    )
    assert finding["match_basis"]["value"] == "PO-0100"
    assert {item["document_id"] for item in finding["evidence"]} == {str(invoice), str(order)}
    for anchor in finding["evidence"]:
        source = client.get(anchor["source_url"]).json()
        assert source["quote"].startswith("Grand total: INR")
        assert source["page_number"] == 1 and source["bbox_norm"]
        if source["document_id"] == str(invoice):
            assert source["quote"] == "Grand total: INR 1,100.00"
            assert source["ingestion_version"] == original.ingestion_version
        page = client.get(anchor["page_url"]).json()
        assert page["ingestion_version"] == anchor["ingestion_version"]
        assert source["quote"] in page["text_raw"]
    assert client.get(route.replace(str(workspace), str(uuid4()))).status_code == 404

    response = post(client, route + "/reports", "report", {"format": "csv"})
    assert response.status_code == 202, response.json()
    receipt = response.json()
    assert post(client, route + "/reports", "report", {"format": "csv"}).json() == receipt
    process(receipt["job_id"])
    report = client.get(base + "/reports/" + receipt["report_id"]).json()
    downloaded = client.get(report["download_url"])
    assert (
        downloaded.status_code == 200 and "attachment" in downloaded.headers["content-disposition"]
    )
    rows = list(csv.DictReader(io.StringIO(downloaded.text)))
    assert len(rows) == 2
    assert {row["difference"] for row in rows} == {"100.00"}
    assert {row["match_method"] for row in rows} == {"exact_po_and_vendor"}
    assert {row["source_url"] for row in rows} == {a["source_url"] for a in finding["evidence"]}

    totals = t.extracted_records.update().where(
        t.extracted_records.c.workspace_id == workspace,
        t.extracted_records.c.investigation_id == identifier,
    )
    with repo.engine.begin() as c:
        c.execute(totals.values(grand_total=t.extracted_records.c.grand_total + 1))
    assert client.get(route).json()["error"]["code"] == "COMPARISON_INTEGRITY_ERROR"
    assert client.get(report["download_url"]).status_code == 503
    with repo.engine.begin() as c:
        c.execute(totals.values(grand_total=t.extracted_records.c.grand_total - 1))
    artifact = t.report_artifacts.update().where(
        t.report_artifacts.c.workspace_id == workspace,
        t.report_artifacts.c.id == UUID(receipt["report_id"]),
    )
    with repo.engine.begin() as c:
        c.execute(artifact.values(content=b"altered"))
    for path in (base + "/reports/" + receipt["report_id"], report["download_url"]):
        assert client.get(path).json()["error"]["code"] == "REPORT_INTEGRITY_ERROR"

    with pytest.raises(IntegrityError), repo.engine.begin() as c:
        c.execute(
            t.record_evidence.update()
            .where(
                t.record_evidence.c.workspace_id == workspace,
                t.record_evidence.c.investigation_id == identifier,
                t.record_evidence.c.field == "grand_total",
            )
            .values(quote="Wrong raw span")
        )
    deletion = client.delete(base + "/documents/" + str(invoice)).json()
    assert client.get(route).status_code == 410
    assert client.get(finding["evidence"][0]["source_url"]).status_code == 410
    assert client.get(report["download_url"]).status_code == 410
    process(deletion["job_id"])
    assert repo.job(workspace, UUID(deletion["job_id"]))["status"] == "succeeded"
    with repo.engine.connect() as c:
        assert not c.scalar(sa.select(sa.func.count()).select_from(t.extracted_records))
        assert c.scalar(sa.select(t.report_artifacts.c.content)) is None


@pytest.mark.integration
def test_ambiguous_po_and_unreviewed_sources_cannot_verify(client, repo, pdf, table_pdf):
    workspace = repo.cfg.local_workspace_id
    base = f"/api/v1/workspaces/{workspace}"
    invoice = upload(repo, pdf, INVOICE, "invoice")
    order = upload(repo, pdf, ORDER, "order")
    another = upload(repo, pdf, ORDER, "another")
    route, _ = create(client, base, [invoice, order, another])
    process(start(client, route))
    (finding,) = client.get(route + "/findings").json()["items"]
    assert finding["type"] == "UNVERIFIED_MATCH" and finding["status"] == "needs_review"
    assert not finding["evidence"] and "difference" not in finding

    pending = repo.upload(workspace, table_pdf(), "unreviewed.pdf", "unreviewed")
    process(pending["job_id"])
    route, _ = create(client, base, [pending["document_id"], order], "review")
    process(start(client, route, "review-run"))
    (finding,) = client.get(route + "/findings").json()["items"]
    assert finding["reason"] == "SOURCE_REVIEW_REQUIRED" and "difference" not in finding


@pytest.mark.integration
def test_reviewed_competing_table_total_blocks_new_and_existing_exports(
    client, repo, pdf, monkeypatch
):
    workspace = repo.cfg.local_workspace_id
    base = f"/api/v1/workspaces/{workspace}"
    with pymupdf.open(stream=pdf(INVOICE), filetype="pdf") as document:
        page = document.new_page(width=320, height=340)
        for x in (30, 165, 290):
            page.draw_line((x, 50), (x, 135))
        for y in (50, 90, 135):
            page.draw_line((30, y), (290, y))
        for x, y, label in (
            (40, 75, "Label"),
            (175, 75, "Amount"),
            (40, 115, "Grand total"),
            (175, 115, "INR 2,000.00"),
        ):
            page.insert_text((x, y), label)
        receipt = repo.upload(workspace, document.tobytes(), "mixed.pdf", "mixed")
    invoice = receipt["document_id"]
    process(receipt["job_id"])
    reviewed = repo.reprocess(
        workspace,
        invoice,
        "reviewed-table",
        review={
            "base_ingestion_version": receipt["latest_ingestion_version"],
            "confirm_all_pages": True,
            "corrections": [],
        },
    )
    process(reviewed["job_id"])
    order = upload(repo, pdf, ORDER, "order")
    previous, _ = create(client, base, [invoice, order], "previous")
    with monkeypatch.context() as before:
        before.setattr(
            investigations,
            "extract_record",
            lambda status, elements: extract_record(
                status, (row for row in elements if row["element_type"] == "paragraph")
            ),
        )
        before.setattr(investigations, "has_comparison_table_label", lambda raw: False)
        process(start(client, previous, "previous-run"))
        finding = client.get(previous).json()["findings"][0]
        assert finding["difference"] == "100.00"
        evidence_link = finding["evidence"][0]["source_url"]
        assert client.get(evidence_link).status_code == 200
        exported = post(client, previous + "/reports", "old-report", {"format": "csv"}).json()
        process(exported["job_id"])
        metadata = base + "/reports/" + exported["report_id"]
        link = metadata + "/content"
        assert client.get(link).status_code == 200
    for path in (previous, metadata, link, evidence_link):
        response = client.get(path)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVESTIGATION_RECHECK_REQUIRED"
    for key in ("old-report", "new-report"):
        response = post(client, previous + "/reports", key, {"format": "csv"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVESTIGATION_RECHECK_REQUIRED"
    current, _ = create(client, base, [invoice, order], "current")
    process(start(client, current, "current-run"))
    finding = client.get(current).json()["findings"][0]
    assert finding["reason"] == "COMPARISON_TABLE_FIELDS_UNSUPPORTED"
    assert finding["status"] == "needs_review" and "difference" not in finding
