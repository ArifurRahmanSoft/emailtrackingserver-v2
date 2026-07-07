"""Database-query tests for synchronization download aggregation."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import AttachmentBase, TrackingAttachment
from app.models.email_tracking import Base, EmailTracking
from app.services.database_tracking import DatabaseTrackingService


@pytest.fixture
def aggregation_data() -> tuple[
    DatabaseTrackingService,
    dict[str, datetime],
]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    AttachmentBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    service = DatabaseTrackingService(None)
    service._engine = engine
    service._session_factory = session_factory

    base_time = datetime(2026, 7, 4, 8, 0, tzinfo=timezone.utc)
    times = {
        "one_updated": base_time,
        "multi_updated": base_time + timedelta(hours=1),
        "none_updated": base_time + timedelta(hours=2),
        "one_first": base_time + timedelta(minutes=5),
        "one_last": base_time + timedelta(minutes=15),
        "multi_first_early": base_time + timedelta(minutes=2),
        "multi_first_late": base_time + timedelta(minutes=7),
        "multi_last_early": base_time + timedelta(minutes=20),
        "multi_last_late": base_time + timedelta(minutes=40),
    }

    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id="one-attachment",
                    open_count=2,
                    click_count=1,
                    updated_at=times["one_updated"],
                ),
                EmailTracking(
                    tracking_id="multiple-attachments",
                    open_count=4,
                    click_count=2,
                    updated_at=times["multi_updated"],
                ),
                EmailTracking(
                    tracking_id="no-attachments",
                    open_count=1,
                    click_count=0,
                    updated_at=times["none_updated"],
                ),
            ]
        )
        session.add_all(
            [
                TrackingAttachment(
                    id=uuid4(),
                    tracking_id="one-attachment",
                    attachment_id=uuid4(),
                    download_count=2,
                    first_download=times["one_first"],
                    last_download=times["one_last"],
                    updated_at=times["one_last"],
                ),
                TrackingAttachment(
                    id=uuid4(),
                    tracking_id="multiple-attachments",
                    attachment_id=uuid4(),
                    download_count=3,
                    first_download=times["multi_first_late"],
                    last_download=times["multi_last_early"],
                    updated_at=times["multi_last_early"],
                ),
                TrackingAttachment(
                    id=uuid4(),
                    tracking_id="multiple-attachments",
                    attachment_id=uuid4(),
                    download_count=5,
                    first_download=times["multi_first_early"],
                    last_download=times["multi_last_late"],
                    updated_at=times["multi_last_late"],
                ),
            ]
        )
        session.commit()

    return service, times


def records_by_id(service: DatabaseTrackingService) -> dict[str, dict[str, object]]:
    return {
        str(record["tracking_id"]): record
        for record in service.fetch_sync_records()
    }


def test_one_attachment_row_is_aggregated(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, _ = aggregation_data
    record = records_by_id(service)["one-attachment"]

    assert record["download_count"] == 2


def test_multiple_attachment_download_counts_are_summed(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, _ = aggregation_data
    record = records_by_id(service)["multiple-attachments"]

    assert record["download_count"] == 8


def test_no_attachment_rows_return_zero_and_nulls(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, _ = aggregation_data
    record = records_by_id(service)["no-attachments"]

    assert record["download_count"] == 0
    assert record["first_download"] is None
    assert record["last_download"] is None


def test_first_download_uses_earliest_timestamp(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, times = aggregation_data
    record = records_by_id(service)["multiple-attachments"]

    assert record["first_download"] == times["multi_first_early"].replace(tzinfo=None)


def test_last_download_uses_latest_timestamp(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, times = aggregation_data
    record = records_by_id(service)["multiple-attachments"]

    assert record["last_download"] == times["multi_last_late"].replace(tzinfo=None)


def test_updated_after_still_filters_email_tracking_updated_at(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, times = aggregation_data

    records = service.fetch_sync_records(times["one_updated"])

    assert [record["tracking_id"] for record in records] == [
        "multiple-attachments",
        "no-attachments",
    ]


def test_results_remain_sorted_by_email_tracking_updated_at(
    aggregation_data: tuple[DatabaseTrackingService, dict[str, datetime]],
) -> None:
    service, _ = aggregation_data

    records = service.fetch_sync_records()

    assert [record["tracking_id"] for record in records] == [
        "one-attachment",
        "multiple-attachments",
        "no-attachments",
    ]
