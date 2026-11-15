"""Durable structured comparisons and cited CSV reports."""

import csv
import hashlib
import io
import logging
from dataclasses import asdict
from uuid import uuid4

import sqlalchemy as sa

from documind import schema as t
from documind.comparison import (
    MODE,
    RULE,
    compare_records,
    decimal_string,
    extract_record,
    has_comparison_table_label,
    supports_field,
)
from documind.errors import AppError
from documind.parser import fingerprint
from documind.phase_jobs import lock_workspace, queue, record_request, request, terminal
from documind.repository import now

logger = logging.getLogger("documind")


def scope(table, workspace, identifier):
    return table.c.workspace_id == workspace, table.c.investigation_id == identifier


def investigation(c, workspace, identifier):
    row = (
        c.execute(
            sa.select(t.investigations).where(
                t.investigations.c.workspace_id == workspace, t.investigations.c.id == identifier
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppError("NOT_FOUND", 404)
    if row["revoked_at"] is not None:
        raise AppError("SOURCE_UNAVAILABLE", 410)
    return row


def create(repo, workspace, name, documents, key):
    repo.authorize(workspace)
    digest = fingerprint({"name": name, "document_ids": sorted(map(str, documents))})
    with repo.engine.begin() as c:
        replay = request(c, workspace, "investigation_create", key, digest)
        if replay:
            row = investigation(c, workspace, replay["resource_id"])
            return {"investigation_id": row["id"]}
        if len(set(documents)) != len(documents):
            raise AppError("INVALID_DOCUMENT_SCOPE", 422)
        identifier = uuid4()
        sources = []
        for document_id in documents:
            doc = repo._document(c, workspace, document_id)
            if not doc["preview_ingestion_version"]:
                raise AppError("DOCUMENT_NOT_READY", 409)
            sources.append(
                {
                    "workspace_id": workspace,
                    "investigation_id": identifier,
                    "document_id": document_id,
                    "ingestion_version": doc["preview_ingestion_version"],
                }
            )
        c.execute(
            t.investigations.insert().values(
                workspace_id=workspace,
                id=identifier,
                name=name,
                rule_version=RULE,
            )
        )
        c.execute(t.investigation_documents.insert(), sources)
        record_request(c, workspace, "investigation_create", key, digest, identifier)
        return {"investigation_id": identifier}


def start(repo, workspace, identifier, key):
    repo.authorize(workspace)
    digest = fingerprint({"investigation_id": str(identifier)})
    with repo.engine.begin() as c:
        replay = request(c, workspace, "investigation_run", key, digest)
        row = investigation(c, workspace, identifier)
        if replay:
            return replay["job_id"]
        job_id = row["job_id"]
        if job_id is None:
            job_id = queue(c, workspace, "comparison")
            c.execute(
                t.investigations.update()
                .where(
                    t.investigations.c.workspace_id == workspace,
                    t.investigations.c.id == identifier,
                )
                .values(job_id=job_id)
            )
        record_request(c, workspace, "investigation_run", key, digest, identifier, job_id)
        return job_id


def selected_sources(c, workspace, identifier):
    table = t.investigation_documents
    return (
        c.execute(
            sa.select(table, t.versions.c.extraction_status)
            .join(
                t.versions,
                sa.and_(
                    t.versions.c.workspace_id == table.c.workspace_id,
                    t.versions.c.document_id == table.c.document_id,
                    t.versions.c.ingestion_version == table.c.ingestion_version,
                ),
            )
            .where(*scope(table, workspace, identifier))
            .order_by(table.c.document_id)
        )
        .mappings()
        .all()
    )


def records_for(c, workspace, identifier):
    return (
        c.execute(
            sa.select(t.extracted_records)
            .where(*scope(t.extracted_records, workspace, identifier))
            .order_by(t.extracted_records.c.document_id)
        )
        .mappings()
        .all()
    )


def process_comparison(repo, job):
    workspace = job["workspace_id"]
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        repo._fence(c, job)
        identifier = c.scalar(
            sa.select(t.investigations.c.id).where(
                t.investigations.c.workspace_id == workspace,
                t.investigations.c.job_id == job["id"],
            )
        )
        row = investigation(c, workspace, identifier)
        if row["rule_version"] != RULE:
            raise AppError("COMPARISON_RULE_UNAVAILABLE", 409)
        for source in selected_sources(c, workspace, identifier):
            document, version = source["document_id"], source["ingestion_version"]
            elements = c.execute(
                sa.select(t.elements).where(
                    t.elements.c.workspace_id == workspace,
                    t.elements.c.document_id == document,
                    t.elements.c.ingestion_version == version,
                )
            ).mappings()
            record = extract_record(source["extraction_status"], elements)
            values = asdict(record)
            values.pop("evidence")
            fields = {
                "workspace_id": workspace,
                "investigation_id": identifier,
                "document_id": document,
                "ingestion_version": version,
            }
            c.execute(t.extracted_records.insert().values(**fields, **values))
            if record.evidence:
                c.execute(
                    t.record_evidence.insert(),
                    [
                        {**fields, "field": field, **asdict(anchor)}
                        for field, anchor in record.evidence.items()
                    ],
                )
        records = records_for(c, workspace, identifier)
        decision = compare_records(records)
        c.execute(
            t.findings.insert().values(
                workspace_id=workspace,
                investigation_id=identifier,
                type=decision.type,
                reason=decision.reason,
                difference=decision.difference,
            )
        )
        if decision.difference is not None:
            c.execute(
                t.finding_evidence.insert(),
                [
                    {
                        "workspace_id": workspace,
                        "investigation_id": identifier,
                        "role": role,
                        "document_id": document,
                    }
                    for role, document in (
                        ("invoice", decision.invoice),
                        ("purchase_order", decision.purchase_order),
                    )
                ],
            )
        terminal(c, job, "succeeded")
    logger.info(
        '{"event":"structured_comparison","investigation_id":"%s","rule":"%s"}', identifier, RULE
    )


def anchors_for(c, workspace, identifier):
    table = t.record_evidence
    rows = c.execute(
        sa.select(table, t.pages.c.page_number, t.elements.c.text_raw, t.elements.c.bbox_norm)
        .join(t.elements, t.elements.c.id == table.c.element_id)
        .join(t.pages, t.pages.c.id == table.c.page_id)
        .where(*scope(table, workspace, identifier))
        .order_by(table.c.document_id, table.c.field)
    ).mappings()
    anchors = {}
    for row in rows:
        document, field = row["document_id"], row["field"]
        if row["text_raw"][row["start_char"] : row["end_char"]] != row["quote"]:
            raise AppError("INVALID_SOURCE_SPAN", 503)
        base = f"/api/v1/workspaces/{workspace}"
        anchors[document, field] = {
            key: row[key]
            for key in (
                "document_id",
                "ingestion_version",
                "page_number",
                "element_id",
                "start_char",
                "end_char",
                "quote",
                "bbox_norm",
                "field",
            )
        } | {
            "source_url": f"{base}/investigations/{identifier}/evidence/{document}/{field}",
            "page_url": (
                f"{base}/documents/{document}/pages/{row['page_number']}"
                f"?ingestion_version={row['ingestion_version']}"
            ),
        }
    return anchors


def finding_for(c, workspace, identifier, records, anchors):
    row = (
        c.execute(sa.select(t.findings).where(*scope(t.findings, workspace, identifier)))
        .mappings()
        .first()
    )
    if row is None:
        return None
    decision = compare_records(records)
    if (row["type"], row["reason"], row["difference"]) != (
        decision.type,
        decision.reason,
        decision.difference,
    ):
        raise AppError("COMPARISON_INTEGRITY_ERROR", 503)
    finding = {
        "type": decision.type,
        "reason": decision.reason,
        "status": decision.status,
        "mode": MODE,
        "rule_id": RULE,
        "retrieval": "structured_sql",
        "evidence": [],
    }
    if decision.difference is None:
        return finding
    pinned = t.investigation_documents
    cells = c.scalars(
        sa.select(t.elements.c.text_raw)
        .join(
            pinned,
            sa.and_(
                t.elements.c.workspace_id == pinned.c.workspace_id,
                t.elements.c.document_id == pinned.c.document_id,
                t.elements.c.ingestion_version == pinned.c.ingestion_version,
            ),
        )
        .where(*scope(pinned, workspace, identifier), t.elements.c.element_type == "table_cell")
    )
    # Findings made before the table guard need a new investigation.
    if any(has_comparison_table_label(raw) for raw in cells):
        raise AppError("INVESTIGATION_RECHECK_REQUIRED", 409)
    rows = {r["document_id"]: r for r in records}
    pair = dict(
        c.execute(
            sa.select(t.finding_evidence.c.role, t.finding_evidence.c.document_id).where(
                *scope(t.finding_evidence, workspace, identifier)
            )
        ).all()
    )
    if pair != {"invoice": decision.invoice, "purchase_order": decision.purchase_order}:
        raise AppError("COMPARISON_INTEGRITY_ERROR", 503)
    required_fields = ("grand_total", "po_number", "vendor_id", "currency", "total_basis")
    invoice, order = rows[decision.invoice], rows[decision.purchase_order]
    if any(
        (record["document_id"], field) not in anchors
        or not supports_field(field, anchors[record["document_id"], field]["quote"], record)
        for record in (invoice, order)
        for field in required_fields
    ):
        raise AppError("COMPARISON_INTEGRITY_ERROR", 503)
    return finding | {
        "currency": invoice["currency"],
        "total_basis": invoice["total_basis"],
        "match_basis": {
            "method": "exact_po_and_vendor",
            "field": "po_number",
            "value": invoice["po_number"],
            "vendor_id": invoice["vendor_id"],
        },
        "invoice_amount": decimal_string(invoice["grand_total"]),
        "purchase_order_amount": decimal_string(order["grand_total"]),
        "difference": decimal_string(decision.difference),
        "operation": "invoice_total - purchase_order_total",
        "tolerance": "0.00",
        "evidence": [
            {"role": role, **anchors[document, "grand_total"]}
            for role, document in (
                ("invoice", decision.invoice),
                ("purchase_order", decision.purchase_order),
            )
        ],
    }


def detail(repo, workspace, identifier):
    repo.authorize(workspace)
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        item = investigation(c, workspace, identifier)
        job = (
            c.execute(
                sa.select(t.jobs.c.status, t.jobs.c.error_code).where(
                    t.jobs.c.workspace_id == workspace,
                    t.jobs.c.id == item["job_id"],
                )
            )
            .mappings()
            .first()
        )
        sources = selected_sources(c, workspace, identifier)
        records = records_for(c, workspace, identifier)
        anchors = anchors_for(c, workspace, identifier)
        finding = finding_for(c, workspace, identifier, records, anchors)
        return {
            "investigation_id": identifier,
            "name": item["name"],
            "job_id": item["job_id"],
            "rule_id": item["rule_version"],
            "mode": MODE,
            "status": job["status"] if job else "draft",
            "error_code": job["error_code"] if job else None,
            "sources": [
                {k: s[k] for k in ("document_id", "ingestion_version", "extraction_status")}
                for s in sources
            ],
            "records": [
                {
                    key: row[key]
                    for key in (
                        "document_id",
                        "ingestion_version",
                        "kind",
                        "invoice_number",
                        "po_number",
                        "vendor_id",
                        "currency",
                        "total_basis",
                        "issue",
                    )
                }
                | {
                    "grand_total": decimal_string(row["grand_total"])
                    if row["grand_total"] is not None
                    else None,
                    "evidence": [
                        a for (document, _), a in anchors.items() if document == row["document_id"]
                    ],
                }
                for row in records
            ],
            "findings": [finding] if finding else [],
        }


def evidence(repo, workspace, identifier, document, field):
    repo.authorize(workspace)
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        investigation(c, workspace, identifier)
        anchors = anchors_for(c, workspace, identifier)
        anchor = anchors.get((document, field))
        if anchor is None:
            raise AppError("NOT_FOUND", 404)
        finding_for(c, workspace, identifier, records_for(c, workspace, identifier), anchors)
        return anchor


def start_report(repo, workspace, identifier, key):
    repo.authorize(workspace)
    digest = fingerprint({"investigation_id": str(identifier), "format": "csv"})
    with repo.engine.begin() as c:
        replay = request(c, workspace, "investigation_report", key, digest)
        investigation(c, workspace, identifier)
        report_finding(c, workspace, identifier)
        if replay:
            return replay["job_id"]
        report_id = c.scalar(
            sa.select(t.report_artifacts.c.id).where(
                *scope(t.report_artifacts, workspace, identifier),
            )
        )
        if report_id is None:
            report_id = queue(c, workspace, "report")
            c.execute(
                t.report_artifacts.insert().values(
                    workspace_id=workspace,
                    id=report_id,
                    investigation_id=identifier,
                )
            )
        record_request(c, workspace, "investigation_report", key, digest, report_id, report_id)
        return report_id


def report_row(c, workspace, report_id):
    row = (
        c.execute(
            sa.select(t.report_artifacts).where(
                t.report_artifacts.c.workspace_id == workspace,
                t.report_artifacts.c.id == report_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppError("NOT_FOUND", 404)
    investigation(c, workspace, row["investigation_id"])
    return row


def report_finding(c, workspace, identifier):
    finding = finding_for(
        c,
        workspace,
        identifier,
        records_for(c, workspace, identifier),
        anchors_for(c, workspace, identifier),
    )
    if finding is None:
        raise AppError("INVESTIGATION_NOT_COMPLETE", 409)
    return finding


def csv_bytes(c, workspace, identifier):
    finding = report_finding(c, workspace, identifier)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    fields = (
        "mode",
        "rule_id",
        "type",
        "status",
        "reason",
        "currency",
        "total_basis",
        "invoice_amount",
        "purchase_order_amount",
        "difference",
        "operation",
        "tolerance",
    )
    source_fields = (
        "role",
        "document_id",
        "ingestion_version",
        "page_number",
        "element_id",
        "start_char",
        "end_char",
        "quote",
        "source_url",
        "page_url",
    )
    writer.writerow([*fields, "match_method", "po_number", "vendor_id", *source_fields])
    match = finding.get("match_basis", {})
    for anchor in finding["evidence"] or [{}]:
        # Quotes begin with a recognized field label; identifiers begin with an ASCII alphanumeric.
        writer.writerow(
            [
                *(finding.get(field, "") for field in fields),
                match.get("method", ""),
                match.get("value", ""),
                match.get("vendor_id", ""),
                *(anchor.get(field, "") for field in source_fields),
            ]
        )
    return output.getvalue().encode("utf-8")


def process_report(repo, job):
    workspace = job["workspace_id"]
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        repo._fence(c, job)
        row = report_row(c, workspace, job["id"])
        content = csv_bytes(c, workspace, row["investigation_id"])
        c.execute(
            t.report_artifacts.update()
            .where(
                t.report_artifacts.c.workspace_id == workspace,
                t.report_artifacts.c.id == job["id"],
            )
            .values(content=content, sha256=hashlib.sha256(content).hexdigest())
        )
        terminal(c, job, "succeeded")


def report(repo, workspace, report_id, *, download=False):
    repo.authorize(workspace)
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        row = report_row(c, workspace, report_id)
        job = (
            c.execute(
                sa.select(t.jobs.c.status, t.jobs.c.error_code).where(
                    t.jobs.c.workspace_id == workspace,
                    t.jobs.c.id == report_id,
                )
            )
            .mappings()
            .one()
        )
        if job["status"] == "succeeded":
            content = csv_bytes(c, workspace, row["investigation_id"])
            if content != row["content"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise AppError("REPORT_INTEGRITY_ERROR", 503)
            if download:
                return content
        if download:
            raise AppError("REPORT_NOT_READY", 409)
        return {
            "report_id": report_id,
            "investigation_id": row["investigation_id"],
            "format": "csv",
            "status": job["status"],
            "error_code": job["error_code"],
            "sha256": row["sha256"],
            "download_url": f"/api/v1/workspaces/{workspace}/reports/{report_id}/content"
            if job["status"] == "succeeded"
            else None,
        }


def revoke_source(c, workspace, document):
    table = t.investigation_documents
    ids = c.scalars(
        sa.select(table.c.investigation_id).where(
            table.c.workspace_id == workspace,
            table.c.document_id == document,
        )
    ).all()
    if not ids:
        return
    jobs = (
        sa.select(t.investigations.c.job_id)
        .where(
            t.investigations.c.workspace_id == workspace,
            t.investigations.c.id.in_(ids),
        )
        .union_all(
            sa.select(t.report_artifacts.c.id).where(
                t.report_artifacts.c.workspace_id == workspace,
                t.report_artifacts.c.investigation_id.in_(ids),
            )
        )
    )
    c.execute(
        t.jobs.update()
        .where(
            t.jobs.c.workspace_id == workspace,
            t.jobs.c.id.in_(jobs),
            t.jobs.c.status.in_(("queued", "running")),
        )
        .values(
            status="cancelled",
            error_code="SOURCE_UNAVAILABLE",
            finished_at=now(),
            lease_token=None,
            lease_expires_at=None,
        )
    )
    c.execute(
        t.investigations.update()
        .where(
            t.investigations.c.workspace_id == workspace,
            t.investigations.c.id.in_(ids),
        )
        .values(name=None, revoked_at=now())
    )
    c.execute(
        t.report_artifacts.update()
        .where(
            t.report_artifacts.c.workspace_id == workspace,
            t.report_artifacts.c.investigation_id.in_(ids),
        )
        .values(content=None, sha256=None)
    )
    for child in (t.findings, table):
        c.execute(
            child.delete().where(
                child.c.workspace_id == workspace,
                child.c.investigation_id.in_(ids),
            )
        )
