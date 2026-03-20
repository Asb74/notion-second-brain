from app.config.config_manager import ConfigManager


def test_config_manager_creates_default_schema(tmp_path) -> None:
    manager = ConfigManager(config_path=tmp_path / "config.json")

    config = manager.load()

    assert config["user_profile"] == {
        "nombre": "",
        "email_principal": "",
        "dominio": "",
        "alias": [],
    }
    assert config["email_account"]["provider"] == "gmail"
    assert config["email_settings"]["interval"] == 60
    assert config["order_validation"]["required_fields"] == [
        "Cliente",
        "FechaSalida",
        "PuntoCarga",
        "Cantidad",
        "Mercancia",
        "Confeccion",
    ]


def test_config_manager_normalizes_profile(tmp_path) -> None:
    manager = ConfigManager(config_path=tmp_path / "config.json")
    manager.save(
        {
            "user_profile": {
                "nombre": "Ana",
                "email_principal": "ANA@Example.com ",
                "dominio": "@Example.com",
                "alias": " a@example.com, b@example.com ",
            },
            "email_settings": {"auto_check": 1, "interval": 1},
        }
    )

    loaded = manager.load()

    assert loaded["user_profile"]["email_principal"] == "ana@example.com"
    assert loaded["user_profile"]["dominio"] == "@example.com"
    assert loaded["user_profile"]["alias"] == ["a@example.com", "b@example.com"]
    assert loaded["email_settings"]["interval"] == 10


def test_config_manager_normalizes_order_validation_required_fields(tmp_path) -> None:
    manager = ConfigManager(config_path=tmp_path / "config.json")
    manager.save({"order_validation": {"required_fields": ["Cantidad", " Cliente ", "", 99]}})

    loaded = manager.load()

    assert loaded["order_validation"]["required_fields"] == ["Cantidad", "Cliente", "99"]
