from __future__ import annotations

import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from dmf_promsd.main import create_app
from dmf_promsd.observer import MetadataError, OpenBaoObserver, parse_record
from dmf_promsd.settings import ObserverSettings, Settings

from .test_observer import TARGET, _ok_handler, make_observer, record

FLAG = "dmf_openbao_approle_rotation_last_failure{"
SCHEDULED_SUCCESS = 1_700_000_000
FAILURE_AT = 1_700_000_050
LATER = 1_700_000_080


def _serving(tmp_path, *, scheduled_ok=False, **latest):
    """Observer whose metadata has a failure at FAILURE_AT and the given latest run."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_handler(request)
        value = record(request.url.path.rsplit("/", 1)[-1])
        value.update({"last_failure_at": FAILURE_AT, **latest})
        if scheduled_ok:
            value.update(last_scheduled_success_at=LATER, last_successful_rotation_at=LATER)
        return httpx.Response(200, json={"data": {"data": value}})

    return make_observer(tmp_path, handler)


def _exposition(observer, client) -> str:
    try:
        observer.poll_once()
        return observer.render()[0].decode()
    finally:
        observer.stop()
        client.close()


def test_scheduled_failure_stays_after_a_later_manual_success(tmp_path):
    observer, client = _serving(
        tmp_path,
        latest_run_kind="manual",
        latest_run_outcome="success",
        latest_run_at=LATER,
        last_successful_rotation_at=LATER,
    )
    assert FLAG in _exposition(observer, client)


def test_manual_failure_after_a_scheduled_success_raises_the_conservative_flag(tmp_path):
    observer, client = _serving(
        tmp_path,
        scheduled_ok=True,
        latest_run_kind="manual",
        latest_run_outcome="failure",
        latest_run_at=LATER + 10,
        last_failure_at=LATER + 10,
    )
    exposition = _exposition(observer, client)
    assert FLAG in exposition
    assert "Conservative failure flag" in exposition


def test_scheduled_failure_tied_with_the_last_scheduled_success_is_not_suppressed(tmp_path):
    observer, client = _serving(
        tmp_path,
        latest_run_kind="scheduled",
        latest_run_outcome="failure",
        latest_run_at=SCHEDULED_SUCCESS,
        last_failure_at=SCHEDULED_SUCCESS,
    )
    assert FLAG in _exposition(observer, client)


def test_tied_scheduled_failure_parses_as_active():
    value = record()
    value.update(
        latest_run_kind="scheduled", latest_run_outcome="failure", last_failure_at=1_700_000_000
    )
    assert parse_record({"data": {"data": value}}, TARGET).scheduled_failure_active


def test_scheduled_failure_stays_after_a_later_manual_failure(tmp_path):
    observer, client = _serving(
        tmp_path,
        latest_run_kind="manual",
        latest_run_outcome="failure",
        latest_run_at=LATER,
        last_failure_at=LATER,
    )
    assert FLAG in _exposition(observer, client)


def test_scheduled_success_after_the_failure_clears_the_signal(tmp_path):
    observer, client = _serving(
        tmp_path,
        scheduled_ok=True,
        latest_run_kind="scheduled",
        latest_run_outcome="success",
        latest_run_at=LATER,
    )
    assert FLAG not in _exposition(observer, client)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(last_failure_at=value["latest_run_at"] + 100),
        lambda value: value.update(last_successful_rotation_at=value["latest_run_at"] + 100),
        lambda value: value.update(last_scheduled_success_at=value["latest_run_at"] + 100),
        lambda value: value.update(
            last_successful_rotation_at=1_699_999_000, last_scheduled_success_at=1_700_000_000
        ),
        lambda value: value.update(latest_run_at=1_700_000_100),
        lambda value: value.update(
            latest_run_at=1_700_000_100, last_successful_rotation_at=1_700_000_100
        ),
        lambda value: value.update(latest_run_kind="manual", latest_run_at=1_700_000_100),
    ],
)
def test_contradictory_chronology_is_invalid_metadata(mutate):
    value = record()
    mutate(value)
    with pytest.raises(MetadataError):
        parse_record({"data": {"data": value}}, TARGET)


def test_poll_that_turns_contradictory_reports_the_failure_signal(tmp_path):
    contradictory = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_handler(request)
        value = record(request.url.path.rsplit("/", 1)[-1])
        if contradictory:
            value["last_failure_at"] = value["latest_run_at"] + 100
        return httpx.Response(200, json={"data": {"data": value}})

    observer, client = make_observer(tmp_path, handler)
    try:
        observer.poll_once()
        contradictory = True
        observer.poll_once()
        exposition = observer.render()[0].decode()
    finally:
        observer.stop()
        client.close()

    assert f'dmf_openbao_approle_rotation_metadata_available{{target="{TARGET}"}} 0.0' in exposition
    assert f'dmf_openbao_approle_rotation_collection_error{{target="{TARGET}"}} 1.0' in exposition


def test_stalled_poll_does_not_delay_a_scrape(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_handler(request)
        entered.set()
        release.wait(10)
        return _ok_handler(request)

    observer, client = make_observer(tmp_path, handler)
    poller = threading.Thread(target=observer.poll_once, daemon=True)
    try:
        poller.start()
        assert entered.wait(5)
        done: list[bytes] = []
        scraper = threading.Thread(target=lambda: done.append(observer.render()[0]), daemon=True)
        scraper.start()
        scraper.join(2)
        scraped_while_stalled = not scraper.is_alive()
    finally:
        release.set()
        poller.join(5)
        observer.stop()
        client.close()

    assert scraped_while_stalled
    assert b"dmf_openbao_approle_rotation_collector_success 0.0" in done[0]


def test_metrics_endpoint_answers_other_requests_while_a_poll_is_stalled(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return _ok_handler(request)
        entered.set()
        release.wait(10)
        return _ok_handler(request)

    observer, client = make_observer(tmp_path, handler)
    app = create_app(
        Settings(netbox_url="https://netbox.invalid", netbox_token="t"), observer=observer
    )
    poller = threading.Thread(target=observer.poll_once, daemon=True)
    results: dict[str, int] = {}
    try:
        with TestClient(app) as web:
            poller.start()
            assert entered.wait(5)
            probes = [
                threading.Thread(
                    target=lambda p=path: results.__setitem__(p, web.get(p).status_code),
                    daemon=True,
                )
                for path in ("/metrics", "/healthz")
            ]
            for probe in probes:
                probe.start()
            for probe in probes:
                probe.join(3)
            answered_while_stalled = dict(results)
    finally:
        release.set()
        poller.join(5)
        client.close()

    assert answered_while_stalled == {"/metrics": 200, "/healthz": 200}


def test_invalid_ca_file_does_not_stop_the_app(tmp_path):
    jwt = tmp_path / "jwt"
    jwt.write_text("JWT_SENTINEL", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("not a certificate", encoding="utf-8")
    observer = OpenBaoObserver(
        ObserverSettings(
            address="https://openbao.example.invalid", jwt_file=str(jwt), ca_file=str(ca)
        )
    )
    app = create_app(
        Settings(netbox_url="https://netbox.invalid", netbox_token="t"), observer=observer
    )
    try:
        with TestClient(app) as web:
            failed = web.get("/metrics").text
            assert web.get("/healthz").status_code == 200
        assert observer._client is None
        assert f'dmf_openbao_approle_rotation_collection_error{{target="{TARGET}"}} 1.0' in failed
        observer.poll_once()
        assert observer._client is None
    finally:
        observer.stop()


def test_client_is_built_again_once_the_ca_becomes_usable(tmp_path, monkeypatch):
    built = []
    real_client = httpx.Client

    def flaky_client(*, verify, timeout):
        built.append(verify)
        if len(built) == 1:
            raise OSError("bad CA")
        return real_client(transport=httpx.MockTransport(_ok_handler))

    monkeypatch.setattr("dmf_promsd.observer.httpx.Client", flaky_client)
    jwt = tmp_path / "jwt"
    jwt.write_text("JWT_SENTINEL", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("pending", encoding="utf-8")
    observer = OpenBaoObserver(
        ObserverSettings(
            address="https://openbao.example.invalid", jwt_file=str(jwt), ca_file=str(ca)
        )
    )
    try:
        assert observer._client is None
        observer.poll_once()
        exposition = observer.render()[0].decode()
    finally:
        observer.stop()

    assert f'dmf_openbao_approle_rotation_collection_error{{target="{TARGET}"}} 0.0' in exposition


def test_missing_ca_never_falls_back_to_the_system_trust_store(tmp_path, monkeypatch):
    built = []
    real_client = httpx.Client

    def recording_client(*, verify, timeout):
        built.append(verify)
        if len(built) == 1:
            raise OSError("bad CA")
        return real_client(transport=httpx.MockTransport(_ok_handler))

    monkeypatch.setattr("dmf_promsd.observer.httpx.Client", recording_client)
    jwt = tmp_path / "jwt"
    jwt.write_text("JWT_SENTINEL", encoding="utf-8")
    ca = tmp_path / "ca.crt"
    ca.write_text("pending", encoding="utf-8")
    observer = OpenBaoObserver(
        ObserverSettings(
            address="https://openbao.example.invalid", jwt_file=str(jwt), ca_file=str(ca)
        )
    )
    error = f'dmf_openbao_approle_rotation_collection_error{{target="{TARGET}"}}'
    try:
        ca.unlink()
        observer.poll_once()
        assert observer._client is None
        assert f"{error} 1.0" in observer.render()[0].decode()
        ca.write_text("", encoding="utf-8")
        observer.poll_once()
        assert observer._client is None
        ca.write_text("restored", encoding="utf-8")
        observer.poll_once()
        exposition = observer.render()[0].decode()
    finally:
        observer.stop()

    assert built == [str(ca), str(ca)]
    assert f"{error} 0.0" in exposition
