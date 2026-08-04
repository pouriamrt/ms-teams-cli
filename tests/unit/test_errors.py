from teams_cli.errors import ApiError, NotFound, SessionExpired, TeamsError, UserError


def test_exception_hierarchy() -> None:
    assert issubclass(SessionExpired, TeamsError)
    assert issubclass(NotFound, TeamsError)
    assert issubclass(UserError, TeamsError)
    assert issubclass(ApiError, TeamsError)


def test_exit_codes() -> None:
    assert SessionExpired("expired").exit_code == 77
    assert NotFound("nope").exit_code == 64
    assert UserError("bad").exit_code == 2
    assert ApiError("boom").exit_code == 1


def test_api_error_carries_status() -> None:
    err = ApiError("server says no", status_code=503)
    assert err.status_code == 503
    assert "503" in repr(err)
