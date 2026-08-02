from pless.composegen import build_compose
from pless.config import Config


def test_webserver_image_is_exact_version_pin() -> None:
    cfg = Config.model_validate({"paperless": {"version": "2.20.15"}})
    image = build_compose(cfg)["services"]["webserver"]["image"]
    assert image == "ghcr.io/paperless-ngx/paperless-ngx:2.20.15"
    assert ":latest" not in image
