import os


def test_build_bot_core_override_volumes_binds_market_data_provider():
    # The override should be derived from the host project root (BOTS_PATH in docker-compose).
    host_root = "/Users/example/project"

    from utils.bot_core_overrides import build_bot_core_override_volumes

    volumes = build_bot_core_override_volumes(host_root)

    expected_source = os.path.abspath(
        os.path.join(host_root, "hummingbot", "hummingbot", "data_feed", "market_data_provider.py")
    )
    assert expected_source in volumes
    assert volumes[expected_source]["bind"] == "/home/hummingbot/hummingbot/data_feed/market_data_provider.py"
    assert volumes[expected_source]["mode"] == "ro"

