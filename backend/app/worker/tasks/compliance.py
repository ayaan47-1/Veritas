from __future__ import annotations

import asyncio
import uuid

from ...config import settings
from ...database import SessionLocal
from ...models import ComplianceReport, ComplianceResult, IfcModel
from ...models.enums import ReportStatus, ResultStatus
from ...services.mcp_client import run_compliance_check


def execute_mcp(report_id: str) -> dict:
    """Spawn VeritasMCP, run the compliance check, store raw results on the report."""
    db = SessionLocal()
    try:
        report = db.query(ComplianceReport).filter(
            ComplianceReport.id == report_id
        ).first()
        if not report:
            return {"report_id": report_id, "status": "not_found"}

        ifc_model = db.query(IfcModel).filter(
            IfcModel.id == report.ifc_model_id
        ).first()
        if not ifc_model:
            report.status = ReportStatus.failed
            report.error_message = "IFC model record not found"
            db.commit()
            return {"report_id": report_id, "status": "error"}

        from ...models import Document
        spec_doc = db.query(Document).filter(
            Document.id == report.spec_document_id
        ).first()
        if not spec_doc:
            report.status = ReportStatus.failed
            report.error_message = "Spec document record not found"
            db.commit()
            return {"report_id": report_id, "status": "error"}

        report.status = ReportStatus.running
        db.commit()

        raw = asyncio.run(
            run_compliance_check(
                ifc_file_path=ifc_model.file_path,
                spec_file_path=spec_doc.file_path,
                mcp_server_path=settings.mcp_server_path,
            )
        )

        # Store the raw results temporarily in error_message (JSON) for the
        # persist step to consume, avoiding a second MCP call.
        import json
        report.error_message = json.dumps(raw)
        db.commit()

        return {"report_id": report_id, "status": "ok", "result_count": len(raw.get("results", []))}
    except Exception as exc:
        db.rollback()
        try:
            report = db.query(ComplianceReport).filter(
                ComplianceReport.id == report_id
            ).first()
            if report:
                report.status = ReportStatus.failed
                report.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()


def persist_results(report_id: str) -> dict:
    """Bulk-insert ComplianceResult rows and update summary counts."""
    db = SessionLocal()
    try:
        report = db.query(ComplianceReport).filter(
            ComplianceReport.id == report_id
        ).first()
        if not report or report.error_message is None:
            return {"report_id": report_id, "status": "skipped"}

        import json
        raw = json.loads(report.error_message)
        report.error_message = None  # clear temporary storage

        results_data = raw.get("results", [])
        summary = raw.get("summary", {})

        _STATUS_MAP = {
            "pass": ResultStatus.passed,
            "fail": ResultStatus.failed,
            "warning": ResultStatus.warning,
            "not_applicable": ResultStatus.not_applicable,
        }

        rows = []
        for item in results_data:
            rows.append(
                ComplianceResult(
                    id=uuid.uuid4(),
                    report_id=report.id,
                    rule_id=item.get("ruleId", ""),
                    section=item.get("section", ""),
                    requirement=item.get("requirement", ""),
                    element_express_id=item.get("elementExpressId"),
                    element_type=item.get("elementType"),
                    element_name=item.get("elementName"),
                    status=_STATUS_MAP.get(item.get("status", ""), ResultStatus.warning),
                    actual_value=str(item["actualValue"]) if item.get("actualValue") is not None else None,
                    message=item.get("message", ""),
                )
            )

        db.bulk_save_objects(rows)

        report.total = summary.get("total", len(rows))
        report.passed = summary.get("passed", 0)
        report.failed = summary.get("failed", 0)
        report.warnings = summary.get("warnings", 0)
        report.not_applicable = summary.get("notApplicable", 0)
        report.status = ReportStatus.completed
        db.commit()

        return {
            "report_id": report_id,
            "status": "ok",
            "rows_inserted": len(rows),
            "summary": summary,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def emit_compliance_notification(report_id: str) -> dict:
    """Emit an in-app notification when a compliance report completes."""
    db = SessionLocal()
    try:
        report = db.query(ComplianceReport).filter(
            ComplianceReport.id == report_id
        ).first()
        if not report:
            return {"report_id": report_id, "status": "not_found"}

        from ...models import NotificationEvent, NotificationEventType, NotificationStatus, NotificationChannel, UserNotification
        event = NotificationEvent(
            id=uuid.uuid4(),
            event_type=NotificationEventType.processing_complete,
            payload={
                "report_id": report_id,
                "status": report.status.value,
                "total": report.total,
                "failed": report.failed,
            },
        )
        db.add(event)
        db.add(
            UserNotification(
                id=uuid.uuid4(),
                user_id=report.created_by,
                event_id=event.id,
                channel=NotificationChannel.in_app,
                status=NotificationStatus.pending,
            )
        )
        db.commit()
        return {"report_id": report_id, "status": "ok"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
