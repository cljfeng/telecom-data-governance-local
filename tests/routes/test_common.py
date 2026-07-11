import importlib
import importlib.util
import json


def test_common_route_helpers_define_protocol_contract():
    assert importlib.util.find_spec("governance_app.routes.common") is not None
    common = importlib.import_module("governance_app.routes.common")

    status, headers, body = common.json_response({"状态": "正常"}, status=201)
    assert status == 201
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"状态": "正常"}
    assert common.json_body("[]")[1][0] == 400
    assert common.batch_id_from_payload({"batch_id": "3"}) == (3, None)
    assert common.batch_id_from_query("batch_id=4") == (4, None)
    assert common.pagination_from_query({"limit": ["999"], "offset": ["2"]}) == (500, 2)


def test_common_upload_helper_sanitizes_name_and_rejects_extension(app_config):
    assert importlib.util.find_spec("governance_app.routes.common") is not None
    common = importlib.import_module("governance_app.routes.common")

    path = common.save_uploaded_workbook(app_config, "../台账.xlsx", b"workbook")
    assert path.parent == app_config.data_dir / "uploads"
    assert path.name.endswith("-台账.xlsx")

    try:
        common.save_uploaded_workbook(app_config, "台账.txt", b"bad")
    except ValueError as exc:
        assert ".xlsx" in str(exc)
    else:
        raise AssertionError("invalid extension should be rejected")
